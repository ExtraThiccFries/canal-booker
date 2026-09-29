#!/bin/bash
# Opens a browser that records every click. Do one real booking, close the browser,
# and send recording.py to whoever maintains portal_recipe.json.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
.venv/bin/python -m playwright codegen --channel chrome -o recording.py https://booking.carleton.ca/
echo "Saved recording.py"
