"""Flask web application for LearnUs video downloader"""
import os

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
os.environ.setdefault('PYTHONUTF8', '1')

import json
from flask import Flask, render_template, request, jsonify, send_file
from pathlib import Path
from dotenv import load_dotenv
from auth_module import LearnUsAuth
from scraper import LearnUsScraper, LectureInfo, CourseInfo
from datetime import datetime
from downloader import VideoDownloader
from utils import find_file_in_old_structure, relocate_file_to_new_structure, is_video_file
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import pickle
from pathlib import Path as PathLib

# Load environment variables
load_dotenv()

# WEB VERSION - Transcription/Analysis disabled, LLM APIs available
APP_MODE = 'web'
IS_LOCAL_MODE = False
IS_WEB_MODE = True

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)

# Global state
auth_session = None
courses_cache = []  # List of CourseInfo objects
lectures_cache = []  # Flattened list of all lectures
current_course_url = None
BASE_DIR = Path(__file__).resolve().parent
download_dir = BASE_DIR / "downloads"
download_dir.mkdir(exist_ok=True)

# Background tasks
task_status = {}

# Hierarchy tracking file
HIERARCHY_FILE = download_dir / "CONTENTS_HIERARCHY.md"

# LLM API settings (for web version - future features)
llm_settings = {
    'provider': os.getenv('LLM_PROVIDER', 'openai'),  # 'openai', 'google', 'ollama'
    'openai_api_key': os.getenv('OPENAI_API_KEY', ''),
    'openai_model': os.getenv('OPENAI_MODEL', 'gpt-4'),
    'google_api_key': os.getenv('GOOGLE_API_KEY', ''),
    'google_model': os.getenv('GOOGLE_MODEL', 'gemini-pro'),
    'ollama_url': os.getenv('OLLAMA_URL', 'http://localhost:11434'),
    'ollama_model': os.getenv('OLLAMA_MODEL', 'llama2'),
}

# Local processing modules NOT available in web version
    VideoAnalyzer = None
    WhisperTranscriber = None
    Summarizer = None


@app.route('/')
def index():
    """Main page"""
    return render_template('index.html', is_local_mode=IS_LOCAL_MODE, app_mode=APP_MODE)


@app.route('/api/check-env', methods=['GET'])
def check_env():
    """Check if .env credentials are available"""
    username = os.getenv('LEARNUS_USERNAME')
    password = os.getenv('LEARNUS_PASSWORD')
    
    has_credentials = bool(username and password)
    
    return jsonify({
        'success': True,
        'has_credentials': has_credentials,
        'username_set': bool(username),
        'password_set': bool(password)
    })


@app.route('/api/login', methods=['POST'])
def login():
    """Handle login - supports both credentials and browser cookies"""
    global auth_session
    
    data = request.json or {}
    
    # Option 1: Use browser cookies if provided and valid (preferred for production)
    cookies = data.get('cookies')
    if cookies and isinstance(cookies, dict) and len(cookies) > 0:
        auth = LearnUsAuth()
        if auth.create_session_from_cookies(cookies):
            auth_session = auth.get_session()
            return jsonify({'success': True, 'message': 'Login successful using browser session'})
        # If cookies fail, fall through to try credentials
    
    # Option 2: Use username/password from request or .env (fallback)
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
            'message': 'Please provide credentials in .env file (LEARNUS_USERNAME, LEARNUS_PASSWORD) or log in to LearnUs in your browser first'
        }), 401
    
    auth = LearnUsAuth()
    if auth.login(username, password):
        auth_session = auth.get_session()
        return jsonify({'success': True, 'message': 'Login successful using .env credentials'})
    else:
        return jsonify({'success': False, 'message': 'Login failed. Please check your credentials in .env file.'}), 401



def get_current_semester():
    """
    Determine current semester based on current month.
    Jan, Feb → Winter (21)
    Mar, Apr, May, Jun, Jul → 1st Semester (10)
    Aug → Summer (11)
    Sep, Oct, Nov, Dec → 2nd Semester (20)
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
        print("❌ No auth_session found!")
        return jsonify({'success': False, 'message': 'Please login first'}), 401
    
    print(f"✓ Auth session exists: {bool(auth_session)}")
    print(f"✓ Session cookies: {list(auth_session.cookies.keys())}")
    
    # Get parameters for specific semester, or auto-detect current
    year = request.args.get('year')
    semester = request.args.get('semester')
    discover_all = request.args.get('discover') == 'true'  # Initial load
    
    print(f"Parameters: year={year}, semester={semester}, discover_all={discover_all}")

    try:
        scraper = LearnUsScraper(auth_session)
        print("✓ Scraper created")
        
        all_courses = []
        available_semesters = []  # Track which semesters have content
        
        if discover_all:
            # Initial load: discover all semesters with content
            print("🔍 Discovery mode: checking all recent semesters...")
            current_year_val = datetime.now().year
            
            # Check current year and previous 5 years, all semesters (expanded range)
            for check_year in range(current_year_val, current_year_val - 6, -1):
                for check_sem in ['20', '11', '10', '21']:  # 2nd, Summer, 1st, Winter
                    print(f"  Checking {check_year}/{check_sem}...")
                    courses = scraper.parse_course_list(year=str(check_year), semester=check_sem)
                    if courses:
                        print(f"  ✓ Found {len(courses)} courses in {check_year}/{check_sem}")
                        all_courses.extend(courses)
                        available_semesters.append({
                            'year': str(check_year),
                            'semester': check_sem,
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
                    'course_count': len(courses)
                })
        else:
            # Default: current semester only (based on month)
            current_year, current_sem = get_current_semester()
            print(f"Fetching current semester (auto-detected): {current_year}/{current_sem}")
            courses = scraper.parse_course_list(year=current_year, semester=current_sem)
            all_courses.extend(courses)
        
        # Check cache first (1 hour cache)
        cache_file = PathLib('courses_cache.pkl')
        force_refresh = request.args.get('force_refresh') == 'true'
        
        if not force_refresh and cache_file.exists():
            try:
                cache_age = time.time() - cache_file.stat().st_mtime
                if cache_age < 3600:  # 1 hour
                    print(f"📦 Loading from cache (age: {int(cache_age/60)} minutes)")
                    with open(cache_file, 'rb') as f:
                        cached_data = pickle.load(f)
                        if cached_data.get('auth_cookies') == dict(auth_session.cookies):
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
                                response_data['available_semesters'] = available_semesters
                            return jsonify(response_data)
            except Exception as e:
                print(f"⚠️ Cache read error: {e}, refreshing...")
        
        # Parse content for all courses in parallel
        courses_data = []
        all_lectures = []
        
        print(f"\n📦 Parsing content for {len(all_courses)} courses in parallel...")
        
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
                
                return course, sections_raw, lectures
            except Exception as e:
                print(f"  ❌ Error parsing {course.course_name}: {e}")
                return course, [], []
        
        # Parallel processing with ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(5, len(all_courses))) as executor:
            future_to_course = {executor.submit(parse_single_course, course): course for course in all_courses}
            
            for future in as_completed(future_to_course):
                course, sections_raw, lectures = future.result()
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
        
        print(f"\n✅ Finished parsing {len(all_courses)} courses, {len(all_lectures)} total lectures\n")
            
        courses_cache = all_courses
        lectures_cache = all_lectures  # Store all parsed lectures
        
        # Save to cache
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump({
                    'courses_data': courses_data,
                    'lectures': all_lectures,
                    'courses_cache': courses_cache,
                    'auth_cookies': dict(auth_session.cookies),
                    'timestamp': time.time()
                }, f)
            print("💾 Cache saved")
        except Exception as e:
            print(f"⚠️ Cache save error: {e}")
        
        response_data = {
            'success': True,
            'courses': courses_data,
            'total_courses': len(courses_data)
        }
        
        # Include available semesters if discovery was done
        if discover_all:
            response_data['available_semesters'] = available_semesters
        
        return jsonify(response_data)

    except Exception as e:
        return jsonify({'success': False, 'message': f'Error fetching courses: {str(e)}'}), 500
        



@app.route('/api/course', methods=['POST'])
def fetch_course():
    """Fetch lectures from a course URL (legacy endpoint)"""
    global lectures_cache, current_course_url, auth_session
    
    if not auth_session:
        return jsonify({'success': False, 'message': 'Please login first'}), 401
    
    data = request.json
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
        return jsonify({'success': False, 'message': 'Please login first'}), 401
    
    data = request.json
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
        return jsonify({'success': False, 'message': 'Please login first'}), 401
    
    data = request.json
    lecture_ids = data.get('lecture_ids', [])
    transcribe_ids = data.get('transcribe_ids', [])
    summarize_ids = data.get('summarize_ids', [])
    summarize_audio_only = data.get('summarize_audio_only', False)  # New: summarize audio even without transcription
    analyze_video = data.get('analyze_video', False)  # New: analyze static frames with LLM
    use_multiprocessing = data.get('use_multiprocessing', False)  # New: multiprocessing toggle
    
    if not lecture_ids:
        return jsonify({'success': False, 'message': 'No lectures selected'}), 400
    
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
            scraper = LearnUsScraper(auth_session)
            downloader = VideoDownloader(str(download_dir))
            # Local processing not available in web version
                if transcribe_ids or summarize_audio_only or analyze_video:
                task_status[task_id]['status'] = 'error'
                task_status[task_id]['messages'].append('Transcription and analysis are not available in web version. Use local version for these features.')
                return
            
            transcriber = None
            summarizer = None
            video_analyzer = None
            
            completed = 0
            failed = 0
            
            for idx, lecture_id in enumerate(lecture_ids):
                # Check if task is paused or stopped
                if task_status[task_id].get('stopped', False):
                    task_status[task_id]['messages'].append('Download stopped by user')
                    task_status[task_id]['status'] = 'stopped'
                    break
                
                # Wait if paused
                while task_status[task_id].get('paused', False) and not task_status[task_id].get('stopped', False):
                    time.sleep(0.5)
                
                # Check again after pause
                if task_status[task_id].get('stopped', False):
                    task_status[task_id]['messages'].append('Download stopped by user')
                    task_status[task_id]['status'] = 'stopped'
                    break
                
                try:
                    # Update current lecture index
                    task_status[task_id]['current_lecture_index'] = idx
                    
                    # Find lecture
                    lecture = next((l for l in lectures_cache if l.lecture_id == lecture_id), None)
                    if not lecture:
                        task_status[task_id]['messages'].append(f"Lecture {lecture_id} not found")
                        failed += 1
                        continue
                    
                    # Find course info for folder structure
                    course = next((c for c in courses_cache if c.course_id == lecture.course_id), None)
                    year = course.year if course else str(datetime.now().year)
                    semester = course.semester if course else "Unknown"
                    course_name = lecture.course_name
                    
                    # Extract video URL
                    task_status[task_id]['messages'].append(f"Extracting video URL for: {lecture.title}")
                    video_url = scraper.extract_video_url(lecture)
                    
                    if not video_url:
                        task_status[task_id]['messages'].append(f"Failed to extract video URL for: {lecture.title}")
                        failed += 1
                        continue
                    
                    # Download video
                    output_path = downloader.get_output_path(
                        year, semester, course_name,
                        lecture.week,
                        lecture.title
                    )
                    
                    task_status[task_id]['messages'].append(f"Downloading: {lecture.title}")
                    if downloader.download_video(video_url, output_path, auth_session):
                        completed += 1
                        task_status[task_id]['messages'].append(f"Downloaded: {lecture.title}")
                        
                        transcript_path = None
                        
                        # Transcribe if requested
                        if lecture_id in transcribe_ids and transcriber:
                            task_status[task_id]['messages'].append(f"Transcribing: {lecture.title}")
                            transcript_path = transcriber.transcribe_video(output_path)
                            if transcript_path:
                                task_status[task_id]['messages'].append(f"Transcribed: {lecture.title}")
                        
                        # Summarize if requested
                        if summarizer:
                            summary_text = None
                            
                            # If transcription exists, use it
                            if transcript_path and transcript_path.exists():
                                with open(transcript_path, 'r', encoding='utf-8') as f:
                                    summary_text = f.read()
                            # If summarize_audio_only is enabled and no transcription, extract audio and transcribe
                            elif summarize_audio_only and transcriber:
                                task_status[task_id]['messages'].append(f"Extracting audio for summarization: {lecture.title}")
                                audio_path = transcriber.extract_audio(output_path)
                                if audio_path:
                                    task_status[task_id]['messages'].append(f"Transcribing audio: {lecture.title}")
                                    transcript_path = transcriber.transcribe_audio(audio_path)
                                    if transcript_path:
                                        with open(transcript_path, 'r', encoding='utf-8') as f:
                                            summary_text = f.read()
                            
                            # Generate summary if we have text
                            if summary_text and (lecture_id in summarize_ids or summarize_audio_only):
                                task_status[task_id]['messages'].append(f"Summarizing: {lecture.title}")
                                summary = summarizer.summarize(summary_text)
                                if summary:
                                    summary_path = output_path.parent / f"{output_path.stem}.summary.txt"
                                    with open(summary_path, 'w', encoding='utf-8') as f:
                                        f.write(summary)
                                    task_status[task_id]['messages'].append(f"Summarized: {lecture.title}")
                            
                            # Analyze video frames if requested
                            if analyze_video and video_analyzer and output_path.exists():
                                # Need transcript for frame analysis
                                transcript_json_path = None
                                if transcript_path and transcript_path.exists():
                                    # Try to find JSON version with timestamps
                                    transcript_json_path = transcript_path.with_suffix('.json')
                                    if not transcript_json_path.exists():
                                        # Use text transcript as fallback
                                        transcript_json_path = transcript_path
                                
                                if not transcript_json_path or not transcript_json_path.exists():
                                    # Extract and transcribe audio if not already done
                                    if transcriber:
                                        task_status[task_id]['messages'].append(f"Transcribing for video analysis: {lecture.title}")
                                        audio_path = transcriber.extract_audio(output_path)
                                        if audio_path:
                                            transcript_path = transcriber.transcribe_audio(audio_path)
                                            transcript_json_path = transcript_path.with_suffix('.json')
                                
                                if transcript_json_path and transcript_json_path.exists():
                                    task_status[task_id]['messages'].append(f"Analyzing video frames: {lecture.title}")
                                    try:
                                        analysis_output_dir = output_path.parent / f"{output_path.stem}_analysis"
                                        result = video_analyzer.analyze_video(
                                            output_path,
                                            transcript_json_path,
                                            analysis_output_dir
                                        )
                                        change_count = result.get('total_changes', 0)
                                        phrase_count = len(result.get('important_phrases', []))
                                        task_status[task_id]['messages'].append(f"Detected {change_count} frame changes, extracted {phrase_count} key phrases: {lecture.title}")
                                    except Exception as e:
                                        task_status[task_id]['messages'].append(f"Video analysis error: {str(e)}")
                                else:
                                    task_status[task_id]['messages'].append(f"Transcript required for video analysis: {lecture.title}")
                    else:
                        failed += 1
                        task_status[task_id]['messages'].append(f"Failed to download: {lecture.title}")
                    
                    # Update progress
                    task_status[task_id]['progress'] = int((idx + 1) / len(lecture_ids) * 100)
                    task_status[task_id]['completed'] = completed
                    task_status[task_id]['failed'] = failed
                    
                except Exception as e:
                    failed += 1
                    task_status[task_id]['messages'].append(f"Error processing lecture {lecture_id}: {str(e)}")
            
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
    task_status[task_id]['messages'].append('Task paused by user')
    return jsonify({'success': True, 'message': 'Task paused'})


@app.route('/api/task/<task_id>/resume', methods=['POST'])
def resume_task(task_id):
    """Resume a paused task"""
    if task_id not in task_status:
        return jsonify({'success': False, 'message': 'Task not found'}), 404
    
    if task_status[task_id]['status'] != 'running':
        return jsonify({'success': False, 'message': 'Task is not running'}), 400
    
    task_status[task_id]['paused'] = False
    task_status[task_id]['messages'].append('Task resumed by user')
    return jsonify({'success': True, 'message': 'Task resumed'})


@app.route('/api/task/<task_id>/cancel', methods=['POST'])
def cancel_task(task_id):
    """Cancel a task"""
    if task_id not in task_status:
        return jsonify({'success': False, 'message': 'Task not found'}), 404
    
    task_status[task_id]['stopped'] = True
    task_status[task_id]['paused'] = False
    task_status[task_id]['status'] = 'cancelled'
    task_status[task_id]['messages'].append('Task cancelled by user')
    return jsonify({'success': True, 'message': 'Task cancelled'})


@app.route('/api/available-semesters', methods=['GET'])
def get_available_semesters():
    """Get list of all available semesters that have courses"""
    global auth_session
    
    if not auth_session:
        return jsonify({'success': False, 'message': 'Please login first'}), 401
    
    try:
        scraper = LearnUsScraper(auth_session)
        current_year_val = datetime.now().year
        available_semesters = []
        
        # Check current year and previous 8 years for maximum coverage
        for check_year in range(current_year_val, current_year_val - 9, -1):
            for check_sem in ['20', '11', '10', '21']:  # 2nd, Summer, 1st, Winter
                courses = scraper.parse_course_list(year=str(check_year), semester=check_sem)
                if courses:
                    available_semesters.append({
                        'year': str(check_year),
                        'semester': check_sem,
                        'semester_name': get_semester_name(check_sem),
                        'course_count': len(courses)
                    })
        
        # Sort by year (desc) then semester (desc)
        available_semesters.sort(key=lambda x: (x['year'], x['semester']), reverse=True)
        
        return jsonify({
            'success': True,
            'semesters': available_semesters
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


def get_semester_name(semester_code):
    """Convert semester code to readable name"""
    semester_names = {
        '10': '1st Semester',
        '20': '2nd Semester',
        '11': 'Summer',
        '21': 'Winter'
    }
    return semester_names.get(semester_code, f'Semester {semester_code}')


# Global cloud storage settings
cloud_storage_settings = {
    'enabled': False,
    'type': 'onedrive',  # 'onedrive' or 'gdrive'
    'path': None  # Base path for cloud storage
}


@app.route('/api/cloud-settings', methods=['GET'])
def get_cloud_settings():
    """Get current cloud storage settings"""
    return jsonify({
        'success': True,
        'settings': cloud_storage_settings
    })


@app.route('/api/cloud-settings', methods=['POST'])
def set_cloud_settings():
    """Set global cloud storage settings"""
    global cloud_storage_settings
    
    try:
        data = request.json
        cloud_storage_settings['enabled'] = data.get('enabled', False)
        cloud_storage_settings['type'] = data.get('type', 'onedrive')
        cloud_storage_settings['path'] = data.get('path')
        
        return jsonify({
            'success': True,
            'message': 'Cloud storage settings updated',
            'settings': cloud_storage_settings
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/cloud-link', methods=['GET', 'POST'])
def get_cloud_link():
    """Get cloud storage link and instructions based on user configuration"""
    try:
        # GET uses global settings
        if not cloud_storage_settings.get('enabled'):
            return jsonify({
                'success': False,
                'message': 'Cloud storage not configured. Please configure it in Settings page.'
            }), 400
        
        cloud_type = cloud_storage_settings.get('type', 'onedrive')
        configured_path = cloud_storage_settings.get('path')
        
        # Use configured path or default to downloads directory
        if configured_path:
            base_path = os.path.abspath(os.path.expanduser(configured_path))
        else:
            base_path = str(download_dir.resolve())
        
        # Generate cloud link information based on user configuration
        if cloud_type == 'onedrive':
            link_info = {
                'type': 'onedrive',
                'path': base_path,
                'configured': bool(configured_path),
                'instructions': (
                    f'OneDrive Cloud Storage Configuration:\n\n'
                    f'Configured Path: {base_path}\n\n'
                    '📋 Setup Instructions:\n'
                    '1. Ensure OneDrive is installed and syncing on your computer\n'
                    '2. Set the "Cloud Storage Path" in Settings to a folder within your OneDrive directory\n'
                    '   Example: C:\\Users\\YourName\\OneDrive\\LearnUs Downloads\n'
                    '3. All files downloaded will be saved to this path and sync automatically\n'
                    '4. You can access your files from any device with OneDrive\n\n'
                    '💡 Tip: If the path is already in OneDrive, files will sync automatically.'
                ),
                'sync_url': 'https://onedrive.live.com' if configured_path else None
            }
        elif cloud_type == 'gdrive':
            link_info = {
                'type': 'gdrive',
                'path': base_path,
                'configured': bool(configured_path),
                'instructions': (
                    f'Google Drive Cloud Storage Configuration:\n\n'
                    f'Configured Path: {base_path}\n\n'
                    '📋 Setup Instructions:\n'
                    '1. Install "Google Drive for Desktop" if not already installed\n'
                    '2. Set the "Cloud Storage Path" in Settings to a folder within your Google Drive directory\n'
                    '   Example: C:\\Users\\YourName\\Google Drive\\LearnUs Downloads\n'
                    '3. All files downloaded will be saved to this path and sync automatically\n'
                    '4. You can access your files from any device with Google Drive\n\n'
                    '💡 Tip: If the path is already in Google Drive, files will sync automatically.'
                ),
                'sync_url': 'https://drive.google.com' if configured_path else None
            }
        else:
            return jsonify({'success': False, 'message': 'Invalid cloud type'}), 400
        
        return jsonify({
            'success': True,
            'cloud_link': link_info
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# LLM API settings endpoints (for web version)
@app.route('/api/llm-settings', methods=['GET'])
def get_llm_settings():
    """Get current LLM API settings"""
    # Don't expose actual API keys in response
    safe_settings = {
        'provider': llm_settings['provider'],
        'openai_model': llm_settings['openai_model'],
        'google_model': llm_settings['google_model'],
        'ollama_url': llm_settings['ollama_url'],
        'ollama_model': llm_settings['ollama_model'],
        'has_openai_key': bool(llm_settings['openai_api_key']),
        'has_google_key': bool(llm_settings['google_api_key']),
    }
    return jsonify({
        'success': True,
        'settings': safe_settings,
        'app_mode': APP_MODE,
        'is_local_mode': IS_LOCAL_MODE
    })


@app.route('/api/llm-settings', methods=['POST'])
def set_llm_settings():
    """Update LLM API settings"""
    global llm_settings
    
    try:
        data = request.json
        
        if 'provider' in data:
            llm_settings['provider'] = data['provider']
        if 'openai_api_key' in data:
            llm_settings['openai_api_key'] = data['openai_api_key'] or ''
        if 'openai_model' in data:
            llm_settings['openai_model'] = data['openai_model']
        if 'google_api_key' in data:
            llm_settings['google_api_key'] = data['google_api_key'] or ''
        if 'google_model' in data:
            llm_settings['google_model'] = data['google_model']
        if 'ollama_url' in data:
            llm_settings['ollama_url'] = data['ollama_url']
        if 'ollama_model' in data:
            llm_settings['ollama_model'] = data['ollama_model']
        
        # Update environment variables
        if llm_settings['openai_api_key']:
            os.environ['OPENAI_API_KEY'] = llm_settings['openai_api_key']
        if llm_settings['google_api_key']:
            os.environ['GOOGLE_API_KEY'] = llm_settings['google_api_key']
        os.environ['LLM_PROVIDER'] = llm_settings['provider']
        os.environ['OPENAI_MODEL'] = llm_settings['openai_model']
        os.environ['GOOGLE_MODEL'] = llm_settings['google_model']
        os.environ['OLLAMA_URL'] = llm_settings['ollama_url']
        os.environ['OLLAMA_MODEL'] = llm_settings['ollama_model']
        
        return jsonify({
            'success': True,
            'message': 'LLM settings updated',
            'app_mode': APP_MODE
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/app-info', methods=['GET'])
def get_app_info():
    """Get application information and capabilities"""
    return jsonify({
        'success': True,
        'app_mode': APP_MODE,
        'is_local_mode': IS_LOCAL_MODE,
        'is_web_mode': IS_WEB_MODE,
        'features': {
            'transcription': IS_LOCAL_MODE,
            'video_analysis': IS_LOCAL_MODE,
            'llm_api': IS_WEB_MODE,
            'downloads': True,
            'cloud_sync': True,
        }
    })


@app.route('/api/download-single', methods=['POST'])
def download_single_item():
    """Download a single item (video, material, or assignment)"""
    global auth_session, lectures_cache
    
    if not auth_session:
        return jsonify({'success': False, 'message': 'Please login first'}), 401
    
    data = request.json
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
            
            # Get course info
            course = next((c for c in courses_cache if c.course_id == str(course_id)), None)
            year = course.year if course else str(datetime.now().year)
            semester = course.semester if course else "Unknown"
            course_name = course.course_name if course else "Unknown"
            
            if item_type == 'video':
                lecture = next((l for l in lectures_cache if l.lecture_id == item_id), None)
                if not lecture:
                    task_status[task_id]['status'] = 'error'
                    task_status[task_id]['failed'] = 1
                    task_status[task_id]['messages'].append('Lecture not found')
                    return
                
                downloader = VideoDownloader(str(download_dir))
                video_url = scraper.extract_video_url(lecture)
                if not video_url:
                    task_status[task_id]['status'] = 'error'
                    task_status[task_id]['failed'] = 1
                    task_status[task_id]['messages'].append('Failed to extract video URL')
                    return
                
                output_path = downloader.get_output_path(year, semester, course_name, lecture.week, lecture.title)
                
                # Check if file exists in old structure and relocate it
                if not output_path.exists():
                    old_file = find_file_in_old_structure(download_dir, output_path.name, year, semester, course_name)
                    if old_file and old_file.exists():
                        task_status[task_id]['messages'].append(f"Found existing file in old structure, relocating: {lecture.title}")
                        relocated = relocate_file_to_new_structure(old_file, download_dir, year, semester, course_name, lecture.week)
                        if relocated and relocated == output_path:
                            task_status[task_id]['status'] = 'completed'
                            task_status[task_id]['completed'] = 1
                            task_status[task_id]['items'][item_id] = {'progress': 100, 'status': 'completed'}
                            return
                
                # Skip if already exists in new location
                if output_path.exists():
                    task_status[task_id]['status'] = 'completed'
                    task_status[task_id]['completed'] = 1
                    task_status[task_id]['items'][item_id] = {'progress': 100, 'status': 'completed'}
                    task_status[task_id]['messages'].append(f"File already exists: {lecture.title}")
                    return
                
                task_status[task_id]['messages'].append(f"Downloading: {lecture.title}")
                
                if downloader.download_video(video_url, output_path, auth_session):
                    task_status[task_id]['status'] = 'completed'
                    task_status[task_id]['completed'] = 1
                    task_status[task_id]['items'][item_id] = {'progress': 100, 'status': 'completed'}
                else:
                    task_status[task_id]['status'] = 'error'
                    task_status[task_id]['failed'] = 1
                    task_status[task_id]['items'][item_id] = {'progress': 0, 'status': 'failed'}
                    
            elif item_type == 'material' or item_type == 'assignment':
                # Create directory structure: year/semester/course/week/Materials or Assignments
                section_clean = "".join(c for c in section_title if c not in r'<>:"/\|?*').strip()
                week_clean = "".join(c for c in week if c not in r'<>:"/\|?*').strip()
                course_clean = "".join(c for c in course_name if c not in r'<>:"/\|?*')
                
                if item_type == 'material':
                    save_dir = download_dir / year / semester / course_clean / week_clean / "Materials"
                else:
                    save_dir = download_dir / year / semester / course_clean / week_clean / "Assignments"
                
                save_dir.mkdir(parents=True, exist_ok=True)
                save_path = save_dir / item_name
                
                # Check if file exists in old structure and relocate it
                if not save_path.exists():
                    old_file = find_file_in_old_structure(download_dir, item_name, year, semester, course_name)
                    if old_file and old_file.exists():
                        task_status[task_id]['messages'].append(f"Found existing file in old structure, relocating: {item_name}")
                        relocated = relocate_file_to_new_structure(old_file, download_dir, year, semester, course_name, week)
                        if relocated and relocated == save_path:
                            task_status[task_id]['status'] = 'completed'
                            task_status[task_id]['completed'] = 1
                            task_status[task_id]['items'][item_id] = {'progress': 100, 'status': 'completed'}
                            return
                
                if save_path.exists():
                    task_status[task_id]['status'] = 'completed'
                    task_status[task_id]['completed'] = 1
                    task_status[task_id]['messages'].append('File already exists')
                else:
                    if scraper.download_file(item_url, str(save_path)):
                        task_status[task_id]['status'] = 'completed'
                        task_status[task_id]['completed'] = 1
                        task_status[task_id]['items'][item_id] = {'progress': 100, 'status': 'completed'}
                    else:
                        task_status[task_id]['status'] = 'error'
                        task_status[task_id]['failed'] = 1
                        task_status[task_id]['items'][item_id] = {'progress': 0, 'status': 'failed'}
            
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
            
            # Extract course info from path
            relative_path = video_path.relative_to(download_dir)
            path_parts = relative_path.parts
            
            course_info = "Unknown"
            if len(path_parts) >= 2:
                # Format: year-semester-course_name/filename
                course_info = path_parts[0]  # year-semester-course_name
            
            # Use forward slashes for cross-platform compatibility
            relative_path_str = str(video_path.relative_to(download_dir)).replace('\\', '/')
            
            videos.append({
                'name': video_path.name,
                'path': relative_path_str,
                'full_path': str(video_path),
                'size': video_path.stat().st_size,
                'modified': video_path.stat().st_mtime,
                'course': course_info,
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
            
        # New structure: year/semester/course/week/files
        # Also support old structure: Year_Semester_CourseName for backward compatibility
        
        # Check if new structure (has year directories)
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
    """List downloaded files (legacy endpoint)"""
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
        # Normalize path separators for cross-platform compatibility
        file_path = file_path.replace('/', os.sep).replace('\\', os.sep)
        full_path = download_dir / file_path
        # Security check: ensure path is within download_dir
        if not str(full_path.resolve()).startswith(str(download_dir.resolve())):
            return jsonify({'success': False, 'message': 'Invalid path'}), 403
        
        if not full_path.exists():
            return jsonify({'success': False, 'message': f'File not found: {full_path}'}), 404
        
        return send_file(full_path, as_attachment=False)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/check-files', methods=['POST'])
def check_files():
    """Check if files exist in the downloads directory"""
    try:
        data = request.json
        file_paths = data.get('file_paths', [])  # List of relative paths
        
        existing_files = {}
        
        for rel_path in file_paths:
            # Normalize path
            rel_path_clean = rel_path.replace('/', os.sep).replace('\\', os.sep)
            full_path = download_dir / rel_path_clean
            
            # Security check
            if str(full_path.resolve()).startswith(str(download_dir.resolve())):
                existing_files[rel_path] = full_path.exists() and full_path.is_file()
            else:
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
    """Transcribe an existing video file (local mode only)"""
    if not IS_LOCAL_MODE:
        return jsonify({
            'success': False,
            'message': 'Transcription is only available in local mode. Use app_local.py or set APP_MODE=local to enable.'
        }), 403
    
    data = request.json
    video_path_str = data.get('video_path')
    
    if not video_path_str:
        return jsonify({'success': False, 'message': 'Video path required'}), 400
    
    # Normalize path separators for cross-platform compatibility
    video_path_str = video_path_str.replace('/', os.sep).replace('\\', os.sep)
    video_path = download_dir / video_path_str
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
    """Analyze an existing video file (local mode only)"""
    if not IS_LOCAL_MODE:
        return jsonify({
            'success': False,
            'message': 'Video analysis is only available in local mode. Set APP_MODE=local to enable.'
        }), 403
    
    data = request.json
    video_path_str = data.get('video_path')
    
    if not video_path_str:
        return jsonify({'success': False, 'message': 'Video path required'}), 400
    
    # Normalize path separators for cross-platform compatibility
    video_path_str = video_path_str.replace('/', os.sep).replace('\\', os.sep)
    video_path = download_dir / video_path_str
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
                    # Use text transcript as fallback
                    transcript_json_path = transcript_path
                else:
                    # Need to transcribe first
                    task_status[task_id]['messages'].append("No transcript found. Transcribing audio...")
                    task_status[task_id]['progress'] = 20
                    audio_path = transcriber.extract_audio(video_path)
                    if audio_path:
                        task_status[task_id]['progress'] = 40
                        transcript_path = transcriber.transcribe_audio(audio_path)
                        transcript_json_path = transcript_path.with_suffix('.json')
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
    """Batch transcribe multiple video files (local mode only)"""
    if not IS_LOCAL_MODE:
        return jsonify({
            'success': False,
            'message': 'Batch transcription is only available in local mode. Use app_local.py or set APP_MODE=local to enable.'
        }), 403
    
    data = request.json
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
                    # Normalize path
                    video_path_str = video_path_str.replace('/', os.sep).replace('\\', os.sep)
                    video_path = download_dir / video_path_str
                    
                    if not video_path.exists():
                        task_status[task_id]['messages'].append(f"File not found: {video_path.name}")
                        failed += 1
                        continue
                    
                    task_status[task_id]['messages'].append(f"Transcribing ({idx+1}/{len(video_paths)}): {video_path.name}")
                    
                    transcript_path = transcriber.transcribe_video(video_path)
                    
                    if transcript_path:
                        completed += 1
                        task_status[task_id]['messages'].append(f"✅ Completed: {video_path.name}")
                    else:
                        failed += 1
                        task_status[task_id]['messages'].append(f"❌ Failed: {video_path.name}")
                    
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
    """Batch analyze multiple video files (local mode only)"""
    if not IS_LOCAL_MODE:
        return jsonify({
            'success': False,
            'message': 'Batch analysis is only available in local mode. Use app_local.py or set APP_MODE=local to enable.'
        }), 403
    
    data = request.json
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
                    # Normalize path
                    video_path_str = video_path_str.replace('/', os.sep).replace('\\', os.sep)
                    video_path = download_dir / video_path_str
                    
                    if not video_path.exists():
                        task_status[task_id]['messages'].append(f"File not found: {video_path.name}")
                        failed += 1
                        continue
                    
                    task_status[task_id]['messages'].append(f"Analyzing ({idx+1}/{len(video_paths)}): {video_path.name}")
                    
                    # Check for transcript
                    transcript_json_path = video_path.with_suffix('.json')
                    if not transcript_json_path.exists():
                        transcript_path = video_path.with_suffix('.txt')
                        if not transcript_path.exists():
                            # Need to transcribe first
                            task_status[task_id]['messages'].append(f"Transcribing audio first: {video_path.name}")
                            audio_path = transcriber.extract_audio(video_path)
                            if audio_path:
                                transcript_path = transcriber.transcribe_audio(audio_path)
                                if transcript_path:
                                    transcript_json_path = transcript_path.with_suffix('.json')
                    
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
                        task_status[task_id]['messages'].append(f"✅ {change_count} changes, {phrase_count} key phrases: {video_path.name}")
                    else:
                        failed += 1
                        task_status[task_id]['messages'].append(f"❌ No transcript available: {video_path.name}")
                    
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
    file_path = download_dir / filename
    if file_path.exists() and file_path.is_file():
        return send_file(str(file_path), as_attachment=True)
    else:
        return jsonify({'success': False, 'message': 'File not found'}), 404


@app.route('/api/download_video', methods=['POST'])
def download_video():
    """Download a single video file (for Chrome extension)"""
    global auth_session
    
    if not auth_session:
        return jsonify({'success': False, 'message': 'Please login first'}), 401
    
    data = request.json
    video_url = data.get('video_url')
    output_path = data.get('output_path')
    is_hls = data.get('is_hls', False)
    
    if not video_url or not output_path:
        return jsonify({'success': False, 'message': 'video_url and output_path are required'}), 400
    
    try:
        from downloader import VideoDownloader
        from pathlib import Path
        
        # Parse output path
        path_parts = output_path.replace('\\', '/').split('/')
        filename = path_parts.pop()
        relative_dir = '/'.join(path_parts) if path_parts else ''
        
        # Create downloader with proper directory
        downloader = VideoDownloader(str(download_dir))
        
        # Construct full path
        if relative_dir:
            full_dir = download_dir / relative_dir
            full_dir.mkdir(parents=True, exist_ok=True)
            output_file = full_dir / filename
        else:
            output_file = download_dir / filename
        
        # Download video
        if downloader.download_video(video_url, output_file, auth_session):
            return jsonify({
                'success': True,
                'message': 'Video downloaded successfully',
                'path': str(output_file.relative_to(download_dir))
            })
        else:
            return jsonify({'success': False, 'message': 'Download failed'}), 500
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500



@app.route('/api/download-materials', methods=['POST'])
def download_materials():
    """Download lecture materials and assignments for a course"""
    global auth_session
    
    if not auth_session:
        return jsonify({'success': False, 'message': 'Please login first'}), 401
        
    data = request.json
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
            
            # Get course info for directory structure (year/semester/course/week)
            course = next((c for c in courses_cache if c.course_id == str(course_id)), None)
            if course:
                year = course.year
                semester = course.semester
                course_name = course.course_name
            else:
                year = str(datetime.now().year)
                semester = "Unknown"
                course_name = f"Course_{course_id}"
            
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
            
            # Get course info for directory structure
            if course:
                year = course.year
                semester = course.semester
                course_name = course.course_name
            else:
                year = str(datetime.now().year)
                semester = "Unknown"
                course_name = f"Course_{course_id}"
            
            # Clean course name for directory
            course_clean = "".join(c for c in course_name if c not in r'<>:"/\|?*')
            year_clean = "".join(c for c in year if c not in r'<>:"/\|?*')
            semester_clean = "".join(c for c in semester if c not in r'<>:"/\|?*')
            
            processed_count = 0
            
            for section in sections:
                section_title = section['title'] or "General"
                section_title = "".join(c for c in section_title if c not in r'<>:"/\|?*').strip()
                
                # Download Materials
                for mat in section['materials']:
                    processed_count += 1
                    task_status[task_id]['current_item'] = mat['name']
                    task_status[task_id]['progress'] = int((processed_count / (total_items or 1)) * 100)
                    
                    mat_type = mat.get('type', 'file')
                    
                    if mat_type == 'folder':
                        # Parse folder page to get actual files
                        task_status[task_id]['messages'].append(f"Parsing folder: {mat['name']}")
                        folder_data = scraper.parse_folder_page(mat['url'])
                        
                        # Save folder description if available
                        if folder_data.get('description'):
                            week_clean = "".join(c for c in section_title if c not in r'<>:"/\|?*').strip()
                            folder_dir = download_dir / year_clean / semester_clean / course_clean / week_clean / "Materials" / "".join(c for c in mat['name'] if c not in r'<>:"/\|?*')
                            folder_dir.mkdir(parents=True, exist_ok=True)
                            desc_path = folder_dir / "folder_description.txt"
                            try:
                                with open(desc_path, 'w', encoding='utf-8') as f:
                                    f.write(folder_data['description'])
                                task_status[task_id]['messages'].append(f"Saved folder description: {mat['name']}")
                            except Exception as e:
                                task_status[task_id]['messages'].append(f"Failed to save description: {e}")
                        
                        # Download files from folder
                        week_clean = "".join(c for c in section_title if c not in r'<>:"/\|?*').strip()
                        folder_dir = download_dir / year_clean / semester_clean / course_clean / week_clean / "Materials" / "".join(c for c in mat['name'] if c not in r'<>:"/\|?*')
                        for file_item in folder_data.get('files', []):
                            save_path = folder_dir / file_item['name']
                            if not save_path.exists():
                                task_status[task_id]['messages'].append(f"Downloading from folder: {file_item['name']}")
                                if scraper.download_file(file_item['url'], str(save_path)):
                                    task_status[task_id]['completed'] += 1
                                else:
                                    task_status[task_id]['failed'] += 1
                            else:
                                task_status[task_id]['completed'] += 1
                    else:
                        # Regular file download
                        week_clean = "".join(c for c in section_title if c not in r'<>:"/\|?*').strip()
                        save_dir = download_dir / year_clean / semester_clean / course_clean / week_clean / "Materials"
                        save_dir.mkdir(parents=True, exist_ok=True)
                        save_path = save_dir / mat['name']
                        
                        if not save_path.exists():
                            task_status[task_id]['messages'].append(f"Downloading file: {mat['name']}")
                            if scraper.download_file(mat['url'], str(save_path)):
                                task_status[task_id]['completed'] += 1
                            else:
                                task_status[task_id]['failed'] += 1
                                task_status[task_id]['messages'].append(f"Failed to download: {mat['name']}")
                        else:
                            task_status[task_id]['messages'].append(f"File exists: {mat['name']}")
                            task_status[task_id]['completed'] += 1

                # Process Assignments
                for assign in section['assignments']:
                    processed_count += 1
                    task_status[task_id]['current_item'] = assign['name']
                    task_status[task_id]['progress'] = int((processed_count / (total_items or 1)) * 100)
                    task_status[task_id]['messages'].append(f"Processing assignment: {assign['name']}")
                    
                    week_clean = "".join(c for c in section_title if c not in r'<>:"/\|?*').strip()
                    assign_dir = download_dir / year_clean / semester_clean / course_clean / week_clean / "Assignments" / "".join(c for c in assign['name'] if c not in r'<>:"/\|?*')
                    assign_dir.mkdir(parents=True, exist_ok=True)
                    
                    assign_data = scraper.parse_assignment_page(assign.get('url'))
                    
                    # Save assignment description as text file
                    if assign_data.get('description'):
                        desc_path = assign_dir / "assignment_description.txt"
                        try:
                            with open(desc_path, 'w', encoding='utf-8') as f:
                                f.write(assign_data['description'])
                            task_status[task_id]['messages'].append(f"Saved assignment description: {assign['name']}")
                        except Exception as e:
                            task_status[task_id]['messages'].append(f"Failed to save description: {e}")
                    
                    # Download Requirements
                    for req in assign_data.get('requirements', []):
                        save_path = assign_dir / req['name']
                        if not save_path.exists():
                            task_status[task_id]['messages'].append(f"Downloading requirement: {req['name']}")
                            if scraper.download_file(req['url'], str(save_path)):
                                task_status[task_id]['completed'] += 1
                            else:
                                task_status[task_id]['failed'] += 1
                        else:
                            task_status[task_id]['completed'] += 1
                    
                    # Download Submissions
                    for sub in assign_data.get('submissions', []):
                        save_path = assign_dir / sub['name']
                        if not save_path.exists():
                            task_status[task_id]['messages'].append(f"Downloading submission: {sub['name']}")
                            if scraper.download_file(sub['url'], str(save_path)):
                                task_status[task_id]['completed'] += 1
                            else:
                                task_status[task_id]['failed'] += 1
                        else:
                            task_status[task_id]['completed'] += 1
                            
                    task_status[task_id]['completed'] += 1

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
            print(f"🔒 SSL certificates loaded: {args.ssl_cert}, {args.ssl_key}")
        except Exception as e:
            print(f"⚠️  SSL certificate error: {e}")
            print("⚠️  Falling back to HTTP")
            ssl_context = None
    
    protocol = 'https' if ssl_context else 'http'
    print(f"🌐 Starting server on {protocol}://{args.host}:{args.port}")
    if args.production:
        print("📦 Production mode enabled")
        print("💡 For better performance, use run_production.py with --use-gunicorn")
    else:
        print("⚠️  Development mode - use --production for production")
    
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug and not args.production,
        ssl_context=ssl_context,
        threaded=True
    )