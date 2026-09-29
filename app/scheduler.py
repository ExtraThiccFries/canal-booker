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
