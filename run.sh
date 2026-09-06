#!/bin/sh
# Quick launcher: creates a venv on first run, installs deps, starts the app.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
. .venv/bin/activate
pip install -q -r requirements.txt
python3 app.py
