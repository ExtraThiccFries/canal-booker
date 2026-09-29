"""Run Canal Booker once from GitHub Actions (no computer needed).

The workflow starts this well before midnight, because GitHub often starts scheduled
jobs late. It reads your slots from the team plan, signs in a couple of minutes before
the new day opens, and books it the moment it does (midnight mode), then retries for a
while if nothing was free.

Settings come from environment variables (GitHub secrets and variables):
  CARLETON_USERNAME  your MyCarletonOne username          (secret, required)
  CARLETON_PASSWORD  your password                        (secret, required)
  TEAM_PLAN_URL      the team plan sheet link             (variable, optional; default in app_defaults.json)
  ROOMS              rooms in order, like "CB 2103, CB 2302" (variable, optional; overrides the plan)
  MODE               "book" (default), "test" (dry run on the next open day right now)

Exit code 1 means something went wrong (GitHub emails you about failed runs).
"""
import datetime as dt
import os
import sys
import time

from . import booker, storage
from .scheduler import TZ, _hm

# Race if the new day opens within this long; otherwise there is nothing to do this run.
MAX_WAIT_MINUTES = 180


def log(msg):
    print(f"[{dt.datetime.now(TZ):%H:%M:%S}] {msg}", flush=True)


def settings_from_env():
    user = os.environ.get("CARLETON_USERNAME", "").strip().split("@")[0]
    password = os.environ.get("CARLETON_PASSWORD", "")
    if not user or not password:
        return None, None, "Add the CARLETON_USERNAME and CARLETON_PASSWORD secrets first (see the setup steps)."
    s = storage.load_settings()
    s["username"] = user
    plan_url = os.environ.get("TEAM_PLAN_URL", "").strip() or s.get("team_plan_url", "")
    if not plan_url:
        return None, None, "No team plan link. Set the TEAM_PLAN_URL variable."
    try:
        plan = storage.fetch_plan(plan_url)
    except Exception as e:
        return None, None, f"Could not load the team plan: {e}"
    me = next((p for p in plan.get("people", []) if p.get("username", "").lower() == user.lower()), None)
    if not me or not me.get("slots"):
        return None, None, f"'{user}' has no rows in the team plan yet. Add your rows to the sheet."
    rooms = [r.strip() for r in os.environ.get("ROOMS", "").split(",") if r.strip()]
    s["rooms"] = rooms or me.get("rooms") or plan.get("rooms") or []
    if not s["rooms"]:
        return None, None, "No rooms. Fill in the Rooms column in the sheet, or set the ROOMS variable."
    s["slots"] = [dict(x, enabled=True) for x in me["slots"]]
    return s, password, None


def slot_for(s, day):
    for slot in s["slots"]:
        if storage.DAYS[day.weekday()] == slot["day"]:
            return dict(slot, times=storage.slot_times(slot))
    return None


def book(s, password, recipe):
    now = dt.datetime.now(TZ)
    open_at = dt.datetime.combine(now.date(), _hm(s.get("open_time", "00:00")), TZ)
    if open_at <= now:
        open_at += dt.timedelta(days=1)
    wait = (open_at - now).total_seconds()
    if wait > MAX_WAIT_MINUTES * 60:
        log(f"The next day opens at {open_at:%a %H:%M}, more than {MAX_WAIT_MINUTES} minutes away. Nothing to do this run.")
        return 0
    day = open_at.date() + dt.timedelta(days=int(s["days_ahead"]))
    slot = slot_for(s, day)
    if not slot:
        log(f"No slot for {day:%a %b %d} in the team plan. Nothing to book.")
        return 0
    lead = int(s.get("race_lead_seconds", 150))
    log(f"Going for {day:%a %b %d}. Opens at {open_at:%H:%M}; getting ready {lead} s before. Rooms: {', '.join(s['rooms'])}.")
    while (open_at - dt.datetime.now(TZ)).total_seconds() > lead:
        time.sleep(min(60, (open_at - dt.datetime.now(TZ)).total_seconds() - lead))

    result = booker.race_booking(s, password, recipe, day, slot, open_at)
    log(result["message"])
    if result["status"] == "booked":
        return 0
    if result["status"] == "login_failed":
        return 1

    # Nothing opened up in midnight mode: keep retrying the normal way for a while.
    deadline = time.time() + int(s.get("retry_minutes", 20)) * 60
    while time.time() < deadline:
        time.sleep(int(s.get("retry_every_seconds", 30)))
        results = booker.run_bookings(s, password, recipe, [(day, slot)])
        r = results[0] if results else {"status": "error", "message": "The browser returned nothing."}
        log(r["message"])
        if r["status"] == "booked":
            return 0
        if r["status"] == "login_failed":
            return 1
    log("Gave up for tonight. Nothing was booked.")
    return 0 if result["status"] == "unavailable" else 1


def test(s, password, recipe):
    """Dry run of midnight mode right now, on the next day that is already open."""
    s = dict(s, race_window_seconds=8)  # the day is already open; don't wait long on a full day
    tried = 0
    for offset in range(int(s["days_ahead"]), 0, -1):
        day = dt.datetime.now(TZ).date() + dt.timedelta(days=offset)
        slot = slot_for(s, day)
        if not slot:
            continue
        tried += 1
        log(f"Test (dry run) on {day:%a %b %d}. Rooms: {', '.join(s['rooms'])}.")
        result = booker.race_booking(s, password, recipe, day, slot, dt.datetime.now(TZ), dry_run=True)
        log(result["message"])
        if result["status"] == "dry_run":
            log("Test passed. Nothing was booked.")
            return 0
        if result["status"] != "unavailable" or tried >= 3:
            return 1
    log("Everything was full (or you already have bookings) on the days tried. Sign-in and the form steps worked.")
    return 0


def main():
    if not os.environ.get("CARLETON_USERNAME") and not os.environ.get("CARLETON_PASSWORD"):
        log("Not set up in this repo (no CARLETON_USERNAME / CARLETON_PASSWORD secrets). Skipping.")
        return 0
    s, password, problem = settings_from_env()
    if problem:
        log(problem)
        return 1
    storage.refresh_recipe(s)
    recipe = storage.load_recipe()
    mode = os.environ.get("MODE", "book").strip().lower()
    return test(s, password, recipe) if mode == "test" else book(s, password, recipe)


if __name__ == "__main__":
    sys.exit(main())
