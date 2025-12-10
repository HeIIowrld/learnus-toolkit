"""
Utility functions for file operations and path management
"""
import os
import re
import shutil
from pathlib import Path
from typing import Optional, Tuple


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


def parse_old_directory_name(dir_name: str) -> Optional[Tuple[str, str, str]]:
    """
    Parse old directory format: "2023-1학기-Course_ 조직행동론"
    Returns: (year, semester, course_name) or None if can't parse
    """
    # Pattern 1: Year-Semester학기-Course_ CourseName
    pattern1 = r'^(\d{4})[-_]?(\d)학기[-_]?Course[-_](.+)$'
    match = re.match(pattern1, dir_name)
    if match:
        return (match.group(1), match.group(2), match.group(3).strip())
    
    # Pattern 2: Year-Semester-CourseName or Year_Semester_CourseName
    pattern2 = r'^(\d{4})[-_](\d)[-_](.+)$'
    match = re.match(pattern2, dir_name)
    if match:
        return (match.group(1), match.group(2), match.group(3).strip())
    
    # Pattern 3: Just Year-Semester (fallback)
    pattern3 = r'^(\d{4})[-_](\d)'
    match = re.match(pattern3, dir_name)
    if match:
        course_name = re.sub(r'^\d{4}[-_]\d[-_]?', '', dir_name).strip()
        if not course_name or course_name.startswith('학기'):
            course_name = dir_name
        return (match.group(1), match.group(2), course_name)
    
    return None


def relocate_file_to_new_structure(
    old_file_path: Path,
    download_dir: Path,
    year: str,
    semester: str,
    course_name: str,
    week: str
) -> Optional[Path]:
    """
    Relocate a file from old structure to new structure.
    Returns new path if relocation successful, None otherwise.
    """
    try:
        # Sanitize components
        year_clean = sanitize_filename(year)
        semester_clean = sanitize_filename(semester)
        course_clean = sanitize_filename(course_name)
        week_clean = sanitize_filename(week)
        
        # Create new directory structure
        new_week_dir = download_dir / year_clean / semester_clean / course_clean / week_clean
        new_week_dir.mkdir(parents=True, exist_ok=True)
        
        # New file path
        new_file_path = new_week_dir / old_file_path.name
        
        # Check if already exists in new location
        if new_file_path.exists():
            # Compare sizes - if same, remove old one
            if old_file_path.exists() and old_file_path.stat().st_size == new_file_path.stat().st_size:
                old_file_path.unlink()
                return new_file_path
            # Different or old doesn't exist - rename new file
            stem = new_file_path.stem
            suffix = new_file_path.suffix
            counter = 1
            while new_file_path.exists():
                new_file_path = new_week_dir / f"{stem}_{counter}{suffix}"
                counter += 1
        
        # Move file if it exists
        if old_file_path.exists():
            shutil.move(str(old_file_path), str(new_file_path))
            return new_file_path
        
        return None
    except Exception as e:
        print(f"Warning: Could not relocate file {old_file_path}: {e}")
        return None


def find_file_in_old_structure(
    download_dir: Path,
    filename: str,
    year: str,
    semester: str,
    course_name: str
) -> Optional[Path]:
    """
    Check if file exists in old directory structure and return its path.
    Returns Path if found, None otherwise.
    """
    # Try different old directory name patterns
    patterns = [
        f"{year}-{semester}학기-Course_{course_name}",
        f"{year}_{semester}_{course_name}",
        f"{year}-{semester}-{course_name}",
        f"{year}-{semester}학기-{course_name}",
    ]
    
    for pattern in patterns:
        old_course_dir = download_dir / pattern
        if old_course_dir.exists() and old_course_dir.is_dir():
            # Search recursively
            for root, dirs, files in os.walk(old_course_dir):
                if filename in files:
                    return Path(root) / filename
    
    return None


def has_extension(filename: str) -> bool:
    """Check if filename has an extension"""
    parts = filename.split('.')
    if len(parts) < 2:
        return False
    
    last_part = parts[-1].lower()
    if len(last_part) < 1 or len(last_part) > 10:
        return False
    
    if not re.match(r'^[a-z0-9]+$', last_part):
        return False
    
    # Avoid files like "file.2023" or "file.1" (these are likely not extensions)
    if last_part.isdigit() and len(last_part) <= 3:
        return False
    
    return True


def is_video_file(filename: str) -> bool:
    """Check if file is a video based on extension"""
    video_extensions = {'.mp4', '.avi', '.mkv', '.mov', '.webm', '.flv', '.m4v'}
    return Path(filename).suffix.lower() in video_extensions

