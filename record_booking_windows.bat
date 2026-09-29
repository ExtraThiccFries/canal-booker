@echo off
rem Opens a browser that records every click. Do one real booking, close the browser,
rem and send recording.py to whoever maintains portal_recipe.json.
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
.venv\Scripts\python -m playwright codegen --channel msedge -o recording.py https://booking.carleton.ca/
echo Saved recording.py
pause
