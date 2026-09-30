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

Each run writes its result at the top of its page on GitHub. Exit code 1 means it was not
set up, sign-in failed, or nothing was booked, so GitHub emails you about it.
"""
import datetime as dt
import os
import sys
import time

from . import booker, storage
from .scheduler import TZ, _hm

# Race if the new day opens within this long; otherwise there is nothing to do this run.
# GitHub jobs can run for up to 6 hours, and scheduled starts can be hours late.
MAX_WAIT_MINUTES = 330


def log(msg):
    print(f"[{dt.datetime.now(TZ):%H:%M:%S}] {msg}", flush=True)


def summary(title, detail=""):
    """Show the result at the top of the run's page on GitHub (the job summary)."""
    log(f"{title} {detail}".strip())
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"## {title}\n\n{detail}\n\n_{dt.datetime.now(TZ):%a %b %d, %I:%M %p} Ottawa time_\n")


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
        summary("Nothing to do this run",
                f"The next day opens {open_at:%a %I:%M %p}, more than {MAX_WAIT_MINUTES // 60} hours away. "
                "(A run that starts after midnight, or a backup start, ends here.)")
        return 0
    day = open_at.date() + dt.timedelta(days=int(s["days_ahead"]))
    if not slot_for(s, day):
        summary(f"No slot on {day:%a %b %d}", "You have no row for that day in the team plan, so nothing to book.")
        return 0
    lead = int(s.get("race_lead_seconds", 150))
    log(f"Going for {day:%a %b %d}. Opens at {open_at:%H:%M}; getting ready {lead} s before.")
    while (open_at - dt.datetime.now(TZ)).total_seconds() > lead:
        time.sleep(min(60, (open_at - dt.datetime.now(TZ)).total_seconds() - lead))

    # Read the sheet again right before, so edits made while the job waited count.
    fresh, _, problem = settings_from_env()
    if fresh:
        s = fresh
    else:
        log(f"Could not re-read the team plan ({problem}); using the version from the start of the run.")
    slot = slot_for(s, day)
    if not slot:
        summary(f"No slot on {day:%a %b %d}", "Your row for that day was removed from the team plan.")
        return 0
    log(f"Rooms: {', '.join(s['rooms'])}. Times: {', '.join(a + '-' + b for a, b in slot['times'])}.")

    result = booker.race_booking(s, password, recipe, day, slot, open_at)
    log(result["message"])
    if result["status"] == "booked":
        summary(f"✅ Booked {day:%a %b %d}", result["message"])
        return 0
    if result["status"] == "login_failed":
        summary("❌ Sign-in failed", result["message"] + " Update the CARLETON_PASSWORD secret if your password changed.")
        return 1

    # Nothing opened up in midnight mode: keep retrying the normal way for a while.
    deadline = time.time() + int(s.get("retry_minutes", 20)) * 60
    while time.time() < deadline:
        time.sleep(int(s.get("retry_every_seconds", 30)))
        results = booker.run_bookings(s, password, recipe, [(day, slot)])
        r = results[0] if results else {"status": "error", "message": "The browser returned nothing."}
        log(r["message"])
        if r["status"] == "booked":
            summary(f"✅ Booked {day:%a %b %d}", r["message"] + " (on a retry after midnight)")
            return 0
        if r["status"] == "login_failed":
            summary("❌ Sign-in failed", r["message"])
            return 1
    summary(f"❌ Not booked: {day:%a %b %d}",
            f"{result['message']} Retried for {s.get('retry_minutes', 20)} minutes. Book by hand on the portal if you still need it.")
    return 1


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
            summary("✅ Test passed", result["message"])
            return 0
        if result["status"] != "unavailable" or tried >= 3:
            summary("❌ Test failed", result["message"])
            return 1
    summary("✅ Test passed (sign-in and form steps work)",
            "Every choice was full, or you already have bookings, on the days tried, so it could not reach the last step.")
    return 0


def main():
    try:
        sys.stdout.reconfigure(errors="replace")  # a console without emoji support won't crash
    except Exception:
        pass
    s, password, problem = settings_from_env()
    if problem:
        summary("❌ Not set up", problem)
        return 1
    storage.refresh_recipe(s)
    recipe = storage.load_recipe()
    mode = os.environ.get("MODE", "book").strip().lower()
    return test(s, password, recipe) if mode == "test" else book(s, password, recipe)


if __name__ == "__main__":
    sys.exit(main())
