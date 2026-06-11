"""Flask web application for LearnUs video downloader"""
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent


def _relaunch_with_project_venv():
    """Use the project virtualenv when app.py is launched directly."""
    if __name__ != "__main__":
        return
    if os.environ.get("LEARNUS_SKIP_VENV_RELAUNCH"):
        return

    venv_python = ROOT_DIR / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    if not venv_python.exists():
        return

    try:
        current_python = Path(sys.executable).resolve()
        target_python = venv_python.resolve()
    except OSError:
        return

    if current_python != target_python:
        os.environ["LEARNUS_SKIP_VENV_RELAUNCH"] = "1"
        os.execv(str(target_python), [str(target_python), str(ROOT_DIR / "app.py"), *sys.argv[1:]])


_relaunch_with_project_venv()

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
os.environ.setdefault('PYTHONUTF8', '1')

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(errors='replace')

import json
try:
    from flask import Flask, render_template, request, jsonify, send_file
except ModuleNotFoundError as exc:
    if exc.name == "flask":
        raise SystemExit(
            "Flask is not installed. Run scripts/bootstrap.cmd on Windows "
            "or scripts/bootstrap.sh on macOS/Linux, then start with scripts/run_app."
        ) from exc
    raise
from dotenv import load_dotenv
from learnus.auth import LearnUsAuth
from learnus.scraping import LearnUsScraper, LectureInfo, CourseInfo
from datetime import datetime
from learnus.downloads import VideoDownloader
from learnus.utils import is_video_file, find_ffmpeg, sanitize_filename, normalize_semester_name
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
import pickle
from typing import Optional

# Load environment variables
load_dotenv()

# LOCAL VERSION - Always enabled
APP_MODE = 'local'
IS_LOCAL_MODE = True
IS_WEB_MODE = False

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)

# Global state
auth_session = None
courses_cache = []  # List of CourseInfo objects
lectures_cache = []  # Flattened list of all lectures
current_course_url = None
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
COURSE_CACHE_FILE = DATA_DIR / "courses_cache.pkl"
DEFAULT_DOWNLOAD_DIR = BASE_DIR / "downloads"
download_dir = DEFAULT_DOWNLOAD_DIR
download_dir.mkdir(parents=True, exist_ok=True)

# Background tasks
task_status = {}

# Hierarchy tracking file
HIERARCHY_FILE = download_dir / "CONTENTS_HIERARCHY.md"


def resolve_download_path(relative_path: str) -> Path:
    """Resolve a user-provided relative path safely within the active download directory."""
    if not relative_path:
        raise ValueError('Path is required')

    normalized = relative_path.replace('/', os.sep).replace('\\', os.sep)
    candidate = (download_dir / normalized).resolve()
    root = download_dir.resolve()

    if candidate != root and root not in candidate.parents:
        raise ValueError('Invalid path')

    return candidate


def get_request_data() -> dict:
    """Return JSON request data, defaulting to an empty object for malformed requests."""
    return request.get_json(silent=True) or {}


def resolve_transcript_source(transcript_path: Optional[Path]) -> Optional[Path]:
    """Return the preferred transcript source for analysis, falling back to text when JSON is absent."""
    if not transcript_path or not transcript_path.exists():
        return None

    transcript_json_path = transcript_path.with_suffix('.json')
    if transcript_json_path.exists():
        return transcript_json_path

    return transcript_path


def get_course_storage_metadata(course_id: Optional[str] = None, lecture: Optional[LectureInfo] = None) -> tuple[str, str, str]:
    """Return the canonical year, semester, and course folder name for downloads."""
    lookup_id = str(course_id or getattr(lecture, 'course_id', '') or '')
    course = next((c for c in courses_cache if str(c.course_id) == lookup_id), None)

    year = course.year if course and course.year else str(datetime.now().year)
    semester = normalize_semester_name(course.semester if course and course.semester else "Unknown")

    if course and course.course_name:
        course_name = course.course_name
    elif lecture and lecture.course_name:
        course_name = lecture.course_name
    elif lookup_id:
        course_name = f"Course_{lookup_id}"
    else:
        course_name = "Unknown"

    return year, semester, course_name


def get_course_week_dir(year: str, semester: str, course_name: str, week: str) -> Path:
    """Return the canonical download directory for a course week."""
    return (
        download_dir
        / (sanitize_filename(year) if year else "Unknown")
        / (sanitize_filename(semester) if semester else "Unknown")
        / sanitize_filename(course_name)
        / sanitize_filename(week)
    )


def unique_download_path(path: Path, identity: str, used_paths: dict[Path, str]) -> Path:
    """Return a stable path, suffixing only when the same name maps to different content."""
    path = path.with_name(sanitize_filename(path.name))
    key = path.resolve()
    identity = identity or path.name

    if key not in used_paths:
        used_paths[key] = identity
        return path
    if used_paths[key] == identity:
        return path

    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        resolved = candidate.resolve()
        if resolved not in used_paths:
            used_paths[resolved] = identity
            return candidate
        counter += 1


def write_text_file(path: Path, text: str) -> None:
    """Write a UTF-8 text artifact inside the download tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def download_file_item(
    scraper: LearnUsScraper,
    file_item: dict,
    save_dir: Path,
    used_paths: dict[Path, str],
    messages: list[str],
) -> bool:
    """Download one resolved LearnUs file item into save_dir."""
    file_name = file_item.get('name') or 'file'
    file_url = file_item.get('url') or ''
    extension = (file_item.get('extension') or '').strip()
    if not extension and not Path(file_name).suffix and 'mod/ubfile' in file_url:
        extension = scraper._resolve_file_extension_from_url(file_url)
    if extension and not extension.startswith('.'):
        extension = f".{extension}"
    if extension and not Path(file_name).suffix:
        file_name = f"{file_name}{extension}"
    save_path = unique_download_path(save_dir / file_name, file_url, used_paths)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    if save_path.exists() and save_path.stat().st_size > 0:
        messages.append(f"File exists: {save_path.name}")
        return True

    messages.append(f"Downloading file: {file_name}")
    ok = scraper.download_file(file_url, str(save_path))
    if not ok:
        messages.append(f"Failed to download: {file_name}")
    return ok


def download_material_item(
    scraper: LearnUsScraper,
    material: dict,
    week_dir: Path,
    used_paths: dict[Path, str],
    messages: list[str],
) -> tuple[int, int]:
    """Download a material, expanding folders and boards when needed."""
    completed = 0
    failed = 0
    material_name = material.get('name') or 'Material'
    material_url = material.get('url') or ''
    material_type = material.get('type') or ''

    if material_type == 'folder' or 'mod/folder' in material_url:
        messages.append(f"Parsing folder: {material_name}")
        folder_data = scraper.parse_folder_page(material_url)
        folder_dir = week_dir / "Materials" / sanitize_filename(material_name)
        if folder_data.get('description'):
            write_text_file(folder_dir / "folder_description.txt", folder_data['description'])
            completed += 1
        for file_item in folder_data.get('files', []):
            if download_file_item(scraper, file_item, folder_dir, used_paths, messages):
                completed += 1
            else:
                failed += 1
        return completed, failed

    if material_type == 'board' or 'mod/ubboard' in material_url:
        messages.append(f"Parsing board: {material_name}")
        board_data = scraper.parse_board_page(material_url)
        board_dir = week_dir / "Materials" / sanitize_filename(material_name)
        if board_data.get('description'):
            write_text_file(board_dir / "board_description.txt", board_data['description'])
            completed += 1
        for file_item in board_data.get('files', []):
            if download_file_item(scraper, file_item, board_dir, used_paths, messages):
                completed += 1
            else:
                failed += 1
        return completed, failed

    save_dir = week_dir / "Materials"
    if download_file_item(scraper, material, save_dir, used_paths, messages):
        completed += 1
    else:
        failed += 1
    return completed, failed


def download_assignment_item(
    scraper: LearnUsScraper,
    assignment: dict,
    week_dir: Path,
    used_paths: dict[Path, str],
    messages: list[str],
) -> tuple[int, int]:
    """Download assignment description, instructor attachments, and submissions."""
    completed = 0
    failed = 0
    assignment_name = assignment.get('name') or 'Assignment'
    assignment_url = assignment.get('url') or ''
    assign_dir = week_dir / "Assignments" / sanitize_filename(assignment_name)

    messages.append(f"Processing assignment: {assignment_name}")
    assign_data = scraper.parse_assignment_page(assignment_url)

    if assign_data.get('description'):
        write_text_file(assign_dir / "assignment_description.txt", assign_data['description'])
        completed += 1

    for req in assign_data.get('requirements', []):
        if download_file_item(scraper, req, assign_dir / "Assignment Attachments", used_paths, messages):
            completed += 1
        else:
            failed += 1

    for sub in assign_data.get('submissions', []):
        if download_file_item(scraper, sub, assign_dir / "My Submissions", used_paths, messages):
            completed += 1
        else:
            failed += 1

    return completed, failed


# Import local processing modules (always available in local version)
from learnus.processing import VideoAnalyzer, WhisperTranscriber, get_transcription_environment, Summarizer


@app.route('/')
def index():
    """Main page"""
    return render_template('index.html', is_local_mode=IS_LOCAL_MODE, app_mode=APP_MODE)


@app.route('/api/check-env', methods=['GET'])
def check_env():
    """Check if .env credentials are available"""
    username = os.getenv('LEARNUS_USERNAME')
    password = os.getenv('LEARNUS_PASSWORD')
    ffmpeg_path = find_ffmpeg()
    whisper_language = (os.getenv('WHISPER_LANGUAGE') or 'auto').strip() or 'auto'
    whisper_model = (os.getenv('WHISPER_MODEL') or 'medium').strip() or 'medium'
    transcription_env = get_transcription_environment()
    
    has_credentials = bool(username and password)
    
    return jsonify({
        'success': True,
        'has_credentials': has_credentials,
        'username_set': bool(username),
        'password_set': bool(password),
        'ffmpeg_available': bool(ffmpeg_path),
        'ffmpeg_path': ffmpeg_path,
        'whisper_installed': transcription_env['whisper_installed'],
        'whisper_language': whisper_language,
        'whisper_model': whisper_model,
        'whisper_backend_requested': transcription_env['backend_requested'],
        'whisper_backend_active': transcription_env['backend_active'],
        'whisper_backend_reason': transcription_env['backend_reason'],
        'onnxruntime_installed': transcription_env['onnxruntime_installed'],
        'onnxruntime_providers': transcription_env['onnxruntime_providers'],
        'npu_provider_available': transcription_env['npu_provider_available'],
        'npu_provider_names': transcription_env['npu_provider_names'],
    })


@app.route('/api/login', methods=['POST'])
def login():
    """Handle login - supports both .env credentials and browser cookies"""
    global auth_session
    
    data = get_request_data()
    
    # Option 1: Use browser cookies (preferred for privacy)
    cookies = data.get('cookies')
    if cookies and isinstance(cookies, dict) and len(cookies) > 0:
        auth = LearnUsAuth()
        if auth.create_session_from_cookies(cookies):
            auth_session = auth.get_session()
            return jsonify({'success': True, 'message': '브라우저 세션으로 로그인했습니다.'})
        else:
            # Cookies failed, fall through to try credentials
            pass
    
    # Option 2: Use username/password from request or .env
    username = data.get('username')
    password = data.get('password')
    
    # Try to use .env credentials if not provided in request
    if not username:
        username = os.getenv('LEARNUS_USERNAME')
    if not password:
        password = os.getenv('LEARNUS_PASSWORD')
    
    if not username or not password:
        return jsonify({
            'success': False, 
            'message': '.env 파일에 자격 증명을 입력하거나, 브라우저에서 먼저 LearnUs에 로그인하세요.'
        }), 401
    
    auth = LearnUsAuth()
    if auth.login(username, password):
        auth_session = auth.get_session()
        return jsonify({'success': True, 'message': '.env 자격 증명으로 로그인했습니다.'})
    else:
        return jsonify({'success': False, 'message': '로그인에 실패했습니다. .env 자격 증명을 확인하세요.'}), 401



def get_current_semester():
    """
    Determine current semester based on current month.
    Jan, Feb -> Winter (21)
    Mar, Apr, May, Jun, Jul -> 1st Semester (10)
    Aug -> Summer (11)
    Sep, Oct, Nov, Dec -> 2nd Semester (20)
    """
    now = datetime.now()
    month = now.month
    year = now.year
    
    if month in [1, 2]:  # Jan, Feb
        semester = '21'  # Winter
    elif month in [3, 4, 5, 6, 7]:  # Mar-Jul
        semester = '10'  # 1st Semester
    elif month == 8:  # Aug
        semester = '11'  # Summer
    else:  # Sep, Oct, Nov, Dec
        semester = '20'  # 2nd Semester
    
    return str(year), semester


@app.route('/api/courses', methods=['GET'])
def fetch_all_courses():
    """Fetch course list and discover available semesters"""
    global courses_cache, lectures_cache, auth_session
    
    print("\n" + "="*60)
    print("FETCH_ALL_COURSES CALLED")
    print("="*60)
    
    if not auth_session:
        print("[ERROR] No auth_session found!")
        return jsonify({'success': False, 'message': '먼저 로그인하세요.'}), 401
    
    print(f"[OK] Auth session exists: {bool(auth_session)}")
    print(f"[OK] Session cookies: {list(auth_session.cookies.keys())}")
    
    # Get parameters for specific semester, or auto-detect current
    year = request.args.get('year')
    semester = request.args.get('semester')
    discover_all = request.args.get('discover') == 'true'  # Initial load
    cache_scope = {
        'discover_all': discover_all,
        'year': year,
        'semester': semester
    }
    
    print(f"Parameters: year={year}, semester={semester}, discover_all={discover_all}")

    try:
        scraper = LearnUsScraper(auth_session)
        print("[OK] Scraper created")
        
        all_courses = []
        available_semesters = []  # Track which semesters have content
        
        if discover_all:
            # Initial load: discover all semesters with content
            print("[INFO] Discovery mode: checking all recent semesters...")
            current_year_val = datetime.now().year
            
            # Check current year and previous 5 years, all semesters (expanded range)
            for check_year in range(current_year_val, current_year_val - 6, -1):
                for check_sem in ['20', '11', '10', '21']:  # 2nd, Summer, 1st, Winter
                    print(f"  Checking {check_year}/{check_sem}...")
                    courses = scraper.parse_course_list(year=str(check_year), semester=check_sem)
                    if courses:
                        print(f"  [OK] Found {len(courses)} courses in {check_year}/{check_sem}")
                        all_courses.extend(courses)
                        available_semesters.append({
                            'year': str(check_year),
                            'semester': check_sem,
                            'semester_name': get_semester_name(check_sem),
                            'course_count': len(courses)
                        })
        
        elif year and semester:
            # Specific semester requested
            print(f"Fetching specific: {year}/{semester}")
            courses = scraper.parse_course_list(year=year, semester=semester)
            all_courses.extend(courses)
            # Also add to available semesters if not already there
            sem_key = f"{year}-{semester}"
            existing_keys = [f"{s['year']}-{s['semester']}" for s in available_semesters]
            if sem_key not in existing_keys:
                available_semesters.append({
                    'year': str(year),
                    'semester': str(semester),
                    'semester_name': get_semester_name(str(semester)),
                    'course_count': len(courses)
                })
        else:
            # Default: current semester only (based on month)
            current_year, current_sem = get_current_semester()
            print(f"Fetching current semester (auto-detected): {current_year}/{current_sem}")
            courses = scraper.parse_course_list(year=current_year, semester=current_sem)
            all_courses.extend(courses)
        
        # Check cache first (1 hour cache)
        cache_file = COURSE_CACHE_FILE
        force_refresh = request.args.get('force_refresh') == 'true'
        
        if not force_refresh and cache_file.exists():
            try:
                cache_age = time.time() - cache_file.stat().st_mtime
                if cache_age < 3600:  # 1 hour
                    print(f"[INFO] Loading from cache (age: {int(cache_age/60)} minutes)")
                    with open(cache_file, 'rb') as f:
                        cached_data = pickle.load(f)
                        if cached_data.get('cache_scope') == cache_scope:
                            courses_data = cached_data['courses_data']
                            all_lectures = cached_data['lectures']
                            courses_cache = cached_data['courses_cache']
                            lectures_cache = all_lectures
                            
                            response_data = {
                                'success': True,
                                'courses': courses_data,
                                'total_courses': len(courses_data),
                                'from_cache': True
                            }
                            if discover_all:
                                response_data['available_semesters'] = cached_data.get('available_semesters', available_semesters)
                            return jsonify(response_data)
                        print("[INFO] Cache scope mismatch, refreshing...")
            except Exception as e:
                print(f"[WARN] Cache read error: {e}, refreshing...")
        
        # Parse content for all courses in parallel
        courses_data = []
        all_lectures = []
        parse_warnings = []
        
        print(f"\n[INFO] Parsing content for {len(all_courses)} courses in parallel...")

        if not all_courses:
            response_data = {
                'success': True,
                'courses': [],
                'total_courses': 0
            }
            if discover_all:
                response_data['available_semesters'] = available_semesters
            return jsonify(response_data)
        
        def parse_single_course(course):
            """Parse a single course - runs in parallel"""
            try:
                print(f"  [PARALLEL] {course.course_name}")
                
                # Parse detailed content (sections, files, assignments, professor)
                content = scraper.parse_course_content(course.course_id)
                sections_raw = content.get('sections', [])
                professor_parsed = content.get('professor')
                
                # Update professor if parsed from course page
                if professor_parsed:
                    course.professor = professor_parsed
                
                # Parse videos (lectures)
                lectures = scraper.parse_lecture_list(course.course_url)
                course.lectures = lectures
                
                return {
                    'course': course,
                    'sections_raw': sections_raw,
                    'lectures': lectures,
                    'warning': content.get('error')
                }
            except Exception as e:
                print(f"  [ERROR] Error parsing {course.course_name}: {e}")
                return {
                    'course': course,
                    'sections_raw': [],
                    'lectures': [],
                    'warning': str(e)
                }
        
        # Parallel processing with ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, min(5, len(all_courses)))) as executor:
            future_to_course = {executor.submit(parse_single_course, course): course for course in all_courses}
            
            for future in as_completed(future_to_course):
                source_course = future_to_course[future]
                try:
                    result = future.result()
                except Exception as e:
                    parse_warnings.append(f"{source_course.course_name}: {e}")
                    print(f"  [ERROR] Unhandled parser failure in {source_course.course_name}: {e}")
                    continue

                course = result['course']
                sections_raw = result['sections_raw']
                lectures = result['lectures']
                warning = result.get('warning')
                if warning:
                    parse_warnings.append(f"{course.course_name}: {warning}")
                all_lectures.extend(lectures)
                
                # Merge videos into sections
                course_sections = []
                
                # If no sections found (e.g. non-weekly format), create a default one
                if not sections_raw and lectures:
                    sections_raw = [{'title': 'General', 'materials': [], 'assignments': []}]
                
                # Helper to normalize string for matching
                def normalize(s): return ''.join(e for e in s if e.isalnum()).lower()
                
                # Create a mutable copy of lectures to track unmatched ones
                remaining_lectures = list(lectures)
                
                for section in sections_raw:
                    sec_title = section.get('title', 'General')
                    sec_materials = section.get('materials', [])
                    sec_assignments = section.get('assignments', [])
                    sec_videos = []
                    
                    found_videos = []
                    for lecture in remaining_lectures:
                        # Heuristic: Check if lecture.week is in section title
                        if lecture.week in sec_title or normalize(lecture.week) in normalize(sec_title):
                            found_videos.append(lecture)
                        # Fallback: matching "Week X" in "X주"
                        elif lecture.week.lower().replace('week', '').strip() in sec_title:
                            found_videos.append(lecture)
                    
                    for v in found_videos:
                        if v in remaining_lectures:
                            remaining_lectures.remove(v)
                            sec_videos.append({
                                'id': v.lecture_id,
                                'title': v.title,
                                'week': v.week,
                                'status': v.status,
                                'activity_url': v.activity_url,
                                'type': 'video'
                            })
                    
                    course_sections.append({
                        'title': sec_title,
                        'videos': sec_videos,
                        'materials': sec_materials,
                        'assignments': sec_assignments
                    })
                
                # Add any remaining videos to an "Other Videos" section
                if remaining_lectures:
                    course_sections.append({
                        'title': 'Other Videos',
                        'videos': [{
                            'id': v.lecture_id,
                            'title': v.title,
                            'week': v.week,
                            'status': v.status,
                            'activity_url': v.activity_url,
                            'type': 'video'
                        } for v in remaining_lectures],
                        'materials': [],
                        'assignments': []
                    })
                
                courses_data.append({
                    'course_id': course.course_id,
                    'course_name': course.course_name,
                    'year': course.year,
                    'semester': course.semester,
                    'professor': course.professor,  # Now from parsed content
                    'url': course.course_url,
                    'loaded': True,  # All courses pre-loaded
                    'lecture_count': len(lectures),
                    'sections': course_sections
                })
        
        print(f"\n[OK] Finished parsing {len(all_courses)} courses, {len(all_lectures)} total lectures\n")
            
        courses_cache = all_courses
        lectures_cache = all_lectures  # Store all parsed lectures
        
        # Save to cache
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump({
                    'courses_data': courses_data,
                    'lectures': all_lectures,
                    'courses_cache': courses_cache,
                    'available_semesters': available_semesters,
                    'cache_scope': cache_scope,
                    'timestamp': time.time()
                }, f)
            print("[OK] Cache saved")
        except Exception as e:
            print(f"[WARN] Cache save error: {e}")
        
        response_data = {
            'success': True,
            'courses': courses_data,
            'total_courses': len(courses_data)
        }

        if parse_warnings:
            response_data['warnings'] = parse_warnings
        
        # Include available semesters if discovery was done
        if discover_all:
            response_data['available_semesters'] = available_semesters
        
        return jsonify(response_data)

    except Exception as e:
        print(f"[ERROR] Error fetching courses: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Error fetching courses: {str(e)}'}), 500
        



@app.route('/api/course', methods=['POST'])
def fetch_course():
    """Fetch lectures from a course URL."""
    global lectures_cache, current_course_url, auth_session
    
    if not auth_session:
        return jsonify({'success': False, 'message': '먼저 로그인하세요.'}), 401
    
    data = get_request_data()
    course_url = data.get('course_url')
    
    if not course_url:
        return jsonify({'success': False, 'message': 'Course URL required'}), 400
    
    try:
        scraper = LearnUsScraper(auth_session)
        lectures = scraper.parse_lecture_list(course_url)
        
        lectures_cache = lectures
        current_course_url = course_url
        
        # Convert to JSON-serializable format
        lectures_data = []
        for lecture in lectures:
            lectures_data.append({
                'id': lecture.lecture_id,
                'title': lecture.title,
                'week': lecture.week,
                'status': lecture.status,
                'activity_url': lecture.activity_url,
                'course_name': lecture.course_name
            })
        
        return jsonify({
            'success': True,
            'lectures': lectures_data,
            'count': len(lectures_data)
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error fetching course: {str(e)}'}), 500


@app.route('/api/course/refresh', methods=['POST'])
def refresh_course():
    """Parse lectures and materials for a single course (on-demand loading)"""
    global auth_session, courses_cache, lectures_cache
    
    if not auth_session:
        return jsonify({'success': False, 'message': '먼저 로그인하세요.'}), 401
    
    data = get_request_data()
    course_id = data.get('course_id')
    
    if not course_id:
        return jsonify({'success': False, 'message': 'Course ID required'}), 400
    
    try:
        # Find course in cache
        course = next((c for c in courses_cache if c.course_id == course_id), None)
        if not course:
            return jsonify({'success': False, 'message': 'Course not found'}), 404
        
        scraper = LearnUsScraper(auth_session)
        
        print(f"\n{'='*60}")
        print(f"REFRESHING COURSE: {course.course_name}")
        print(f"{'='*60}")
        
        # 1. Get detailed content structure (sections, files, assignments)
        content = scraper.parse_course_content(course.course_id)
        sections_raw = content.get('sections', [])
        
        # 2. Get videos (with status, URLs etc)
        lectures = scraper.parse_lecture_list(course.course_url)
        course.lectures = lectures
        
        # Update lectures_cache
        # Remove old lectures for this course  
        lectures_cache = [l for l in lectures_cache if l.course_id != course_id]
        lectures_cache.extend(lectures)
        
        # 3. Merge videos into sections
        course_sections = []
        
        # If no sections found (e.g. non-weekly format), create a default one
        if not sections_raw and lectures:
            sections_raw = [{'title': 'General', 'materials': [], 'assignments': []}]
        
        # Helper to normalize string for matching
        def normalize(s): return ''.join(e for e in s if e.isalnum()).lower()
        
        # Create a mutable copy of lectures to track unmatched ones
        remaining_lectures = list(lectures)
        
        for section in sections_raw:
            sec_title = section.get('title', 'General')
            sec_materials = section.get('materials', [])
            sec_assignments = section.get('assignments', [])
            sec_videos = []
            
            found_videos = []
            for lecture in remaining_lectures:
                # Heuristic: Check if lecture.week is in section title
                if lecture.week in sec_title or normalize(lecture.week) in normalize(sec_title):
                    found_videos.append(lecture)
                # Fallback: matching "Week X" in "X주"
                elif lecture.week.lower().replace('week', '').strip() in sec_title:
                    found_videos.append(lecture)
            
            for v in found_videos:
                if v in remaining_lectures:
                    remaining_lectures.remove(v)
                    sec_videos.append({
                        'id': v.lecture_id,
                        'title': v.title,
                        'week': v.week,
                        'status': v.status,
                        'activity_url': v.activity_url,
                        'type': 'video'
                    })
            
            course_sections.append({
                'title': sec_title,
                'videos': sec_videos,
                'materials': sec_materials,
                'assignments': sec_assignments
            })
        
        # Add any remaining videos to an "Other Videos" section
        if remaining_lectures:
            course_sections.append({
                'title': 'Other Videos',
                'videos': [{
                    'id': v.lecture_id,
                    'title': v.title,
                    'week': v.week,
                    'status': v.status,
                    'activity_url': v.activity_url,
                    'type': 'video'
                } for v in remaining_lectures],
                'materials': [],
                'assignments': []
            })
        
        return jsonify({
            'success': True,
            'course_id': course.course_id,
            'course_name': course.course_name,
            'year': course.year,
            'semester': course.semester,
            'professor': course.professor,
            'sections': course_sections,
            'lecture_count': len(lectures),
            'url': course.course_url,
            'loaded': True
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error refreshing course: {str(e)}'}), 500


@app.route('/api/download', methods=['POST'])
def download_lectures():
    """Download selected lectures"""
    global auth_session, lectures_cache
    
    if not auth_session:
        return jsonify({'success': False, 'message': '먼저 로그인하세요.'}), 401
    
    data = get_request_data()
    lecture_ids = data.get('lecture_ids', [])
    transcribe_ids = data.get('transcribe_ids', [])
    summarize_ids = data.get('summarize_ids', [])
    summarize_audio_only = data.get('summarize_audio_only', False)  # New: summarize audio even without transcription
    analyze_video = data.get('analyze_video', False)  # New: analyze static frames with LLM
    use_multiprocessing = data.get('use_multiprocessing', False)  # New: multiprocessing toggle
    parallel_workers = data.get('parallel_workers', 3)
    
    if not lecture_ids:
        return jsonify({'success': False, 'message': '선택된 강의가 없습니다.'}), 400
    
    # Start background task
    task_id = f"download_{int(time.time())}"
    task_status[task_id] = {
        'status': 'running',
        'progress': 0,
        'total': len(lecture_ids),
        'completed': 0,
        'failed': 0,
        'messages': [],
        'paused': False,
        'stopped': False,
        'current_lecture_index': 0,
        'items': {}  # Per-item progress tracking
    }
    
    def download_task():
        try:
            task_lock = threading.Lock()
            post_processing_requested = bool(transcribe_ids or summarize_ids or summarize_audio_only or analyze_video)
            max_workers = 1
            try:
                requested_workers = int(parallel_workers)
            except (TypeError, ValueError):
                requested_workers = 3
            requested_workers = max(1, min(6, requested_workers))

            if use_multiprocessing and not post_processing_requested:
                max_workers = min(requested_workers, len(lecture_ids))
            elif use_multiprocessing and post_processing_requested:
                task_status[task_id]['messages'].append(
                    'Parallel download is disabled while transcription, summary, or analysis is enabled.'
                )

            lecture_map = {}
            for lecture_id in lecture_ids:
                lecture = next((l for l in lectures_cache if l.lecture_id == lecture_id), None)
                lecture_map[lecture_id] = lecture
                task_status[task_id]['items'][lecture_id] = {
                    'title': lecture.title if lecture else lecture_id,
                    'progress': 0,
                    'status': 'queued'
                }

            def append_message(message: str):
                with task_lock:
                    task_status[task_id]['messages'].append(message)

            def update_overall_progress():
                with task_lock:
                    items = task_status[task_id]['items'].values()
                    if items:
                        total_progress = sum(item.get('progress', 0) for item in items)
                        task_status[task_id]['progress'] = int(total_progress / len(task_status[task_id]['items']))

                    task_status[task_id]['completed'] = sum(
                        1 for item in items if item.get('status') == 'completed'
                    )
                    task_status[task_id]['failed'] = sum(
                        1 for item in items if item.get('status') == 'failed'
                    )

            def update_item(lecture_id, *, progress=None, status=None, title=None):
                with task_lock:
                    item = task_status[task_id]['items'].setdefault(lecture_id, {})
                    if title is not None:
                        item['title'] = title
                    if progress is not None:
                        item['progress'] = max(0, min(100, int(progress)))
                    if status is not None:
                        item['status'] = status
                update_overall_progress()

            def process_lecture(lecture_id, idx):
                local_scraper = LearnUsScraper(auth_session)
                local_downloader = VideoDownloader(str(download_dir))
                local_transcriber = WhisperTranscriber() if post_processing_requested else None
                local_summarizer = Summarizer() if (summarize_ids or summarize_audio_only) else None
                local_video_analyzer = VideoAnalyzer() if analyze_video else None

                if task_status[task_id].get('stopped', False):
                    update_item(lecture_id, status='cancelled')
                    return

                while max_workers == 1 and task_status[task_id].get('paused', False) and not task_status[task_id].get('stopped', False):
                    time.sleep(0.5)

                lecture = lecture_map.get(lecture_id)
                if not lecture:
                    append_message(f"Lecture {lecture_id} not found")
                    update_item(lecture_id, progress=100, status='failed')
                    return

                with task_lock:
                    task_status[task_id]['current_lecture_index'] = idx

                update_item(lecture_id, title=lecture.title, progress=5, status='preparing')
                year, semester, course_name = get_course_storage_metadata(lecture=lecture)

                output_path = local_downloader.get_output_path(
                    year, semester, course_name, lecture.week, lecture.title
                )

                if output_path.exists():
                    append_message(f"File already exists: {lecture.title}")
                    update_item(lecture_id, progress=100, status='completed')
                    return

                append_message(f"Extracting video URL for: {lecture.title}")
                video_url = local_scraper.extract_video_url(lecture)

                if not video_url:
                    append_message(f"Failed to extract video URL for: {lecture.title}")
                    update_item(lecture_id, progress=100, status='failed')
                    return

                append_message(f"Downloading: {lecture.title}")
                update_item(lecture_id, progress=10, status='downloading')

                def on_progress(download_percent):
                    mapped_progress = 10 + int(download_percent * 0.8)
                    update_item(lecture_id, progress=mapped_progress, status='downloading')

                if not local_downloader.download_video(video_url, output_path, auth_session, progress_callback=on_progress):
                    if local_downloader.last_error:
                        append_message(local_downloader.last_error)
                    append_message(f"Failed to download: {lecture.title}")
                    update_item(lecture_id, progress=100, status='failed')
                    return

                append_message(f"Downloaded: {lecture.title}")
                update_item(lecture_id, progress=90, status='downloaded')

                transcript_path = None

                if lecture_id in transcribe_ids and local_transcriber:
                    append_message(f"Transcribing: {lecture.title}")
                    update_item(lecture_id, progress=93, status='transcribing')
                    transcript_path = local_transcriber.transcribe_video(output_path)
                    if transcript_path:
                        append_message(f"Transcribed: {lecture.title}")

                if local_summarizer:
                    summary_text = None

                    if transcript_path and transcript_path.exists():
                        with open(transcript_path, 'r', encoding='utf-8') as f:
                            summary_text = f.read()
                    elif summarize_audio_only and local_transcriber:
                        append_message(f"Extracting audio for summarization: {lecture.title}")
                        update_item(lecture_id, progress=94, status='processing')
                        audio_path = local_transcriber.extract_audio(output_path)
                        if audio_path:
                            append_message(f"Transcribing audio: {lecture.title}")
                            transcript_path = local_transcriber.transcribe_audio(audio_path)
                            if transcript_path:
                                with open(transcript_path, 'r', encoding='utf-8') as f:
                                    summary_text = f.read()

                    if summary_text and (lecture_id in summarize_ids or summarize_audio_only):
                        append_message(f"Summarizing: {lecture.title}")
                        update_item(lecture_id, progress=96, status='processing')
                        summary = local_summarizer.summarize(summary_text)
                        if summary:
                            summary_path = output_path.parent / f"{output_path.stem}.summary.txt"
                            with open(summary_path, 'w', encoding='utf-8') as f:
                                f.write(summary)
                            append_message(f"Summarized: {lecture.title}")

                if analyze_video and local_video_analyzer and output_path.exists():
                    transcript_json_path = None
                    if transcript_path and transcript_path.exists():
                        transcript_json_path = resolve_transcript_source(transcript_path)

                    if not transcript_json_path or not transcript_json_path.exists():
                        if local_transcriber:
                            append_message(f"Transcribing for video analysis: {lecture.title}")
                            update_item(lecture_id, progress=97, status='processing')
                            audio_path = local_transcriber.extract_audio(output_path)
                            if audio_path:
                                transcript_path = local_transcriber.transcribe_audio(audio_path)
                                transcript_json_path = resolve_transcript_source(transcript_path)

                    if transcript_json_path and transcript_json_path.exists():
                        append_message(f"Analyzing video frames: {lecture.title}")
                        update_item(lecture_id, progress=98, status='processing')
                        try:
                            analysis_output_dir = output_path.parent / f"{output_path.stem}_analysis"
                            result = local_video_analyzer.analyze_video(
                                output_path,
                                transcript_json_path,
                                analysis_output_dir
                            )
                            change_count = result.get('total_changes', 0)
                            phrase_count = len(result.get('important_phrases', []))
                            append_message(
                                f"Detected {change_count} frame changes, extracted {phrase_count} key phrases: {lecture.title}"
                            )
                        except Exception as e:
                            append_message(f"Video analysis error: {str(e)}")
                    else:
                        append_message(f"Transcript required for video analysis: {lecture.title}")

                update_item(lecture_id, progress=100, status='completed')

            if max_workers > 1:
                append_message(f"Starting parallel download with {max_workers} workers.")
                pending_jobs = list(enumerate(lecture_ids))
                running_futures = {}

                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    while pending_jobs or running_futures:
                        if task_status[task_id].get('stopped', False):
                            append_message('Download stopped by user')
                            task_status[task_id]['status'] = 'stopped'
                            for future in list(running_futures.keys()):
                                future.cancel()
                            break

                        if task_status[task_id].get('paused', False):
                            task_status[task_id]['status'] = 'paused'
                            time.sleep(0.2)
                            continue

                        if task_status[task_id]['status'] == 'paused':
                            task_status[task_id]['status'] = 'running'

                        while pending_jobs and len(running_futures) < max_workers:
                            idx, lecture_id = pending_jobs.pop(0)
                            future = executor.submit(process_lecture, lecture_id, idx)
                            running_futures[future] = lecture_id

                        if not running_futures:
                            continue

                        done, _ = wait(running_futures.keys(), timeout=0.2, return_when=FIRST_COMPLETED)
                        for future in done:
                            lecture_id = running_futures.pop(future)
                            try:
                                future.result()
                            except Exception as e:
                                append_message(f"Error processing lecture {lecture_id}: {str(e)}")
                                update_item(lecture_id, progress=100, status='failed')
            else:
                for idx, lecture_id in enumerate(lecture_ids):
                    if task_status[task_id].get('stopped', False):
                        append_message('Download stopped by user')
                        task_status[task_id]['status'] = 'stopped'
                        break
                    process_lecture(lecture_id, idx)

            update_overall_progress()
            if task_status[task_id]['status'] != 'stopped':
                task_status[task_id]['status'] = 'completed'
                task_status[task_id]['messages'].append("All downloads completed!")
            
        except Exception as e:
            task_status[task_id]['status'] = 'error'
            task_status[task_id]['messages'].append(f"Task error: {str(e)}")
    
    thread = threading.Thread(target=download_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'task_id': task_id})


@app.route('/api/task/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """Get status of a background task"""
    if task_id not in task_status:
        return jsonify({'success': False, 'message': 'Task not found'}), 404
    
    return jsonify({
        'success': True,
        'status': task_status[task_id]
    })


@app.route('/api/task/<task_id>/pause', methods=['POST'])
def pause_task(task_id):
    """Pause a running task"""
    if task_id not in task_status:
        return jsonify({'success': False, 'message': 'Task not found'}), 404
    
    if task_status[task_id]['status'] != 'running':
        return jsonify({'success': False, 'message': 'Task is not running'}), 400
    
    task_status[task_id]['paused'] = True
    task_status[task_id]['status'] = 'paused'
    task_status[task_id]['messages'].append('사용자가 작업을 일시정지했습니다.')
    return jsonify({'success': True, 'message': '작업을 일시정지했습니다.'})


@app.route('/api/task/<task_id>/resume', methods=['POST'])
def resume_task(task_id):
    """Resume a paused task"""
    if task_id not in task_status:
        return jsonify({'success': False, 'message': 'Task not found'}), 404
    
    if task_status[task_id]['status'] not in ('running', 'paused'):
        return jsonify({'success': False, 'message': 'Task is not running'}), 400
    
    task_status[task_id]['paused'] = False
    task_status[task_id]['status'] = 'running'
    task_status[task_id]['messages'].append('사용자가 작업을 재개했습니다.')
    return jsonify({'success': True, 'message': '작업을 재개했습니다.'})


@app.route('/api/task/<task_id>/cancel', methods=['POST'])
def cancel_task(task_id):
    """Cancel a task"""
    if task_id not in task_status:
        return jsonify({'success': False, 'message': 'Task not found'}), 404
    
    task_status[task_id]['stopped'] = True
    task_status[task_id]['paused'] = False
    task_status[task_id]['status'] = 'cancelled'
    task_status[task_id]['messages'].append('사용자가 작업을 취소했습니다.')
    return jsonify({'success': True, 'message': '작업을 취소했습니다.'})


@app.route('/api/available-semesters', methods=['GET'])
def get_available_semesters():
    """Get list of all available semesters that have courses"""
    global auth_session
    
    if not auth_session:
        return jsonify({'success': False, 'message': '먼저 로그인하세요.'}), 401
    
    try:
        force_refresh = request.args.get('force_refresh') == 'true'
        if not force_refresh and COURSE_CACHE_FILE.exists():
            try:
                cache_age = time.time() - COURSE_CACHE_FILE.stat().st_mtime
                if cache_age < 3600:
                    with open(COURSE_CACHE_FILE, 'rb') as f:
                        cached_data = pickle.load(f)
                    cached_semesters = cached_data.get('available_semesters')
                    if cached_semesters:
                        cached_semesters = [
                            {
                                **semester,
                                'semester_name': semester.get('semester_name') or get_semester_name(str(semester.get('semester', '')))
                            }
                            for semester in cached_semesters
                        ]
                        return jsonify({
                            'success': True,
                            'semesters': cached_semesters,
                            'from_cache': True
                        })
            except Exception as e:
                print(f"[WARN] Semester cache read error: {e}")

        scraper = LearnUsScraper(auth_session)
        current_year_val = datetime.now().year
        available_semesters = []
        consecutive_empty_years = 0
        
        # Check current year and previous 5 years, and stop early after long empty streaks
        for check_year in range(current_year_val, current_year_val - 6, -1):
            year_found = False
            for check_sem in ['20', '11', '10', '21']:  # 2nd, Summer, 1st, Winter
                courses = scraper.parse_course_list(year=str(check_year), semester=check_sem)
                if courses:
                    year_found = True
                    available_semesters.append({
                        'year': str(check_year),
                        'semester': check_sem,
                        'semester_name': get_semester_name(check_sem),
                        'course_count': len(courses)
                    })

            if year_found:
                consecutive_empty_years = 0
            else:
                consecutive_empty_years += 1
                if consecutive_empty_years >= 2 and available_semesters:
                    break
        
        # Sort by year (desc) then semester (desc)
        available_semesters.sort(key=lambda x: (x['year'], x['semester']), reverse=True)
        
        return jsonify({
            'success': True,
            'semesters': available_semesters
        })
    except Exception as e:
        print(f"[ERROR] Error fetching available semesters: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


def get_semester_name(semester_code):
    """Convert semester code to readable name"""
    semester_names = {
        '10': '1학기',
        '20': '2학기',
        '11': '여름계절수업',
        '21': '겨울계절수업',
        '1학기': '1학기',
        '2학기': '2학기',
        'Dashboard': 'Dashboard',
    }
    return semester_names.get(semester_code, f'Semester {semester_code}')


@app.route('/api/app-info', methods=['GET'])
def get_app_info():
    """Get application information and capabilities"""
    return jsonify({
        'success': True,
        'app_mode': APP_MODE,
        'is_local_mode': IS_LOCAL_MODE,
        'is_web_mode': IS_WEB_MODE,
        'download_dir': str(download_dir.resolve()),
        'features': {
            'transcription': IS_LOCAL_MODE,
            'video_analysis': IS_LOCAL_MODE,
            'llm_api': False,  # Not used in local version
            'downloads': True,
        }
    })


@app.route('/api/download-single', methods=['POST'])
def download_single_item():
    """Download a single item (video, material, or assignment)"""
    global auth_session, lectures_cache
    
    if not auth_session:
        return jsonify({'success': False, 'message': '먼저 로그인하세요.'}), 401
    
    data = get_request_data()
    item_type = data.get('type')  # 'video', 'material', 'assignment'
    item_id = data.get('id')
    item_url = data.get('url')
    item_name = data.get('name')
    course_id = data.get('course_id')
    section_title = data.get('section_title', 'General')
    week = data.get('week', 'General')
    
    if not item_type or not item_id:
        return jsonify({'success': False, 'message': 'Item type and ID required'}), 400
    
    # Start background task
    task_id = f"download_single_{int(time.time())}"
    task_status[task_id] = {
        'status': 'running',
        'progress': 0,
        'total': 1,
        'completed': 0,
        'failed': 0,
        'messages': [],
        'paused': False,
        'stopped': False,
        'items': {item_id: {'progress': 0, 'status': 'downloading'}}
    }
    
    def download_single_task():
        try:
            scraper = LearnUsScraper(auth_session)
            
            year, semester, course_name = get_course_storage_metadata(course_id=course_id)
            
            if item_type == 'video':
                lecture = next((l for l in lectures_cache if l.lecture_id == item_id), None)
                if not lecture:
                    task_status[task_id]['status'] = 'error'
                    task_status[task_id]['failed'] = 1
                    task_status[task_id]['messages'].append('Lecture not found')
                    return

                year, semester, course_name = get_course_storage_metadata(course_id=course_id, lecture=lecture)
                
                downloader = VideoDownloader(str(download_dir))
                video_url = scraper.extract_video_url(lecture)
                if not video_url:
                    task_status[task_id]['status'] = 'error'
                    task_status[task_id]['failed'] = 1
                    task_status[task_id]['messages'].append('Failed to extract video URL')
                    return
                
                output_path = downloader.get_output_path(year, semester, course_name, lecture.week, lecture.title)
                
                # Skip if already exists in new location
                if output_path.exists():
                    task_status[task_id]['status'] = 'completed'
                    task_status[task_id]['completed'] = 1
                    task_status[task_id]['items'][item_id] = {'progress': 100, 'status': 'completed'}
                    task_status[task_id]['messages'].append(f"File already exists: {lecture.title}")
                    return
                
                task_status[task_id]['messages'].append(f"Downloading: {lecture.title}")

                def on_progress(download_percent):
                    task_status[task_id]['items'][item_id] = {
                        'progress': max(0, min(100, int(download_percent))),
                        'status': 'downloading'
                    }

                if downloader.download_video(video_url, output_path, auth_session, progress_callback=on_progress):
                    task_status[task_id]['status'] = 'completed'
                    task_status[task_id]['completed'] = 1
                    task_status[task_id]['items'][item_id] = {'progress': 100, 'status': 'completed'}
                else:
                    task_status[task_id]['status'] = 'error'
                    task_status[task_id]['failed'] = 1
                    task_status[task_id]['items'][item_id] = {'progress': 0, 'status': 'failed'}
                    if downloader.last_error:
                        task_status[task_id]['messages'].append(downloader.last_error)
                    task_status[task_id]['messages'].append(f"Failed to download: {lecture.title}")
                    
            elif item_type == 'material' or item_type == 'assignment':
                week_dir = get_course_week_dir(year, semester, course_name, week)

                if not item_url:
                    task_status[task_id]['status'] = 'error'
                    task_status[task_id]['failed'] = 1
                    task_status[task_id]['items'][item_id] = {'progress': 0, 'status': 'failed'}
                    task_status[task_id]['messages'].append('Item URL required')
                    return

                task_status[task_id]['items'][item_id] = {'progress': 10, 'status': 'preparing'}
                used_paths: dict[Path, str] = {}
                item = {
                    'name': item_name or item_id,
                    'url': item_url,
                    'type': item_type,
                }

                if item_type == 'material':
                    completed, failed = download_material_item(
                        scraper,
                        item,
                        week_dir,
                        used_paths,
                        task_status[task_id]['messages'],
                    )
                else:
                    completed, failed = download_assignment_item(
                        scraper,
                        item,
                        week_dir,
                        used_paths,
                        task_status[task_id]['messages'],
                    )

                task_status[task_id]['completed'] = completed
                task_status[task_id]['failed'] = failed

                if failed == 0:
                    task_status[task_id]['status'] = 'completed'
                    task_status[task_id]['items'][item_id] = {'progress': 100, 'status': 'completed'}
                    task_status[task_id]['messages'].append(
                        f"Completed {completed} file(s) for: {item_name or item_id}"
                    )
                else:
                    task_status[task_id]['status'] = 'error'
                    task_status[task_id]['items'][item_id] = {'progress': 100, 'status': 'failed'}
                    task_status[task_id]['messages'].append(
                        f"Completed {completed} file(s), failed {failed} file(s) for: {item_name or item_id}"
                    )
            
            # Update hierarchy file
            update_hierarchy_file()
            
        except Exception as e:
            task_status[task_id]['status'] = 'error'
            task_status[task_id]['failed'] = 1
            task_status[task_id]['messages'].append(f"Error: {str(e)}")
    
    thread = threading.Thread(target=download_single_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'task_id': task_id})


@app.route('/api/videos', methods=['GET'])
def list_videos():
    """List all downloaded video files with metadata"""
    videos = []
    video_extensions = {'.mp4', '.avi', '.mkv', '.mov', '.webm', '.flv'}
    
    for video_path in download_dir.rglob('*'):
        if video_path.is_file() and video_path.suffix.lower() in video_extensions:
            # Check for existing transcript and analysis
            transcript_path = video_path.with_suffix('.txt')
            transcript_json_path = video_path.with_suffix('.json')
            srt_path = video_path.with_suffix('.srt')
            analysis_dir = video_path.parent / f"{video_path.stem}_analysis"
            
            has_transcript = transcript_path.exists() or transcript_json_path.exists() or srt_path.exists()
            has_srt = srt_path.exists()
            has_analysis = analysis_dir.exists() and (analysis_dir / 'frame_analysis.json').exists()
            
            # Extract course info from canonical year/semester/course/week layout.
            relative_path = video_path.relative_to(download_dir)
            path_parts = relative_path.parts

            year_info = None
            semester_info = None
            week_info = None
            course_info = "Unknown"
            if len(path_parts) >= 4:
                year_info = path_parts[0]
                semester_info = path_parts[1]
                course_info = path_parts[2]
                week_info = path_parts[3] if len(path_parts) >= 5 else None
            elif len(path_parts) >= 2:
                # Legacy layout: one course folder directly under downloads.
                course_info = path_parts[0]
            
            # Use forward slashes for cross-platform compatibility
            relative_path_str = str(video_path.relative_to(download_dir)).replace('\\', '/')
            
            videos.append({
                'name': video_path.name,
                'path': relative_path_str,
                'full_path': str(video_path),
                'size': video_path.stat().st_size,
                'modified': video_path.stat().st_mtime,
                'course': course_info,
                'year': year_info,
                'semester': semester_info,
                'week': week_info,
                'has_transcript': has_transcript,
                'has_srt': has_srt,
                'has_analysis': has_analysis,
                'transcript_path': str(transcript_path.relative_to(download_dir)).replace('\\', '/') if transcript_path.exists() else None,
                'transcript_json_path': str(transcript_json_path.relative_to(download_dir)).replace('\\', '/') if transcript_json_path.exists() else None,
                'srt_path': str(srt_path.relative_to(download_dir)).replace('\\', '/') if srt_path.exists() else None
            })
    
    # Sort by modified time (newest first)
    videos.sort(key=lambda x: x['modified'], reverse=True)
    
    return jsonify({
        'success': True,
        'videos': videos,
        'count': len(videos)
    })


@app.route('/api/downloads', methods=['GET'])
def list_downloads():
    """List all downloaded content grouped by course"""
    try:
        courses_map = {} # key: course_dir_name
        
        if not download_dir.exists():
            return jsonify({'success': True, 'courses': []})
            
        # Canonical structure: year/semester/course/week/files
        year_dirs = [d for d in download_dir.iterdir() if d.is_dir() and not d.name.startswith('.') and d.name != 'CONTENTS_HIERARCHY.md']
        
        if year_dirs and all(d.name.isdigit() or d.name.replace('_', '').isdigit() for d in year_dirs[:3]):  # Likely new structure
            # New structure: year/semester/course/week/files
            for year_dir in sorted(year_dirs):
                if not year_dir.is_dir() or year_dir.name.startswith('.') or year_dir.name == 'CONTENTS_HIERARCHY.md':
                    continue
                
                year = year_dir.name
                
                for semester_dir in sorted(year_dir.iterdir()):
                    if not semester_dir.is_dir():
                        continue
                    
                    semester = semester_dir.name
                    
                    for course_dir in sorted(semester_dir.iterdir()):
                        if not course_dir.is_dir():
                            continue
                        
                        course_name = course_dir.name
                        course_key = f"{year}_{semester}_{course_name}"
                        
                        if course_key not in courses_map:
                            courses_map[course_key] = {
                                'dir_name': course_key,
                                'course_name': course_name,
                                'year': year,
                                'semester': semester,
                                'sections': {}
                            }
                        
                        # Walk through week directories
                        try:
                            week_dirs = list(course_dir.iterdir())
                        except (OSError, PermissionError) as e:
                            print(f"Warning: Could not list directories in {course_dir}: {e}")
                            continue
                        
                        for week_dir in sorted(week_dirs):
                            try:
                                if not week_dir.is_dir():
                                    continue
                                
                                week_name = week_dir.name
                                
                                # Walk through files in week directory
                                try:
                                    walk_generator = os.walk(week_dir)
                                except (OSError, PermissionError) as e:
                                    print(f"Warning: Could not walk directory {week_dir}: {e}")
                                    continue
                                
                                for root, dirs, files in walk_generator:
                                    try:
                                        root_path = Path(root)
                                        try:
                                            rel_path = root_path.relative_to(week_dir)
                                        except ValueError:
                                            # If relative path fails, skip this directory
                                            continue
                                        
                                        section_name = f"{week_name}/{str(rel_path)}" if str(rel_path) != '.' else week_name
                                        
                                        if section_name not in courses_map[course_key]['sections']:
                                            courses_map[course_key]['sections'][section_name] = []
                                        
                                        for file in files:
                                            if file.startswith('.') or file.endswith('.json'):
                                                continue
                                            
                                            try:
                                                file_path = root_path / file
                                                
                                                # Skip if file doesn't exist or can't be accessed
                                                if not file_path.exists() or not file_path.is_file():
                                                    continue
                                                
                                                # Try to get file size, skip if fails
                                                try:
                                                    file_size = file_path.stat().st_size
                                                except (OSError, PermissionError, FileNotFoundError) as e:
                                                    print(f"Warning: Could not access file {file_path}: {e}")
                                                    continue
                                                
                                                is_video = is_video_file(file)
                                                
                                                # Get relative path safely
                                                try:
                                                    rel_path = str(file_path.relative_to(download_dir)).replace('\\', '/')
                                                except ValueError:
                                                    # If relative path fails, use absolute path as fallback
                                                    rel_path = str(file_path).replace('\\', '/')
                                                
                                                file_data = {
                                                    'name': file,
                                                    'path': rel_path,
                                                    'size': file_size,
                                                    'type': 'video' if is_video else 'file'
                                                }
                                                
                                                if is_video:
                                                    transcript_path = file_path.with_suffix('.txt')
                                                    transcript_json_path = file_path.with_suffix('.json')
                                                    srt_path = file_path.with_suffix('.srt')
                                                    analysis_dir = file_path.parent / f"{file_path.stem}_analysis"
                                                    
                                                    file_data['has_transcript'] = transcript_path.exists() or transcript_json_path.exists() or srt_path.exists()
                                                    file_data['has_analysis'] = analysis_dir.exists() and (analysis_dir / 'frame_analysis.json').exists()
                                                
                                                courses_map[course_key]['sections'][section_name].append(file_data)
                                            except (OSError, PermissionError, ValueError, FileNotFoundError) as e:
                                                print(f"Warning: Skipping file {file} in {root_path}: {e}")
                                                continue
                                    except Exception as e:
                                        print(f"Warning: Error processing directory {root} in week {week_name}: {e}")
                                        continue
                            except Exception as e:
                                print(f"Warning: Error processing week directory {week_dir}: {e}")
                                continue

        # Convert map to list and sort
        result_courses = []
        for k, v in courses_map.items():
            # Convert sections dict to list
            sections_list = []
            for sec_name, sec_files in v['sections'].items():
                if sec_files: # Only include non-empty sections
                    sections_list.append({
                        'title': sec_name,
                        'files': sec_files
                    })
            
            # Sort sections? Maybe "General" first, then others alphabetically
            sections_list.sort(key=lambda x: x['title'])
            
            if sections_list:
                v['sections'] = sections_list
                result_courses.append(v)
                
        # Sort courses by year/sem desc
        result_courses.sort(key=lambda x: (x.get('year', ''), x.get('semester', '')), reverse=True)
        
        return jsonify({
            'success': True,
            'courses': result_courses
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error listing downloads: {str(e)}'}), 500


@app.route('/api/files', methods=['GET'])
def list_files():
    """List downloaded files from the downloads directory root."""
    files = []
    for file_path in download_dir.glob('*'):
        if file_path.is_file():
            files.append({
                'name': file_path.name,
                'size': file_path.stat().st_size,
                'path': str(file_path)
            })
    
    return jsonify({'success': True, 'files': files})


@app.route('/api/files/<path:file_path>', methods=['GET'])
def get_file(file_path):
    """Serve a file from downloads directory"""
    try:
        full_path = resolve_download_path(file_path)
        
        if not full_path.exists():
            return jsonify({'success': False, 'message': f'File not found: {full_path}'}), 404
        
        return send_file(full_path, as_attachment=False)
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 403
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/check-files', methods=['POST'])
def check_files():
    """Check if files exist in the downloads directory"""
    try:
        data = get_request_data()
        file_paths = data.get('file_paths', [])  # List of relative paths
        
        existing_files = {}
        
        for rel_path in file_paths:
            try:
                full_path = resolve_download_path(rel_path)
                existing_files[rel_path] = full_path.exists() and full_path.is_file()
            except ValueError:
                existing_files[rel_path] = False
        
        return jsonify({
            'success': True,
            'files': existing_files
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


def update_hierarchy_file():
    """Update the hierarchy tracking markdown file"""
    try:
        lines = ["# LearnUs Contents Hierarchy\n", 
                 f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n"]
        
        if not download_dir.exists():
            lines.append("No downloads yet.\n")
            with open(HIERARCHY_FILE, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            return
        
        # Walk through the directory structure
        for year_dir in sorted(download_dir.iterdir()):
            if not year_dir.is_dir() or year_dir.name.startswith('.') or year_dir.name == 'CONTENTS_HIERARCHY.md':
                continue
            
            lines.append(f"## {year_dir.name}\n\n")
            
            for semester_dir in sorted(year_dir.iterdir()):
                if not semester_dir.is_dir():
                    continue
                
                lines.append(f"### {semester_dir.name}\n\n")
                
                for course_dir in sorted(semester_dir.iterdir()):
                    if not course_dir.is_dir():
                        continue
                    
                    lines.append(f"#### {course_dir.name}\n\n")
                    
                    for week_dir in sorted(course_dir.iterdir()):
                        if not week_dir.is_dir():
                            continue
                        
                        lines.append(f"- **{week_dir.name}**\n")
                        
                        # List files in week directory
                        files = sorted([f for f in week_dir.iterdir() if f.is_file()])
                        if files:
                            for file in files:
                                size = file.stat().st_size
                                size_str = f"{size / 1024 / 1024:.2f} MB" if size > 1024*1024 else f"{size / 1024:.2f} KB"
                                rel_path = file.relative_to(download_dir)
                                lines.append(f"  - `{file.name}` ({size_str})\n")
                        else:
                            lines.append("  - *No files*\n")
                        lines.append("\n")
                    
                    lines.append("\n")
        
        with open(HIERARCHY_FILE, 'w', encoding='utf-8') as f:
            f.writelines(lines)
    except Exception as e:
        print(f"Error updating hierarchy file: {e}")


@app.route('/api/transcribe', methods=['POST'])
def transcribe_video():
    """Transcribe an existing video file"""
    data = get_request_data()
    video_path_str = data.get('video_path')
    
    if not video_path_str:
        return jsonify({'success': False, 'message': 'Video path required'}), 400
    
    try:
        video_path = resolve_download_path(video_path_str)
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 403

    if not video_path.exists():
        return jsonify({'success': False, 'message': f'Video file not found: {video_path}'}), 404
    
    # Start background task
    task_id = f"transcribe_{int(time.time())}"
    task_status[task_id] = {
        'status': 'running',
        'progress': 0,
        'total': 1,
        'completed': 0,
        'failed': 0,
        'messages': []
    }
    
    def transcribe_task():
        try:
            transcriber = WhisperTranscriber()
            task_status[task_id]['messages'].append(f"Starting transcription: {video_path.name}")
            task_status[task_id]['progress'] = 25
            
            transcript_path = transcriber.transcribe_video(video_path)
            
            if transcript_path:
                task_status[task_id]['progress'] = 100
                task_status[task_id]['completed'] = 1
                task_status[task_id]['messages'].append(f"Transcription completed: {transcript_path.name}")
                task_status[task_id]['status'] = 'completed'
            else:
                task_status[task_id]['failed'] = 1
                task_status[task_id]['messages'].append("Transcription failed")
                task_status[task_id]['status'] = 'error'
        except Exception as e:
            task_status[task_id]['status'] = 'error'
            task_status[task_id]['failed'] = 1
            task_status[task_id]['messages'].append(f"Error: {str(e)}")
    
    thread = threading.Thread(target=transcribe_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'task_id': task_id})


@app.route('/api/analyze', methods=['POST'])
def analyze_video():
    """Analyze an existing video file"""
    data = get_request_data()
    video_path_str = data.get('video_path')
    
    if not video_path_str:
        return jsonify({'success': False, 'message': 'Video path required'}), 400
    
    try:
        video_path = resolve_download_path(video_path_str)
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 403

    if not video_path.exists():
        return jsonify({'success': False, 'message': f'Video file not found: {video_path}'}), 404
    
    # Start background task
    task_id = f"analyze_{int(time.time())}"
    task_status[task_id] = {
        'status': 'running',
        'progress': 0,
        'total': 1,
        'completed': 0,
        'failed': 0,
        'messages': []
    }
    
    def analyze_task():
        try:
            video_analyzer = VideoAnalyzer()
            transcriber = WhisperTranscriber()
            
            task_status[task_id]['messages'].append(f"Starting video analysis: {video_path.name}")
            task_status[task_id]['progress'] = 10
            
            # Check for existing transcript
            transcript_json_path = video_path.with_suffix('.json')
            transcript_path = video_path.with_suffix('.txt')
            
            if not transcript_json_path.exists():
                if transcript_path.exists():
                    transcript_json_path = resolve_transcript_source(transcript_path)
                else:
                    # Need to transcribe first
                    task_status[task_id]['messages'].append("No transcript found. Transcribing audio...")
                    task_status[task_id]['progress'] = 20
                    audio_path = transcriber.extract_audio(video_path)
                    if audio_path:
                        task_status[task_id]['progress'] = 40
                        transcript_path = transcriber.transcribe_audio(audio_path)
                        transcript_json_path = resolve_transcript_source(transcript_path)
                        if transcript_json_path:
                            task_status[task_id]['messages'].append("Transcription completed")
            
            if transcript_json_path and transcript_json_path.exists():
                task_status[task_id]['progress'] = 50
                task_status[task_id]['messages'].append("Detecting frame changes and weighting transcript...")
                
                analysis_output_dir = video_path.parent / f"{video_path.stem}_analysis"
                result = video_analyzer.analyze_video(
                    video_path,
                    transcript_json_path,
                    analysis_output_dir
                )
                
                task_status[task_id]['progress'] = 100
                task_status[task_id]['completed'] = 1
                change_count = result.get('total_changes', 0)
                phrase_count = len(result.get('important_phrases', []))
                task_status[task_id]['messages'].append(f"Analysis completed: {change_count} frame changes, {phrase_count} key phrases extracted")
                task_status[task_id]['status'] = 'completed'
            else:
                task_status[task_id]['failed'] = 1
                task_status[task_id]['messages'].append("Failed to get transcript for analysis")
                task_status[task_id]['status'] = 'error'
        except Exception as e:
            task_status[task_id]['status'] = 'error'
            task_status[task_id]['failed'] = 1
            task_status[task_id]['messages'].append(f"Error: {str(e)}")
    
    thread = threading.Thread(target=analyze_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'task_id': task_id})


@app.route('/api/batch-transcribe', methods=['POST'])
def batch_transcribe():
    """Batch transcribe multiple video files"""
    data = get_request_data()
    video_paths = data.get('video_paths', [])
    
    if not video_paths:
        return jsonify({'success': False, 'message': 'No video paths provided'}), 400
    
    # Start background task
    task_id = f"batch_transcribe_{int(time.time())}"
    task_status[task_id] = {
        'status': 'running',
        'progress': 0,
        'total': len(video_paths),
        'completed': 0,
        'failed': 0,
        'messages': []
    }
    
    def batch_transcribe_task():
        try:
            transcriber = WhisperTranscriber()
            completed = 0
            failed = 0
            
            for idx, video_path_str in enumerate(video_paths):
                try:
                    video_path = resolve_download_path(video_path_str)
                    
                    if not video_path.exists():
                        task_status[task_id]['messages'].append(f"File not found: {video_path.name}")
                        failed += 1
                        continue
                    
                    task_status[task_id]['messages'].append(f"Transcribing ({idx+1}/{len(video_paths)}): {video_path.name}")
                    
                    transcript_path = transcriber.transcribe_video(video_path)
                    
                    if transcript_path:
                        completed += 1
                        task_status[task_id]['messages'].append(f"Completed: {video_path.name}")
                    else:
                        failed += 1
                        task_status[task_id]['messages'].append(f"Failed: {video_path.name}")
                    
                except Exception as e:
                    failed += 1
                    task_status[task_id]['messages'].append(f"Error on {video_path_str}: {str(e)}")
                
                # Update progress
                task_status[task_id]['progress'] = int((idx + 1) / len(video_paths) * 100)
                task_status[task_id]['completed'] = completed
                task_status[task_id]['failed'] = failed
            
            task_status[task_id]['status'] = 'completed'
            task_status[task_id]['messages'].append(f"Batch transcription complete: {completed} succeeded, {failed} failed")
            
        except Exception as e:
            task_status[task_id]['status'] = 'error'
            task_status[task_id]['messages'].append(f"Batch error: {str(e)}")
    
    thread = threading.Thread(target=batch_transcribe_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'task_id': task_id})


@app.route('/api/batch-analyze', methods=['POST'])
def batch_analyze():
    """Batch analyze multiple video files"""
    data = get_request_data()
    video_paths = data.get('video_paths', [])
    
    if not video_paths:
        return jsonify({'success': False, 'message': 'No video paths provided'}), 400
    
    # Start background task
    task_id = f"batch_analyze_{int(time.time())}"
    task_status[task_id] = {
        'status': 'running',
        'progress': 0,
        'total': len(video_paths),
        'completed': 0,
        'failed': 0,
        'messages': ["Starting batch analysis - Frame change detection"]
    }
    
    def batch_analyze_task():
        try:
            # Use optimized settings for faster analysis
            video_analyzer = VideoAnalyzer(
                sample_interval=0.5,  # Sample every 0.5 seconds
                change_threshold=0.85  # Frame change detection threshold
            )
            transcriber = WhisperTranscriber()
            completed = 0
            failed = 0
            
            for idx, video_path_str in enumerate(video_paths):
                try:
                    video_path = resolve_download_path(video_path_str)
                    
                    if not video_path.exists():
                        task_status[task_id]['messages'].append(f"File not found: {video_path.name}")
                        failed += 1
                        continue
                    
                    task_status[task_id]['messages'].append(f"Analyzing ({idx+1}/{len(video_paths)}): {video_path.name}")
                    
                    # Check for transcript
                    transcript_json_path = video_path.with_suffix('.json')
                    if not transcript_json_path.exists():
                        transcript_path = video_path.with_suffix('.txt')
                        if transcript_path.exists():
                            transcript_json_path = resolve_transcript_source(transcript_path)
                        else:
                            # Need to transcribe first
                            task_status[task_id]['messages'].append(f"Transcribing audio first: {video_path.name}")
                            audio_path = transcriber.extract_audio(video_path)
                            if audio_path:
                                transcript_path = transcriber.transcribe_audio(audio_path)
                                transcript_json_path = resolve_transcript_source(transcript_path)
                    
                    if transcript_json_path and transcript_json_path.exists():
                        analysis_output_dir = video_path.parent / f"{video_path.stem}_analysis"
                        result = video_analyzer.analyze_video(
                            video_path,
                            transcript_json_path,
                            analysis_output_dir
                        )
                        completed += 1
                        change_count = result.get('total_changes', 0)
                        phrase_count = len(result.get('important_phrases', []))
                        task_status[task_id]['messages'].append(f"{change_count} changes, {phrase_count} key phrases: {video_path.name}")
                    else:
                        failed += 1
                        task_status[task_id]['messages'].append(f"No transcript available: {video_path.name}")
                    
                except Exception as e:
                    failed += 1
                    task_status[task_id]['messages'].append(f"Error on {video_path_str}: {str(e)}")
                
                # Update progress
                task_status[task_id]['progress'] = int((idx + 1) / len(video_paths) * 100)
                task_status[task_id]['completed'] = completed
                task_status[task_id]['failed'] = failed
            
            task_status[task_id]['status'] = 'completed'
            task_status[task_id]['messages'].append(f"Batch analysis complete: {completed} succeeded, {failed} failed")
            
        except Exception as e:
            task_status[task_id]['status'] = 'error'
            task_status[task_id]['messages'].append(f"Batch error: {str(e)}")
    
    thread = threading.Thread(target=batch_analyze_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'task_id': task_id})


@app.route('/api/download_file/<path:filename>', methods=['GET'])
def download_file(filename):
    """Download a file"""
    try:
        file_path = resolve_download_path(filename)
        if file_path.exists() and file_path.is_file():
            return send_file(str(file_path), as_attachment=True)
        return jsonify({'success': False, 'message': 'File not found'}), 404
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 403


@app.route('/api/download_video', methods=['POST'])
def download_video():
    """Download a direct video URL into the local downloads directory."""
    global auth_session
    
    if not auth_session:
        return jsonify({'success': False, 'message': '먼저 로그인하세요.'}), 401
    
    data = get_request_data()
    video_url = data.get('video_url')
    output_path = data.get('output_path')
    is_hls = data.get('is_hls', False)
    
    if not video_url or not output_path:
        return jsonify({'success': False, 'message': 'video_url and output_path are required'}), 400
    
    try:
        from pathlib import Path
        
        # Parse output path relative to the active download root
        path_parts = [part for part in output_path.replace('\\', '/').split('/') if part]
        if not path_parts:
            return jsonify({'success': False, 'message': 'output_path must include a filename'}), 400

        filename = path_parts.pop()
        relative_dir = Path(*path_parts) if path_parts else None
        
        # Create downloader with proper directory
        downloader = VideoDownloader(str(download_dir))
        
        # Construct full path
        if relative_dir:
            full_dir = resolve_download_path(str(relative_dir))
            full_dir.mkdir(parents=True, exist_ok=True)
            output_file = full_dir / filename
        else:
            output_file = download_dir / filename

        output_file = output_file.resolve()
        if output_file.parent != download_dir.resolve() and download_dir.resolve() not in output_file.parents:
            return jsonify({'success': False, 'message': 'Invalid output_path'}), 403
        
        # Download video
        if downloader.download_video(video_url, output_file, auth_session):
            return jsonify({
                'success': True,
                'message': 'Video downloaded successfully',
                'path': str(output_file.relative_to(download_dir))
            })
        else:
            return jsonify({
                'success': False,
                'message': downloader.last_error or 'Download failed'
            }), 500
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500



@app.route('/api/download-materials', methods=['POST'])
def download_materials():
    """Download lecture materials and assignments for a course"""
    global auth_session
    
    if not auth_session:
        return jsonify({'success': False, 'message': '먼저 로그인하세요.'}), 401
        
    data = get_request_data()
    course_id = data.get('course_id')
    
    if not course_id:
        return jsonify({'success': False, 'message': 'Course ID required'}), 400
        
    # Start background task
    task_id = f"download_materials_{int(time.time())}"
    task_status[task_id] = {
        'status': 'running',
        'progress': 0,
        'total': 0,
        'completed': 0,
        'failed': 0,
        'messages': ["Fetching course content..."]
    }
    
    def download_materials_task():
        try:
            scraper = LearnUsScraper(auth_session)
            
            year, semester, course_name = get_course_storage_metadata(course_id=course_id)
            
            # Parse course content
            content_data = scraper.parse_course_content(course_id)
            
            if 'error' in content_data:
                task_status[task_id]['status'] = 'error'
                task_status[task_id]['messages'].append(f"Error: {content_data['message']}")
                return

            sections = content_data.get('sections', [])
            # Initial count - will be updated dynamically as we process folders
            total_items = sum(len(s['materials']) + len(s['assignments']) for s in sections)
            
            task_status[task_id]['total'] = total_items
            task_status[task_id]['messages'].append(f"Found {total_items} items to process (folders will be expanded)")
            
            processed_count = 0
            used_material_paths: dict[Path, str] = {}
            
            for section in sections:
                section_title = section['title'] or "General"
                section_title = "".join(c for c in section_title if c not in r'<>:"/\|?*').strip()
                
                # Download Materials
                for mat in section['materials']:
                    processed_count += 1
                    task_status[task_id]['current_item'] = mat['name']
                    task_status[task_id]['progress'] = int((processed_count / (total_items or 1)) * 100)

                    completed, failed = download_material_item(
                        scraper,
                        mat,
                        get_course_week_dir(year, semester, course_name, section_title),
                        used_material_paths,
                        task_status[task_id]['messages'],
                    )
                    task_status[task_id]['completed'] += completed
                    task_status[task_id]['failed'] += failed

                # Process Assignments
                for assign in section['assignments']:
                    processed_count += 1
                    task_status[task_id]['current_item'] = assign['name']
                    task_status[task_id]['progress'] = int((processed_count / (total_items or 1)) * 100)

                    completed, failed = download_assignment_item(
                        scraper,
                        assign,
                        get_course_week_dir(year, semester, course_name, section_title),
                        used_material_paths,
                        task_status[task_id]['messages'],
                    )
                    task_status[task_id]['completed'] += completed
                    task_status[task_id]['failed'] += failed

            task_status[task_id]['status'] = 'completed'
            task_status[task_id]['messages'].append("Download materials task completed")
            
            # Update hierarchy file
            update_hierarchy_file()
            
        except Exception as e:
            task_status[task_id]['status'] = 'error'
            task_status[task_id]['messages'].append(f"Task error: {str(e)}")

    thread = threading.Thread(target=download_materials_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'task_id': task_id})


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='LearnUs Contents Downloader')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=5000, help='Port to bind to (default: 5000)')
    parser.add_argument('--debug', action='store_true', help='Run in debug mode')
    parser.add_argument('--ssl-cert', help='Path to SSL certificate file (for HTTPS)')
    parser.add_argument('--ssl-key', help='Path to SSL private key file (for HTTPS)')
    parser.add_argument('--production', action='store_true', help='Run in production mode')
    
    args = parser.parse_args()
    
    # Production mode settings
    if args.production:
        app.config['DEBUG'] = False
        app.config['TESTING'] = False
    else:
        app.config['DEBUG'] = args.debug
        app.config['TESTING'] = False
    
    # SSL/HTTPS support
    ssl_context = None
    if args.ssl_cert and args.ssl_key:
        try:
            # Verify files exist
            if not os.path.exists(args.ssl_cert):
                raise FileNotFoundError(f"SSL certificate not found: {args.ssl_cert}")
            if not os.path.exists(args.ssl_key):
                raise FileNotFoundError(f"SSL key not found: {args.ssl_key}")
            ssl_context = (args.ssl_cert, args.ssl_key)
            print(f"[OK] SSL certificates loaded: {args.ssl_cert}, {args.ssl_key}")
        except Exception as e:
            print(f"[WARN] SSL certificate error: {e}")
            print("[WARN] Falling back to HTTP")
            ssl_context = None
    
    protocol = 'https' if ssl_context else 'http'
    print(f"[INFO] Starting server on {protocol}://{args.host}:{args.port}")
    if args.production:
        print("[INFO] Production mode enabled")
        print("[INFO] For better performance, use run_production.py with --use-gunicorn")
    else:
        print("[WARN] Development mode - use --production for production")
    
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug and not args.production,
        ssl_context=ssl_context,
        threaded=True
    )
