"""
Utility functions for file operations and path management
"""
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional


SEMESTER_CODE_NAMES = {
    "10": "1학기",
    "11": "여름학기",
    "20": "2학기",
    "21": "겨울학기",
}


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to remove invalid characters for Windows/Linux/Mac"""
    invalid_chars = r'[<>:"/\\|?*\x00-\x1f]'
    filename = re.sub(invalid_chars, '_', filename)
    filename = filename.rstrip('. ')
    filename = filename.lstrip()
    filename = re.sub(r'_+', '_', filename)
    
    reserved_names = ['CON', 'PRN', 'AUX', 'NUL'] + \
                    [f'COM{i}' for i in range(1, 10)] + \
                    [f'LPT{i}' for i in range(1, 10)]
    if filename.upper() in reserved_names:
        filename = f'_{filename}'
    
    if len(filename) > 200:
        filename = filename[:200]
    
    if not filename:
        filename = 'file'
    
    return filename


def normalize_semester_name(semester: str) -> str:
    """Return the canonical semester folder name used for downloads."""
    semester_text = str(semester or "").strip()
    if not semester_text:
        return "Unknown"
    return SEMESTER_CODE_NAMES.get(semester_text, semester_text)


def is_video_file(filename: str) -> bool:
    """Check if file is a video based on extension"""
    video_extensions = {'.mp4', '.avi', '.mkv', '.mov', '.webm', '.flv', '.m4v'}
    return Path(filename).suffix.lower() in video_extensions


def find_ffmpeg() -> Optional[str]:
    """Return the ffmpeg executable path if available."""
    candidates = []

    env_ffmpeg = os.getenv('FFMPEG_PATH')
    if env_ffmpeg:
        candidates.append(env_ffmpeg)

    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        candidates.append(system_ffmpeg)

    try:
        import imageio_ffmpeg

        bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled_ffmpeg:
            candidates.append(bundled_ffmpeg)
    except Exception:
        pass

    for candidate in candidates:
        try:
            subprocess.run([candidate, '-version'], capture_output=True, check=True)
            return candidate
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            continue

    return None

