#!/usr/bin/env bash
# LocalShare launcher for macOS / Linux.
# Creates a virtual environment on first run, installs dependencies,
# starts the server and opens it in your browser.
#   ./start.sh          start (HTTPS)
#   ./start.sh --http   start over plain HTTP
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"

# Find Python 3.9+
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9 or newer is required. Get it from https://www.python.org/downloads/"
  exit 1
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "==> Creating virtual environment (first run only)…"
  if ! "$PY" -m venv "$VENV"; then
    rm -rf "$VENV"
    echo "Could not create a virtual environment."
    echo "On Debian/Ubuntu run: sudo apt install python3-venv"
    exit 1
  fi
fi

# Reinstall only when requirements.txt changes.
STAMP="$VENV/.requirements-installed"
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  echo "==> Installing dependencies…"
  "$VENV/bin/python" -m pip install --disable-pip-version-check -q --upgrade pip
  "$VENV/bin/python" -m pip install --disable-pip-version-check -q -r requirements.txt
  touch "$STAMP"
fi

exec "$VENV/bin/python" share.py --open "$@"
