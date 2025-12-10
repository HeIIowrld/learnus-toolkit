"""
Scraper module for parsing LearnUs course pages and extracting video lectures
"""
import re
from bs4 import BeautifulSoup
from typing import List, Dict, Optional, Tuple
import requests
from datetime import datetime


LEARNUS_ORIGIN = 'https://ys.learnus.org'


class CourseInfo:
    """Represents a course"""
    def __init__(self, course_id: str, course_name: str, course_url: str, 
                 year: str = "", semester: str = "", professor: str = ""):
        self.course_id = course_id
        self.course_name = course_name
        self.course_url = course_url
        self.year = year
        self.semester = semester
        self.professor = professor
        self.lectures = []  # List of LectureInfo
    
    def __repr__(self):
        return f"Course({self.course_name} - {self.year} {self.semester})"


class LectureInfo:
    """Represents a lecture video"""
    def __init__(self, lecture_id: str, title: str, week: str, status: str, 
                 activity_url: str, course_name: str = "", course_id: str = ""):
        self.lecture_id = lecture_id
        self.title = title
        self.week = week
        self.status = status  # "Done" or "New"
        self.activity_url = activity_url
        self.course_name = course_name
        self.course_id = course_id
        self.video_url = None
    
    def __repr__(self):
        return f"Lecture({self.lecture_id}: {self.week} - {self.title} [{self.status}])"


class LearnUsScraper:
    """Scrapes LearnUs course pages for video lectures"""
    
    def __init__(self, session: requests.Session):
        self.session = session
    
    def parse_course_list(self, year: str = None, semester: str = None) -> List[CourseInfo]:
        """
        Parse courses from LearnUs. Handles both Card View (Dashboard) and Table View (Past Semesters).
        """
        print(f"\n{'='*60}")
        print(f"parse_course_list() CALLED")
        print(f"  year: {year}")
        print(f"  semester: {semester}")
        print(f"{'='*60}")
        
        courses = []
        
        try:
            # 1. Determine URL
            url = LEARNUS_ORIGIN
            if year and semester:
                 url = f"{LEARNUS_ORIGIN}/local/ubion/user/index.php?year={year}&semester={semester}"
            
            # 2. Fetch the page
            print(f"\n=== Fetching Course List: {url}")
            print(f"→ Making HTTP GET request to: {url}")
            print(f"→ Session cookies: {list(self.session.cookies.keys())}")
            
            response = self.session.get(url, timeout=10)
            
            print(f"✓ Response status: {response.status_code}")
            print(f"✓ Response URL (after redirects): {response.url}")
            print(f"✓ Content length: {len(response.text)} characters")
            
            # Check for session expiry (redirect to login)
            if 'login' in response.url or '로그인' in response.text[:1000]:
                print("❌ Session expired or invalid. Please login again.")
                return []
                
            soup = BeautifulSoup(response.text, 'html.parser')

            # 3. Check for Table Structure (Past Semesters / Filtered View)
            if soup.select_one('tbody.my-course-lists'):
                print("→ Table view detected")
                courses = self._parse_table_course_list(soup)
                print(f"✓ Found {len(courses)} courses in table view")
                return courses

            # 4. Fallback: Card View (Main Dashboard)
            print("→ Checking for card view (Dashboard)...")
            course_lists = soup.find_all(['ul', 'div'], class_=['my-course-lists', 'course_lists'])
            
            for course_list in course_lists:
                course_boxes = course_list.find_all('div', class_='course-box')
                for box in course_boxes:
                    # Extract info from Card View
                    title_tag = box.find('a', class_='course-title')
                    if not title_tag: continue

                    course_name = title_tag.find('h4').get_text(strip=True) if title_tag.find('h4') else title_tag.get_text(strip=True)
                    course_url = title_tag['href']
                    
                    # Try to parse ID
                    course_id = ""
                    id_match = re.search(r'id=(\d+)', course_url)
                    if id_match:
                        course_id = id_match.group(1)
                    
                    # Dashboard view usually doesn't show year/semester clearly, so we default
                    courses.append(CourseInfo(
                        course_id=course_id,
                        course_name=course_name,
                        course_url=course_url,
                        year=str(datetime.now().year),
                        semester="Dashboard"
                    ))

            print(f"✓ Total courses found: {len(courses)}")
            return courses

        except Exception as e:
            print(f"❌ Error parsing course list: {e}")
            return []

    def _parse_table_course_list(self, soup: BeautifulSoup) -> List[CourseInfo]:
        """
        Parses the specific table structure found in 'LearnUs YONSEI.html'
        Robustly finds links by checking for 'id=' in href, rather than relying on classes.
        """
        courses = []
        try:
            rows = soup.select('tbody.my-course-lists tr')
            
            for row in rows:
                cols = row.find_all('td')
                if len(cols) < 3:
                    continue
                
                year = cols[0].get_text(strip=True)
                semester = cols[1].get_text(strip=True)
                
                # IMPROVED: Find ANY link in the 3rd column that contains "id="
                # This fixes the issue where class="coursefullname" might be missing
                link_tag = cols[2].find('a', href=re.compile(r'id=\d+'))
                
                if not link_tag:
                    continue

                course_url = link_tag.get('href', '').strip()
                course_name = link_tag.get_text(strip=True)

                if not course_url.startswith('http'):
                    course_url = f"{LEARNUS_ORIGIN}{course_url}"

                course_id = ""
                id_match = re.search(r'id=(\d+)', course_url)
                if id_match:
                    course_id = id_match.group(1)

                courses.append(CourseInfo(
                    course_id=course_id,
                    course_name=course_name,
                    course_url=course_url,
                    year=year,
                    semester=semester
                ))
                
        except Exception as e:
            print(f"Error parsing table rows: {e}")
            
        return courses

    def get_course_name(self, course_url: str) -> str:
        """Extract course name from course page"""
        try:
            response = self.session.get(course_url, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            title_tag = soup.find('title')
            if title_tag:
                title = title_tag.text.strip()
                match = re.search(r'강좌:\s*(.+)', title)
                if match:
                    return match.group(1).strip()
                return title
            return "Unknown Course"
        except Exception as e:
            return "Unknown Course"
    
    def parse_lecture_list(self, course_url: str) -> List[LectureInfo]:
        """Parse all video lectures from a course page"""
        lectures = []
        try:
            print(f"  Parsing lectures from: {course_url}")
            response = self.session.get(course_url, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            course_name = self.get_course_name(course_url)
            
            # Find course ID
            course_id = ""
            id_match = re.search(r'id=(\d+)', course_url)
            if id_match: course_id = id_match.group(1)
            
            # Find all activity instances
            activity_instances = soup.find_all('div', class_='activityinstance')
            lecture_counter = 1
            
            for activity_div in activity_instances:
                link = activity_div.find('a', href=True)
                if not link: continue
                
                href = link.get('href', '')
                onclick = link.get('onclick', '') or ''
                
                # === ROBUST VIDEO DETECTION ===
                is_video = False
                viewer_url = None
                
                # 1. Standard LearnUs VOD (mod/vod)
                if 'mod/vod' in href or 'mod/vod' in onclick:
                    is_video = True
                    if 'viewer.php' in onclick:
                        match = re.search(r"id=(\d+)", onclick)
                        if match: viewer_url = f"{LEARNUS_ORIGIN}/mod/vod/viewer.php?id={match.group(1)}"
                    elif 'view.php' in href:
                         viewer_url = href.replace('view.php', 'viewer.php')
                    elif 'viewer.php' in href:
                         viewer_url = href

                # 2. External Tools (Zoom/Panopto often disguised as mod/url or mod/ubfile)
                # We check the icon image to confirm it's a video/link
                elif 'mod/url' in href or 'mod/kalvid' in href or 'mod/commons' in href:
                    img = activity_div.find('img')
                    if img:
                        src = img.get('src', '')
                        # Check for video-like icons
                        if any(x in src for x in ['icon', 'video', 'vod', 'play']):
                            is_video = True
                            viewer_url = href

                if not is_video or not viewer_url:
                    continue
                
                if not viewer_url.startswith('http'):
                    viewer_url = f"{LEARNUS_ORIGIN}{viewer_url}" if viewer_url.startswith('/') else f"{LEARNUS_ORIGIN}/{viewer_url}"

                # Extract Title
                title = "Unknown Lecture"
                instancename = activity_div.find('span', class_='instancename')
                if instancename:
                    # Remove hidden accessibility text
                    for hidden in instancename.find_all(class_='accesshide'):
                        hidden.decompose()
                    title = instancename.get_text(strip=True)
                    title = re.sub(r'\s*동영상\s*$', '', title)

                # Extract Week/Section
                week = "General"
                section = activity_div.find_parent(['li', 'div'], id=re.compile(r'section-\d+'))
                if section:
                    # Try to find section name
                    # LearnUs usually puts it in a hidden span or aria-label
                    section_name_tag = soup.find(id=section['id'].replace('section-', 'section-name-'))
                    if section_name_tag:
                         week = section_name_tag.get_text(strip=True)
                    elif section.get('aria-label'):
                         week = section['aria-label']

                lectures.append(LectureInfo(
                    lecture_id=f"{course_id}_{lecture_counter}",
                    title=title,
                    week=week,
                    status="New",
                    activity_url=viewer_url,
                    course_name=course_name,
                    course_id=course_id
                ))
                lecture_counter += 1
            
            print(f"  ✓ Found {len(lectures)} lectures")
            return lectures
        except Exception as e:
            print(f"Error parsing lecture list: {e}")
            return []

    def parse_course_content(self, course_id):
        """
        Parse course content including files, assignments, and other materials.
        Also extracts professor name from course page.
        Handles various file formats: .py, .r, .c, .txt, .docx, .pdf, etc.
        """
        try:
            # Get the course page URL
            course_url = f"{LEARNUS_ORIGIN}/course/view.php?id={course_id}"
            print(f"\n  → Parsing course content from: {course_url}")
            
            response = self.session.get(course_url, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract professor name
            professor = None
            # Try to find professor in course header/info area
            # LearnUs typically shows this in various places
            prof_patterns = [
                soup.find('div', class_='course-info'),
                soup.find('div', class_='teacher-info'),
                soup.find(text=re.compile(r'교수|Professor|강사|Instructor', re.I)),
            ]
            
            for pattern in prof_patterns:
                if pattern and professor is None:
                    if isinstance(pattern, str):
                        # If it's a text node, get its parent and extract text
                        parent = pattern.parent if hasattr(pattern, 'parent') else None
                        if parent:
                            text = parent.get_text(strip=True)
                            # Extract professor name after the label
                            match = re.search(r'(?:교수|Professor|강사|Instructor)[:\s]+([^,\n]+)', text, re.I)
                            if match:
                                professor = match.group(1).strip()
                    else:
                        text = pattern.get_text(strip=True)
                        if ':' in text:
                            professor = text.split(':', 1)[1].strip()
            
            sections = []
            
            # Find all sections (weekly topics, modules, etc.)
            section_elements = soup.find_all(['li', 'div'], id=re.compile(r'section-\d+'))
            
            for section_elem in section_elements:
                section_id = section_elem.get('id', '')
                
                # Extract section title
                section_title = "General"
                section_name_tag = soup.find(id=section_id.replace('section-', 'sectionname-'))
                if section_name_tag:
                    section_title = section_name_tag.get_text(strip=True)
                elif section_elem.get('aria-label'):
                    section_title = section_elem['aria-label']
                
                materials = []
                assignments = []
                
                # Find all activity instances in this section
                activities = section_elem.find_all('div', class_='activityinstance')
                
                for activity in activities:
                    link = activity.find('a', href=True)
                    if not link:
                        continue
                    
                    href = link.get('href', '')
                    
                    # Get activity name
                    activity_name = "Unknown"
                    instancename = activity.find('span', class_='instancename')
                    if instancename:
                        # Remove hidden accessibility text
                        for hidden in instancename.find_all(class_='accesshide'):
                            hidden.decompose()
                        activity_name = instancename.get_text(strip=True)
                    
                    # Determine activity type based on URL patterns
                    
                    # 1. Files and Resources (mod/resource, mod/folder)
                    if 'mod/resource' in href:
                        # Direct file resource
                        file_ext = self._extract_file_extension(activity_name, href)
                        materials.append({
                            'name': activity_name,
                            'url': href,
                            'type': 'file',
                            'extension': file_ext
                        })
                    elif 'mod/folder' in href:
                        # Folder - mark it so we can parse it later
                        materials.append({
                            'name': activity_name,
                            'url': href,
                            'type': 'folder',
                            'extension': ''
                        })
                    
                    # 2. Assignments (mod/assign)
                    elif 'mod/assign' in href:
                        assignments.append({
                            'name': activity_name,
                            'url': href,
                            'type': 'assignment'
                        })
                    
                    # 3. External files (mod/url pointing to files)
                    elif 'mod/url' in href:
                        # Check if it's likely a file link
                        file_ext = self._extract_file_extension(activity_name, href)
                        if file_ext:
                            materials.append({
                                'name': activity_name,
                                'url': href,
                                'type': 'file',
                                'extension': file_ext
                            })
                    
                    # 4. Page/Resource types that might contain downloadable files
                    # Syllabus, course info pages, etc. - treat as materials if they seem file-like
                    elif 'mod/page' in href or 'mod/ubboard' in href or 'mod/book' in href:
                        # Check if name suggests it's a file (contains file extensions or common file words)
                        activity_lower = activity_name.lower()
                        if any(keyword in activity_lower for keyword in ['syllabus', 'file', 'document', 'pdf', 'doc', 'ppt']):
                            # Try to extract extension from name
                            file_ext = self._extract_file_extension(activity_name, href)
                            materials.append({
                                'name': activity_name,
                                'url': href,
                                'type': 'file',
                                'extension': file_ext if file_ext else ''
                            })
                    
                    # 5. Any link with file extensions in URL or name should be considered
                    elif any(ext in href.lower() or ext in activity_name.lower() 
                            for ext in ['.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.zip', '.txt']):
                        file_ext = self._extract_file_extension(activity_name, href)
                        materials.append({
                            'name': activity_name,
                            'url': href,
                            'type': 'file',
                            'extension': file_ext if file_ext else ''
                        })
                
                # Only add section if it has materials or assignments
                if materials or assignments:
                    sections.append({
                        'title': section_title,
                        'materials': materials,
                        'assignments': assignments
                    })
            
            print(f"  ✓ Found {len(sections)} sections with materials/assignments")
            if professor:
                print(f"  ✓ Professor: {professor}")
            return {'sections': sections, 'professor': professor}
            
        except Exception as e:
            print(f"  ❌ Error parsing course content: {e}")
            return {'sections': [], 'professor': None, 'error': str(e)}
    
    def _extract_file_extension(self, filename: str, url: str) -> str:
        """
        Extract file extension from filename or URL.
        Handles common academic file types.
        """
        # Common file extensions to look for
        common_extensions = [
            '.pdf', '.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls',
            '.txt', '.md', '.zip', '.rar', '.7z',
            '.py', '.ipynb', '.r', '.rmd', '.c', '.cpp', '.h', '.java',
            '.html', '.css', '.js', '.json', '.xml',
            '.csv', '.dat', '.sql'
        ]
        
        # Check filename first
        filename_lower = filename.lower()
        for ext in common_extensions:
            if filename_lower.endswith(ext):
                return ext
        
        # Check URL
        url_lower = url.lower()
        for ext in common_extensions:
            if ext in url_lower:
                return ext
        
        return ''

    def extract_video_url(self, lecture: LectureInfo) -> Optional[str]:
        """Extract the actual mp4/m3u8 URL from the viewer page"""
        try:
            print(f"    → Extracting video URL from: {lecture.activity_url}")
            response = self.session.get(lecture.activity_url, timeout=30)
            response.raise_for_status()
            
            html_content = response.text
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Method 1: Look for source tags with m3u8 or mp4
            sources = soup.find_all('source', src=True)
            for source in sources:
                src = source.get('src', '')
                src_type = source.get('type', '')
                
                if '.m3u8' in src or 'mpegURL' in src_type or 'mpegurl' in src_type:
                    video_url = src if src.startswith('http') else f"https:{src}"
                    print(f"    ✓ Found m3u8 URL: {video_url}")
                    return video_url
                
                if src.endswith('.mp4') or 'mp4' in src_type:
                    video_url = src if src.startswith('http') else f"https:{src}"
                    print(f"    ✓ Found mp4 URL: {video_url}")
                    return video_url
            
            # Method 2: Look for video tags
            videos = soup.find_all('video', src=True)
            for video in videos:
                src = video.get('src', '')
                if src.endswith('.mp4') or src.endswith('.m3u8') or '.m3u8' in src:
                    video_url = src if src.startswith('http') else f"https:{src}"
                    print(f"    ✓ Found video tag URL: {video_url}")
                    return video_url
            
            # Method 3: Search HTML content for m3u8 URLs (regex)
            m3u8_match = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html_content, re.IGNORECASE)
            if m3u8_match:
                video_url = m3u8_match.group(1)
                print(f"    ✓ Found m3u8 URL (regex): {video_url}")
                return video_url
            
            # Method 4: Search for mp4 URLs in HTML
            mp4_match = re.search(r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)', html_content, re.IGNORECASE)
            if mp4_match:
                video_url = mp4_match.group(1)
                print(f"    ✓ Found mp4 URL (regex): {video_url}")
                return video_url
            
            # Method 5: Look for JavaScript variables that might contain video URLs
            # Common patterns: videoUrl, video_url, src, source, streamUrl
            js_patterns = [
                r'videoUrl["\']?\s*[:=]\s*["\']([^"\']+\.(?:m3u8|mp4))',
                r'video_url["\']?\s*[:=]\s*["\']([^"\']+\.(?:m3u8|mp4))',
                r'src["\']?\s*[:=]\s*["\']([^"\']+\.(?:m3u8|mp4))',
                r'streamUrl["\']?\s*[:=]\s*["\']([^"\']+\.(?:m3u8|mp4))',
            ]
            
            for pattern in js_patterns:
                match = re.search(pattern, html_content, re.IGNORECASE)
                if match:
                    video_url = match.group(1)
                    if not video_url.startswith('http'):
                        video_url = f"https:{video_url}" if video_url.startswith('//') else video_url
                    print(f"    ✓ Found video URL (JS variable): {video_url}")
                    return video_url
            
            print(f"    ❌ No video URL found in viewer page")
            return None
            
        except Exception as e:
            print(f"    ❌ Error extracting video URL: {e}")
            return None
    
    def download_file(self, url: str, path: str) -> bool:
        """
        Download a file from LearnUs to the specified path.
        Handles various file types including assignments, PDFs, code files, etc.
        """
        try:
            from pathlib import Path
            import os
            
            # Ensure path is a Path object
            save_path = Path(path)
            
            # Create parent directory if it doesn't exist
            save_path.parent.mkdir(parents=True, exist_ok=True)
            
            print(f"    → Downloading: {save_path.name}")
            
            # Make request with session to maintain authentication
            response = self.session.get(url, timeout=30, stream=True)
            response.raise_for_status()
            
            # Write file in chunks
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            file_size = save_path.stat().st_size
            print(f"    ✓ Downloaded: {save_path.name} ({file_size:,} bytes)")
            return True
            
        except Exception as e:
            print(f"    ❌ Download failed: {str(e)}")
            return False
    
    def parse_assignment_page(self, url: str) -> Dict:
        """
        Parse an assignment page to extract requirements, submissions, and description text.
        Returns a dict with 'requirements', 'submissions', and 'description' fields.
        """
        try:
            print(f"    → Parsing assignment page: {url}")
            response = self.session.get(url, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            requirements = []
            submissions = []
            description_text = ""
            
            # Extract assignment description text
            # Look for common description containers
            description_selectors = [
                'div.assignment-description',
                'div.description',
                'div[class*="intro"]',
                'div[class*="content"]',
                'div.generalbox',
                'div.box',
                'div#intro',
            ]
            
            for selector in description_selectors:
                desc_elem = soup.select_one(selector)
                if desc_elem:
                    # Remove script and style tags
                    for tag in desc_elem.find_all(['script', 'style', 'nav', 'header', 'footer']):
                        tag.decompose()
                    
                    # Get text content
                    text = desc_elem.get_text(separator='\n', strip=True)
                    if text and len(text) > 50:  # Only use if substantial content
                        description_text = text
                        break
            
            # If no description found, try to get main content area
            if not description_text:
                main_content = soup.find('div', {'role': 'main'}) or soup.find('main')
                if main_content:
                    for tag in main_content.find_all(['script', 'style', 'nav', 'header', 'footer']):
                        tag.decompose()
                    text = main_content.get_text(separator='\n', strip=True)
                    if text and len(text) > 50:
                        description_text = text
            
            # Find file links in the assignment description/requirements area
            # Look for actual downloadable files, not just any link
            file_link_patterns = [
                re.compile(r'(pluginfile\.php|mod/resource|mod/assign|forcedownload=1|download=1)', re.I),
            ]
            
            all_links = soup.find_all('a', href=True)
            seen_urls = set()
            
            for link in all_links:
                href = link.get('href', '')
                
                # Check if this looks like a file download link
                is_file_link = any(pattern.search(href) for pattern in file_link_patterns)
                
                # Also check for common file extensions in URL
                file_ext_in_url = re.search(r'\.(pdf|docx?|pptx?|xlsx?|zip|rar|py|r|c|cpp|java|txt|html|css|js)(?:\?|$)', href, re.I)
                
                if is_file_link or file_ext_in_url:
                    if not href.startswith('http'):
                        href = f"{LEARNUS_ORIGIN}{href}" if href.startswith('/') else f"{LEARNUS_ORIGIN}/{href}"
                    
                    # Skip if we've seen this URL
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)
                    
                    # Extract filename
                    filename = link.get_text(strip=True)
                    
                    # Clean up filename
                    if not filename or len(filename) > 200:
                        # Try to extract from URL
                        url_part = href.split('/')[-1].split('?')[0]
                        if url_part and '.' in url_part:
                            filename = url_part
                        else:
                            # Try to get from download parameter
                            match = re.search(r'[?&]file=(.+?)(?:&|$)', href)
                            if match:
                                filename = match.group(1)
                    
                    # Skip navigation/UI links
                    if filename.lower() in ['download', 'view', 'open', 'link', 'here', 'click']:
                        continue
                    
                    if filename and href and len(filename) < 250:
                        requirements.append({
                            'name': filename,
                            'url': href
                        })
            
            print(f"    ✓ Found {len(requirements)} requirement files, {len(submissions)} submissions")
            if description_text:
                print(f"    ✓ Extracted description ({len(description_text)} chars)")
            
            return {
                'requirements': requirements,
                'submissions': submissions,
                'description': description_text
            }
            
        except Exception as e:
            print(f"    ❌ Error parsing assignment page: {e}")
            return {'requirements': [], 'submissions': [], 'description': ''}
    
    def parse_folder_page(self, url: str) -> Dict:
        """
        Parse a folder page (mod/folder/view.php) to extract downloadable files.
        Follows folder structure and extracts actual file links, not HTML pages.
        Returns a dict with 'files' list and 'description' text.
        """
        try:
            print(f"    → Parsing folder page: {url}")
            response = self.session.get(url, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            files = []
            description_text = ""
            
            # Extract folder description/intro
            intro_elem = soup.select_one('div#intro, div.intro, div.box.generalbox')
            if intro_elem:
                for tag in intro_elem.find_all(['script', 'style']):
                    tag.decompose()
                description_text = intro_elem.get_text(separator='\n', strip=True)
            
            # Look for file manager tree structure (common in LearnUs folder pages)
            # Pattern 1: filemanager structure with fp-filename links
            file_manager = soup.select_one('div.filemanager, div.foldertree, div[id*="folder_tree"]')
            if file_manager:
                # Find all file links within the file manager
                # Files are typically in: span.fp-filename-icon > a with forcedownload=1
                file_links = file_manager.find_all('a', href=True)
                
                seen_urls = set()
                for link in file_links:
                    href = link.get('href', '')
                    
                    # Look for file download links (not folder navigation)
                    # Critical patterns: forcedownload=1, pluginfile.php, mod/resource
                    if any(pattern in href for pattern in ['forcedownload=1', 'pluginfile.php', 'mod/resource']):
                        if not href.startswith('http'):
                            href = f"{LEARNUS_ORIGIN}{href}" if href.startswith('/') else f"{LEARNUS_ORIGIN}/{href}"
                        
                        if href in seen_urls:
                            continue
                        seen_urls.add(href)
                        
                        # Extract filename - try multiple methods
                        filename = None
                        
                        # Method 1: Look for span.fp-filename (most reliable)
                        filename_elem = link.find('span', class_='fp-filename')
                        if filename_elem:
                            filename = filename_elem.get_text(strip=True)
                        
                        # Method 2: Get text from link itself
                        if not filename:
                            filename = link.get_text(strip=True)
                        
                        # Method 3: Extract from URL (fallback)
                        if not filename or len(filename) > 200:
                            # Try to get filename from URL parameters
                            match = re.search(r'[?&]file=(.+?)(?:&|$)', href)
                            if match:
                                filename = match.group(1)
                            else:
                                url_part = href.split('/')[-1].split('?')[0]
                                if url_part and '.' in url_part:
                                    filename = url_part
                        
                        # Clean filename
                        if filename:
                            # Remove any HTML entities or extra whitespace
                            filename = re.sub(r'\s+', ' ', filename).strip()
                            
                            # Skip if it looks like navigation text
                            if filename.lower() not in ['download', 'view', 'open', 'link', 'here', 'click', '']:
                                if len(filename) < 250:
                                    files.append({
                                        'name': filename,
                                        'url': href
                                    })
            
            # Pattern 2: Direct file links in content area
            if not files:
                # Look for links with file extensions or download indicators
                all_links = soup.find_all('a', href=re.compile(r'(forcedownload|pluginfile|mod/resource|\.(pdf|docx?|pptx?|zip|py|r|c|cpp))', re.I))
                
                seen_urls = set()
                for link in all_links:
                    href = link.get('href', '')
                    
                    if not href.startswith('http'):
                        href = f"{LEARNUS_ORIGIN}{href}" if href.startswith('/') else f"{LEARNUS_ORIGIN}/{href}"
                    
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)
                    
                    filename = link.get_text(strip=True)
                    if not filename or len(filename) > 200:
                        url_part = href.split('/')[-1].split('?')[0]
                        if url_part and '.' in url_part:
                            filename = url_part
                    
                    if filename and len(filename) < 250:
                        files.append({
                            'name': filename,
                            'url': href
                        })
            
            # Pattern 3: "Download folder" button - if it exists, we might need to handle it differently
            # But for now, we'll extract individual files
            
            print(f"    ✓ Found {len(files)} files in folder")
            if description_text:
                print(f"    ✓ Extracted description ({len(description_text)} chars)")
            
            return {
                'files': files,
                'description': description_text
            }
            
        except Exception as e:
            print(f"    ❌ Error parsing folder page: {e}")
            return {'files': [], 'description': ''}