"""Runs the booking at each chosen time of day, catches up if the computer was off, retries failures."""
import datetime as dt
import threading
import time
from zoneinfo import ZoneInfo

from . import booker, storage

TZ = ZoneInfo("America/Toronto")


def _hm(text):
    h, m = text.split(":")
    return dt.time(int(h), int(m))


class Scheduler:
    def __init__(self):
        self.run_lock = threading.Lock()
        self.status = "Idle"
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="scheduler").start()

    def now(self):
        return dt.datetime.now(TZ)

    # ----- what to book -----

    def ready(self, s):
        return bool(s["username"] and s["rooms"] and [x for x in s["slots"] if x.get("enabled", True)])

    def pending_targets(self, s, now, extra_booked=()):
        """Every future slot inside the booking window whose day is not booked yet.
        The newest day (the one that just opened) comes first. Times that already
        started today are dropped from a slot's list of times."""
        booked = storage.booked_dates() | set(extra_booked)
        out = []
        for offset in range(int(s["days_ahead"]), -1, -1):
            day = now.date() + dt.timedelta(days=offset)
            if day.isoformat() in booked:
                continue
            for slot in s["slots"]:
                if not slot.get("enabled", True) or storage.DAYS[day.weekday()] != slot["day"]:
                    continue
                times = [t for t in storage.slot_times(slot)
                         if dt.datetime.combine(day, _hm(t[0]), TZ) > now]
                if times:
                    out.append((day, dict(slot, times=times)))
        return out

    def upcoming(self, s):
        """Next occurrences of each slot, with their status, for the page."""
        now = self.now()
        latest = {}
        for e in storage.load_log():
            if e.get("date") and (e.get("status") == "booked" or latest.get(e["date"], {}).get("status") != "booked"):
                latest[e["date"]] = e
        rows = []
        for offset in range(0, int(s["days_ahead"]) + 1):
            day = now.date() + dt.timedelta(days=offset)
            for slot in s["slots"]:
                if slot.get("enabled", True) and storage.DAYS[day.weekday()] == slot["day"]:
                    e = latest.get(day.isoformat())
                    start, end = slot["start"], slot["end"]
                    booked_time = e and e.get("status") == "booked" and (e.get("time") or (e.get("slot") or "").split(" ")[-1])
                    if booked_time and "-" in booked_time:
                        start, end = booked_time.split("-")
                    rows.append({"date": day.isoformat(), "day": slot["day"], "start": start,
                                 "end": end, "status": e["status"] if e else "waiting",
                                 "room": e.get("room") if e else None})
        return rows

    def skip_passed_new_times(self, before, after):
        """A newly added run time that already passed today starts tomorrow, instead of
        counting as a missed run and booking right away."""
        now = self.now()
        today = now.date().isoformat()
        new = [t for t in after if t not in before and now >= dt.datetime.combine(now.date(), _hm(t), TZ)]
        if not new:
            return
        state = storage.load_state()
        done = state.get("runs_done", {})
        done_today = done.get("times", []) if done.get("date") == today else []
        state["runs_done"] = {"date": today, "times": sorted(set(done_today) | set(new))}
        storage.save_state(state)

    def _runs_due(self, s, now):
        """Run times today that have passed but have not run yet."""
        state = storage.load_state()
        done = state.get("runs_done", {})
        done_today = done.get("times", []) if done.get("date") == now.date().isoformat() else []
        return [t for t in storage.run_times(s)
                if t not in done_today and now >= dt.datetime.combine(now.date(), _hm(t), TZ)]

    def next_run(self, s):
        if s["paused"]:
            return None
        now = self.now()
        state = storage.load_state()
        done = state.get("runs_done", {})
        done_today = done.get("times", []) if done.get("date") == now.date().isoformat() else []
        times = storage.run_times(s)
        for t in times:
            at = dt.datetime.combine(now.date(), _hm(t), TZ)
            if t not in done_today and at > now:
                return at.isoformat(timespec="minutes")
        if times:
            at = dt.datetime.combine(now.date() + dt.timedelta(days=1), _hm(times[0]), TZ)
            return at.isoformat(timespec="minutes")
        return None

    # ----- the loop -----

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                storage.add_log({"status": "error", "message": f"Scheduler problem: {e}"})
            self._stop.wait(20)

    def _tick(self):
        s = storage.load_settings()
        if s["paused"] or not self.ready(s) or not storage.get_password(s["username"]):
            return
        now = self.now()
        if self.run_lock.locked():  # a run is going; anything due starts on a later tick
            return
        if s.get("race"):
            open_at = self.next_open(s, now)
            state = storage.load_state()
            if (open_at - now).total_seconds() <= int(s["race_lead_seconds"]) and state.get("race_done") != open_at.isoformat():
                state["race_done"] = open_at.isoformat()
                storage.save_state(state)
                self.race(open_at)
                return
        due = self._runs_due(s, now)
        if not due:
            return
        # Several missed run times (computer was asleep) collapse into one run.
        state = storage.load_state()
        done = state.get("runs_done", {})
        today = now.date().isoformat()
        done_today = done.get("times", []) if done.get("date") == today else []
        state["runs_done"] = {"date": today, "times": sorted(set(done_today) | set(due))}
        storage.save_state(state)
        self.run(dry_run=False)

    def run(self, dry_run=False):
        if not self.run_lock.acquire(blocking=False):
            return {"ok": False, "message": "A booking run is already going."}
        try:
            return self._run(dry_run)
        finally:
            self.status = "Idle"
            self.run_lock.release()

    # ----- midnight mode -----

    def next_open(self, s, now):
        """The next time the portal opens a new day."""
        at = dt.datetime.combine(now.date(), _hm(s.get("open_time", "00:00")), TZ)
        return at if at > now else at + dt.timedelta(days=1)

    def race_target(self, s, open_at):
        """The (day, slot) that opens at open_at, if you have a slot that day and it is not booked yet."""
        day = open_at.date() + dt.timedelta(days=int(s["days_ahead"]))
        if day.isoformat() in storage.booked_dates():
            return None
        for slot in s["slots"]:
            if slot.get("enabled", True) and storage.DAYS[day.weekday()] == slot["day"]:
                return day, dict(slot, times=storage.slot_times(slot))
        return None

    def next_race(self, s):
        if s["paused"] or not s.get("race") or not self.ready(s):
            return None
        open_at = self.next_open(s, self.now())
        target = self.race_target(s, open_at)
        return {"open_at": open_at.isoformat(timespec="seconds"), "date": target[0].isoformat()} if target else None

    def race(self, open_at):
        """Midnight mode for the day that opens at open_at, then a normal run for anything left."""
        if not self.run_lock.acquire(blocking=False):
            return
        try:
            s = storage.load_settings()
            password = storage.get_password(s["username"])
            target = self.race_target(s, open_at)
            if not target or not password:
                return
            storage.refresh_recipe(s)
            recipe = storage.load_recipe()
            day, slot = target
            self.status = f"Midnight mode: signed in and waiting for {day:%a %b %d} to open"
            try:
                result = booker.race_booking(s, password, recipe, day, slot, open_at)
            except Exception as e:
                result = {"date": day.isoformat(), "slot": storage.slot_key(slot), "race": True,
                          "status": "error", "message": f"Midnight mode problem: {str(e)[:300]}"}
            storage.add_log(result)
            if result["status"] not in ("booked", "login_failed"):
                self._run(False)  # normal retries, and any earlier days still open
        finally:
            self.status = "Idle"
            self.run_lock.release()

    def race_test(self):
        """Try midnight mode right now on your next open day, as a dry run."""
        if not self.run_lock.acquire(blocking=False):
            return
        try:
            s = storage.load_settings()
            password = storage.get_password(s["username"])
            if not self.ready(s) or not password:
                storage.add_log({"status": "error", "message": "Finish steps 1 to 3 and save your password first."})
                return
            now = self.now()
            targets = self.pending_targets(s, now)[:1] or self._any_target(s)
            if not targets:
                storage.add_log({"status": "error", "message": "No slot to test midnight mode on."})
                return
            day, slot = targets[0]
            self.status = "Testing midnight mode (dry run)"
            recipe = storage.load_recipe()
            try:
                result = booker.race_booking(s, password, recipe, day, slot, now, dry_run=True)
            except Exception as e:
                result = {"date": day.isoformat(), "slot": storage.slot_key(slot), "race": True,
                          "status": "error", "message": f"Midnight mode problem: {str(e)[:300]}"}
            storage.add_log(result)
        finally:
            self.status = "Idle"
            self.run_lock.release()

    def _run(self, dry_run):
        s = storage.load_settings()
        if not self.ready(s):
            return {"ok": False, "message": "Add your username, at least one room and one time slot first."}
        password = storage.get_password(s["username"])
        if not password:
            return {"ok": False, "message": "Save your Carleton password first."}

        storage.refresh_recipe(s)
        recipe = storage.load_recipe()
        deadline = time.time() + (0 if dry_run else int(s["retry_minutes"]) * 60)
        final, booked_now, attempt = {}, set(), 0

        while True:
            attempt += 1
            targets = self.pending_targets(s, self.now(), booked_now)
            if dry_run:
                targets = targets[:1] or self._any_target(s)
            if not targets:
                break
            self.status = f"{'Dry run' if dry_run else 'Booking'}: {len(targets)} slot(s), try {attempt}"
            try:
                results = booker.run_bookings(s, password, recipe, targets, dry_run)
            except Exception as e:
                results = [{"status": "error", "message": str(e)[:300]}]
            if not results or not results[0].get("date"):  # sign-in failed or browser problem
                results = results or [{"status": "error", "message": "The browser returned nothing."}]
                storage.add_log(results[0])
                return {"ok": False, "message": results[0]["message"]}
            for r in results:
                final[(r["date"], r["slot"])] = r
                if r["status"] == "booked":
                    booked_now.add(r["date"])
            if dry_run or all(r["status"] == "booked" for r in results):
                break
            if time.time() + int(s["retry_every_seconds"]) > deadline:
                break
            self.status = f"Waiting to retry ({len(results) - len(booked_now)} left)"
            if self._stop.wait(int(s["retry_every_seconds"])):
                break

        for r in final.values():
            storage.add_log(r)
        if not final:
            return {"ok": True, "message": "Nothing to book right now. Everything in the window is already booked."}
        good = sum(1 for r in final.values() if r["status"] in ("booked", "dry_run"))
        return {"ok": good == len(final), "message": f"Finished: {good} of {len(final)} slot(s) done. See Activity."}

    def _any_target(self, s):
        """For a dry run when everything is booked: the next occurrence of the first slot."""
        now = self.now()
        slots = [x for x in s["slots"] if x.get("enabled", True)]
        for offset in range(1, 8):
            day = now.date() + dt.timedelta(days=offset)
            for slot in slots:
                if storage.DAYS[day.weekday()] == slot["day"]:
                    return [(day, slot)]
        return []
