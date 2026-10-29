#!/usr/bin/env sh
# Cascade Guard: install (first run) and start. Needs Python 3.12 or newer.
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' || {
  echo "Python 3.12 or newer is needed (set PYTHON=/path/to/python3.12)."; exit 1; }
[ -x .venv/bin/python ] || { echo "Setting up for the first time..."; "$PY" -m venv .venv; }
# install only on the first run or when the tested versions change, so later starts work offline
if ! cmp -s constraints.txt .venv/installed-constraints.txt; then
  .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
  cp constraints.txt .venv/installed-constraints.txt
fi
if [ ! -f .env ]; then
  .venv/bin/cascade-guard init >/dev/null
  echo "Created .env. Put your API key in it (or set LLM_PROVIDER=fake to try it free), then run ./start.sh again."
  exit 0
fi
exec .venv/bin/cascade-guard serve "$@"
