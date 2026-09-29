@echo off
rem Runs Canal Booker from the source code (needs Python 3.10 or newer).
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw.exe run.py
