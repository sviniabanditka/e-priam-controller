#!/usr/bin/env bash
# Launch the Cybex e-Priam TUI.
# Creates the virtualenv and installs dependencies on first run (or when
# requirements.txt changes), then starts the app. Safe to re-run anywhere.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

VENV=".venv"
STAMP="$VENV/.requirements.sha256"

note() { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

# --- pick a Python 3.9+ interpreter --------------------------------------
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3.9 python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      PY="$cand"
      break
    fi
  fi
done
[ -n "$PY" ] || die "Python 3.9+ not found. Install it from https://www.python.org/downloads/"

# --- virtualenv ----------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  note "Creating virtualenv with $($PY --version 2>&1)"
  "$PY" -m venv "$VENV" || die "Could not create venv (is python3-venv installed?)"
fi

# --- dependencies (reinstalled only when requirements.txt changes) -------
REQ_HASH="$(shasum -a 256 requirements.txt 2>/dev/null | awk '{print $1}')"
REQ_HASH="${REQ_HASH:-$(md5sum requirements.txt 2>/dev/null | awk '{print $1}')}"

if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$REQ_HASH" ]; then
  note "Installing dependencies"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet -r requirements.txt \
    || die "Dependency install failed (check your network connection)"
  printf '%s' "$REQ_HASH" > "$STAMP"
fi

# --- run -----------------------------------------------------------------
note "Starting e-Priam controller (q / Ctrl+C to quit)"
exec "$VENV/bin/python" main.py "$@"
