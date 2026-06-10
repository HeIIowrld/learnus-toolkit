param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

Set-Location $Root

if (-not (Test-Path $Python)) {
    Write-Host "Creating .venv..."
    python -m venv .venv
}

if (-not $SkipInstall) {
    Write-Host "Upgrading packaging tools..."
    & $Python -m pip install --upgrade pip setuptools wheel

    Write-Host "Installing Python dependencies..."
    & $Python -m pip install -r requirements.txt
}

Write-Host "Checking runtime..."
@'
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
    raise SystemExit("FFmpeg was not detected. Re-run install, or set FFMPEG_PATH to ffmpeg.exe.")

print("Runtime check passed.")
'@ | & $Python -

Write-Host ""
Write-Host "Ready. Run the app with:"
Write-Host "  .\scripts\run_app.cmd"
