"""
Download manager for lecture videos
"""
import os
import requests
from pathlib import Path
from typing import Optional
from tqdm import tqdm
import subprocess
from utils import sanitize_filename


class VideoDownloader:
    """Handles downloading video files"""
    
    def __init__(self, download_dir: str = "downloads"):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(exist_ok=True)
    
    
    def get_output_path(self, year: str, semester: str, course_name: str, week: str, title: str, extension: str = "mp4") -> Path:
        """
        Generate output file path in year-semester-course-week format
        
        Args:
            year: Year (e.g., "2025")
            semester: Semester (e.g., "2학기" or "2")
            course_name: Course name
            week: Week information
            title: Video title
            extension: File extension
            
        Returns:
            Path object for the output file
        """
        # Sanitize all components
        year_clean = sanitize_filename(year) if year else "Unknown"
        semester_clean = sanitize_filename(semester) if semester else "Unknown"
        course_clean = sanitize_filename(course_name)
        week_clean = sanitize_filename(week)
        title_clean = sanitize_filename(title)
        
        # Create directory structure: year-semester-course-week
        week_dir = self.download_dir / year_clean / semester_clean / course_clean / week_clean
        week_dir.mkdir(parents=True, exist_ok=True)
        
        # Filename: Title.extension
        filename = f"{title_clean}.{extension}"
        return week_dir / filename
    
    def download_video(self, video_url: str, output_path: Path, session: requests.Session) -> bool:
        """
        Download video file
        
        Args:
            video_url: URL of the video file
            output_path: Path where to save the file
            session: Authenticated requests session
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Check if file already exists
            if output_path.exists():
                print(f"File already exists: {output_path}")
                return True
            
            # Handle m3u8 (HLS) streams
            if video_url.endswith('.m3u8') or 'm3u8' in video_url:
                return self._download_hls(video_url, output_path, session)
            
            # Regular HTTP download
            response = session.get(video_url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            
            with open(output_path, 'wb') as f:
                with tqdm(total=total_size, unit='B', unit_scale=True, desc=output_path.name) as pbar:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            pbar.update(len(chunk))
            
            print(f"Downloaded: {output_path}")
            return True
            
        except Exception as e:
            print(f"Error downloading video: {e}")
            if output_path.exists():
                output_path.unlink()  # Remove partial file
            return False
    
    def _download_hls(self, m3u8_url: str, output_path: Path, session: requests.Session) -> bool:
        """
        Download HLS stream using ffmpeg
        
        Args:
            m3u8_url: URL to the m3u8 playlist
            output_path: Output file path
            session: Authenticated session (for cookies)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Check if ffmpeg is available
            try:
                subprocess.run(['ffmpeg', '-version'], 
                             capture_output=True, 
                             check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                print("ffmpeg not found. Please install ffmpeg to download HLS streams.")
                print("Alternatively, you can manually download the m3u8 URL.")
                return False
            
            # Get cookies from session
            cookies = session.cookies.get_dict()
            cookie_str = '; '.join([f"{k}={v}" for k, v in cookies.items()])
            
            # Build ffmpeg command
            # Note: ffmpeg needs the cookies passed via headers
            # We'll use a workaround by creating a temporary cookie file
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as cookie_file:
                for name, value in cookies.items():
                    cookie_file.write(f"{name}={value}\n")
                cookie_file_path = cookie_file.name
            
            try:
                # Use ffmpeg to download and convert HLS stream
                cmd = [
                    'ffmpeg',
                    '-headers', f'Cookie: {cookie_str}',
                    '-i', m3u8_url,
                    '-c', 'copy',
                    '-bsf:a', 'aac_adtstoasc',
                    str(output_path),
                    '-y'  # Overwrite output file
                ]
                
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True
                )
                
                # Monitor progress (basic)
                print(f"Downloading HLS stream (this may take a while)...")
                stdout, stderr = process.communicate()
                
                if process.returncode == 0:
                    print(f"Downloaded: {output_path}")
                    return True
                else:
                    print(f"ffmpeg error: {stderr}")
                    return False
                    
            finally:
                # Clean up cookie file
                try:
                    os.unlink(cookie_file_path)
                except:
                    pass
                    
        except Exception as e:
            print(f"Error downloading HLS stream: {e}")
            return False

