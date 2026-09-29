#!/bin/bash
# Runs Canal Booker from the source code (needs Python 3.10 or newer).
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
nohup .venv/bin/python run.py >/dev/null 2>&1 &
