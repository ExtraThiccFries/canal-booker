"""Start Canal Booker: the background booker plus the local web page."""
import os
import socket
import sys
import threading
import webbrowser

PORT = 5057
URL = f"http://127.0.0.1:{PORT}/"


def _port_busy():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def main():
    from . import storage
    # A packaged app has no console window, so send output to a log file.
    if sys.stdout is None or sys.stderr is None:
        f = open(storage.data_dir() / "app.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f

    no_browser = "--no-browser" in sys.argv
    if _port_busy():  # already running, just show the page
        if not no_browser:
            webbrowser.open(URL)
        return

    from .scheduler import Scheduler
    from .server import create_app

    sched = Scheduler()
    sched.start()
    app = create_app(sched)
    if not no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(URL)).start()
    os.environ["FLASK_RUN_FROM_CLI"] = "false"
    app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
