#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
VENV_PYTHON="$ROOT/.venv/bin/python"

cd "$ROOT"

if [ ! -x "$VENV_PYTHON" ]; then
  echo "Creating .venv..."
  "$PYTHON" -m venv .venv
fi

echo "Upgrading packaging tools..."
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel

echo "Installing Python dependencies..."
"$VENV_PYTHON" -m pip install -r requirements.txt

echo "Checking runtime..."
"$VENV_PYTHON" - <<'PY'
from pathlib import Path

required_modules = [
    "flask",
    "requests",
    "bs4",
    "dotenv",
    "whisper",
    "imageio_ffmpeg",
    "cv2",
    "numpy",
]

missing = []
for module in required_modules:
    try:
        __import__(module)
    except Exception as exc:
        missing.append(f"{module}: {exc}")

from learnus.utils import find_ffmpeg
from learnus.processing import get_transcription_environment

ffmpeg_path = find_ffmpeg()
env = get_transcription_environment()

print(f"Project: {Path.cwd()}")
print(f"FFmpeg: {ffmpeg_path or 'not found'}")
print(f"Whisper installed: {env['whisper_installed']}")
print(f"Transcription backend: {env['backend_active']} ({env['backend_reason']})")

if missing:
    print("Missing modules:")
    for item in missing:
        print(f" - {item}")
    raise SystemExit(1)

if not ffmpeg_path:
    raise SystemExit("FFmpeg was not detected. Re-run install, or set FFMPEG_PATH to ffmpeg.")

print("Runtime check passed.")
PY

echo ""
echo "Ready. Run the app with:"
echo "  ./scripts/run_app.sh"
