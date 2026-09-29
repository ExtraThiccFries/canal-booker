"""Settings, activity log, recipe and password storage.

Everything personal lives in a per-user data folder, never in the repo:
  Windows: %APPDATA%/CanalBooker
  Mac:     ~/Library/Application Support/CanalBooker
Passwords go into the operating system keychain through `keyring`.
"""
import json
import os
import ssl
import sys
import threading
import datetime as dt
import urllib.request
from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover
    keyring = None

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover
    _SSL = ssl.create_default_context()

APP_NAME = "CanalBooker"
KEYRING_SERVICE = "CanalBooker (Carleton booking portal)"
_lock = threading.RLock()

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

DEFAULT_SETTINGS = {
    "username": "",
    "rooms": [],            # ranked, first choice first
    "slots": [],            # {"day": "Mon", "start": "09:00", "end": "12:00", "enabled": true,
                            #  "alts": [{"start": "12:00", "end": "15:00"}]}  backup times, tried in order
    "book_time": "00:05",   # older single run time, used when book_times is empty
    "book_times": [],       # every time of day a booking run starts (Ottawa time)
    "days_ahead": 7,        # how far ahead the portal lets you book
    "retry_minutes": 20,    # keep retrying failed slots for this long
    "retry_every_seconds": 90,
    "event_title": "Group study",
    "attendees": 4,
    "team_plan_url": "",
    "show_browser": False,
    "paused": False,
}


def resource_path(*parts) -> Path:
    """Files shipped with the app (works from source and from a packaged build)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = Path(os.environ.get("CANALBOOKER_DATA", base / APP_NAME))
    (d / "screenshots").mkdir(parents=True, exist_ok=True)
    return d


def _read_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


# ---------- settings ----------

def load_settings() -> dict:
    with _lock:
        s = dict(DEFAULT_SETTINGS)
        s.update(_read_json(resource_path("app_defaults.json"), {}))
        s.update(_read_json(data_dir() / "settings.json", {}))
        return s


def save_settings(new: dict) -> dict:
    with _lock:
        s = load_settings()
        s.update({k: v for k, v in new.items() if k in DEFAULT_SETTINGS})
        _write_json(data_dir() / "settings.json", s)
        return s


def load_state() -> dict:
    with _lock:
        return _read_json(data_dir() / "state.json", {})


def save_state(state: dict):
    with _lock:
        _write_json(data_dir() / "state.json", state)


# ---------- activity log ----------

def load_log() -> list:
    with _lock:
        return _read_json(data_dir() / "log.json", [])


def add_log(entry: dict):
    with _lock:
        entry = {"ts": dt.datetime.now().isoformat(timespec="seconds"), **entry}
        log = load_log()
        log.append(entry)
        _write_json(data_dir() / "log.json", log[-500:])


def run_times(settings: dict) -> list:
    """The times of day a booking run starts, earliest first."""
    return sorted(settings.get("book_times") or [settings.get("book_time", "00:05")])


def slot_key(slot: dict) -> str:
    return f"{slot['day']} {slot['start']}-{slot['end']}"


def slot_times(slot: dict) -> list:
    """The main time first, then the backup times: [(start, end), ...]."""
    times = [(slot["start"], slot["end"])]
    for a in slot.get("alts") or []:
        if (a["start"], a["end"]) not in times:
            times.append((a["start"], a["end"]))
    return times


def booked_dates() -> set:
    """Days that already have a booking. The portal allows one 3 hour booking per person per day."""
    return {e.get("date") for e in load_log() if e.get("status") == "booked"}


# ---------- passwords ----------

def get_password(username: str):
    if not username or keyring is None:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, username)
    except Exception:
        return None


def set_password(username: str, password: str):
    if keyring is None:
        raise RuntimeError("Secure password storage is not available on this computer.")
    keyring.set_password(KEYRING_SERVICE, username, password)


# ---------- shared files from GitHub ----------

def to_raw_url(url: str) -> str:
    """Accept a normal GitHub file link and turn it into the raw file link."""
    url = (url or "").strip()
    if "github.com" in url and "/blob/" in url:
        url = url.replace("https://github.com/", "https://raw.githubusercontent.com/").replace("/blob/", "/", 1)
    return url


def fetch_json(url: str):
    req = urllib.request.Request(to_raw_url(url), headers={"User-Agent": "CanalBooker", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=10, context=_SSL) as r:
        return json.loads(r.read().decode("utf-8"))


def load_recipe() -> dict:
    """Use the newest recipe: a downloaded team copy if it is newer than the built-in one."""
    bundled = _read_json(resource_path("portal_recipe.json"), {})
    downloaded = _read_json(data_dir() / "portal_recipe.json", {})
    if downloaded.get("version", 0) > bundled.get("version", 0):
        return downloaded
    return bundled


def refresh_recipe(settings: dict):
    """If a team plan link is set, grab portal_recipe.json from the same folder on GitHub."""
    url = to_raw_url(settings.get("team_plan_url", ""))
    if not url:
        return
    try:
        recipe = fetch_json(url.rsplit("/", 1)[0] + "/portal_recipe.json")
        if isinstance(recipe, dict) and "book" in recipe:
            with _lock:
                _write_json(data_dir() / "portal_recipe.json", recipe)
    except Exception:
        pass
