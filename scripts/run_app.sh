#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
VENV_PYTHON="$ROOT/.venv/bin/python"

if [ ! -x "$VENV_PYTHON" ]; then
  echo ".venv was not found. Run ./scripts/bootstrap.sh first." >&2
  exit 1
fi

cd "$ROOT"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

exec "$VENV_PYTHON" app.py
