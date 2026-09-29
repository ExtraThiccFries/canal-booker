"""Start the app when the computer starts, so bookings keep happening."""
import sys
from pathlib import Path


def _command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--no-browser"]
    root = Path(__file__).resolve().parent.parent
    exe = sys.executable
    if sys.platform == "win32" and exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"
    return [exe, str(root / "run.py"), "--no-browser"]


def _win_path():
    import os
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/CanalBooker.bat"


def _mac_path():
    return Path.home() / "Library/LaunchAgents/ca.canalbooker.plist"


def is_enabled():
    if sys.platform == "win32":
        return _win_path().exists()
    if sys.platform == "darwin":
        return _mac_path().exists()
    return False


def set_enabled(on: bool):
    if sys.platform == "win32":
        p = _win_path()
        if on:
            cmd = " ".join(f'"{c}"' if " " in c else c for c in _command())
            p.write_text(f'@echo off\r\nstart "" {cmd}\r\n', encoding="utf-8")
        elif p.exists():
            p.unlink()
    elif sys.platform == "darwin":
        p = _mac_path()
        if on:
            args = "".join(f"<string>{c}</string>" for c in _command())
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>'
                '<key>Label</key><string>ca.canalbooker</string>'
                f'<key>ProgramArguments</key><array>{args}</array>'
                '<key>RunAtLoad</key><true/>'
                '</dict></plist>\n', encoding="utf-8")
        elif p.exists():
            p.unlink()
    else:
        raise RuntimeError("Start at login is only set up for Windows and Mac.")
    return is_enabled()
