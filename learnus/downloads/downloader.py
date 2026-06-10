"""
Download manager for lecture videos
"""
import os
import requests
import re
from pathlib import Path
from typing import Callable, Optional
from tqdm import tqdm
import subprocess
from learnus.utils import sanitize_filename, find_ffmpeg, normalize_semester_name


class VideoDownloader:
    """Handles downloading video files"""
    
    def __init__(self, download_dir: str = "downloads"):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(exist_ok=True)
        self.last_error = None
    
    
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
        # Sanitize and shorten all components. OneDrive and Windows paths fail
        # quickly with long course names, so keep the generated path conservative.
        year_clean = _shorten_component(sanitize_filename(year) if year else "Unknown", 40)
        semester_clean = _shorten_component(sanitize_filename(normalize_semester_name(semester)), 40)
        course_clean = _shorten_component(sanitize_filename(course_name), 80)
        week_clean = _shorten_component(sanitize_filename(week), 70)
        title_clean = _shorten_component(sanitize_filename(title), 120)
        
        output_path = self.download_dir / year_clean / semester_clean / course_clean / week_clean / f"{title_clean}.{extension}"
        output_path = _fit_output_path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return output_path
    
    def download_video(
        self,
        video_url: str,
        output_path: Path,
        session: requests.Session,
        progress_callback: Optional[Callable[[int], None]] = None
    ) -> bool:
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
            self.last_error = None
            # Check if file already exists
            if output_path.exists():
                print(f"File already exists: {output_path}")
                if progress_callback:
                    progress_callback(100)
                return True
            
            # Handle m3u8 (HLS) streams
            if video_url.endswith('.m3u8') or 'm3u8' in video_url:
                return self._download_hls(video_url, output_path, session, progress_callback=progress_callback)
            
            # Regular HTTP download
            response = session.get(video_url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0
            
            with open(output_path, 'wb') as f:
                with tqdm(total=total_size, unit='B', unit_scale=True, desc=output_path.name) as pbar:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            pbar.update(len(chunk))
                            downloaded_size += len(chunk)
                            if progress_callback and total_size > 0:
                                progress_callback(min(100, int(downloaded_size * 100 / total_size)))
            
            if progress_callback:
                progress_callback(100)
            print(f"Downloaded: {output_path}")
            return True
            
        except Exception as e:
            self.last_error = str(e)
            print(f"Error downloading video: {e}")
            if output_path.exists():
                output_path.unlink()  # Remove partial file
            return False
    
    def _download_hls(
        self,
        m3u8_url: str,
        output_path: Path,
        session: requests.Session,
        progress_callback: Optional[Callable[[int], None]] = None
    ) -> bool:
        """
        Download HLS stream using ffmpeg
        
        Args:
            m3u8_url: URL to the m3u8 playlist
            output_path: Output file path
            session: Authenticated session (for cookies)
            
        Returns:
            True if successful, False otherwise
        """
        def estimate_hls_duration_seconds(url: str) -> Optional[float]:
            try:
                response = requests.get(url, timeout=20)
                response.raise_for_status()
                durations = re.findall(r'#EXTINF:([0-9.]+)', response.text)
                if not durations:
                    return None
                return sum(float(value) for value in durations)
            except Exception:
                return None

        def cleanup_partial_file():
            try:
                if output_path.exists():
                    output_path.unlink()
            except Exception:
                pass

        try:
            ffmpeg_cmd = find_ffmpeg()
            if not ffmpeg_cmd:
                self.last_error = (
                    "ffmpeg not found. Install ffmpeg or set FFMPEG_PATH, "
                    "then restart the app. HLS (.m3u8) downloads require ffmpeg."
                )
                print(self.last_error)
                print("Alternatively, you can manually download the m3u8 URL.")
                return False
            
            # Get cookies from session
            cookies = session.cookies.get_dict()
            cookie_str = '; '.join([f"{k}={v}" for k, v in cookies.items()])
            header_lines = [
                f"Cookie: {cookie_str}",
                "User-Agent: Mozilla/5.0",
                f"Referer: {m3u8_url}"
            ]
            header_blob = "\r\n".join(line for line in header_lines if line.strip()) + "\r\n"
            
            # Build ffmpeg command
            # Note: ffmpeg needs the cookies passed via headers
            # We'll use a workaround by creating a temporary cookie file
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as cookie_file:
                for name, value in cookies.items():
                    cookie_file.write(f"{name}={value}\n")
                cookie_file_path = cookie_file.name
            
            try:
                duration_seconds = estimate_hls_duration_seconds(m3u8_url)

                # Use ffmpeg to download and convert HLS stream
                cmd = [
                    ffmpeg_cmd,
                    '-y',
                    '-loglevel', 'error',
                    '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
                    '-allowed_extensions', 'ALL',
                    '-headers', header_blob,
                    '-i', m3u8_url,
                    '-progress', 'pipe:1',
                    '-nostats',
                    '-c', 'copy',
                    '-bsf:a', 'aac_adtstoasc',
                    str(output_path),
                ]
                
                if progress_callback:
                    progress_callback(5)

                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    bufsize=1
                )
                
                print(f"Downloading HLS stream (this may take a while)...")
                stderr_lines = []
                fallback_progress = 5

                while True:
                    if process.stdout is None:
                        break

                    line = process.stdout.readline()
                    if not line:
                        if process.poll() is not None:
                            break
                        continue

                    line = line.strip()
                    if not line:
                        continue

                    if line.startswith('out_time_ms=') and progress_callback and duration_seconds:
                        out_time_ms = int(line.split('=', 1)[1] or '0')
                        current_seconds = out_time_ms / 1_000_000
                        progress_callback(min(99, int(current_seconds * 100 / duration_seconds)))
                    elif line.startswith('out_time_ms=') and progress_callback:
                        fallback_progress = min(95, fallback_progress + 1)
                        progress_callback(fallback_progress)

                if process.stderr is not None:
                    stderr_output = process.stderr.read()
                    stderr_lines.append(stderr_output)

                if duration_seconds and progress_callback:
                    progress_callback(99)

                process.wait()
                
                if process.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                    if progress_callback:
                        progress_callback(100)
                    print(f"Downloaded: {output_path}")
                    return True
                else:
                    stderr = ''.join(stderr_lines)
                    self.last_error = f"ffmpeg error: {stderr}"
                    print(f"ffmpeg error: {stderr}")
                    cleanup_partial_file()
                    return False
                    
            finally:
                # Clean up cookie file
                try:
                    os.unlink(cookie_file_path)
                except:
                    pass
                    
        except Exception as e:
            self.last_error = str(e)
            print(f"Error downloading HLS stream: {e}")
            cleanup_partial_file()
            return False


def _shorten_component(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip(". _-") or "file"


def _fit_output_path(path: Path, limit: int = 240) -> Path:
    if len(str(path)) <= limit:
        return path

    parts = list(path.parts)
    if len(parts) < 5:
        return _shorten_filename_path(path, len(str(path)) - limit)

    # Path layout: <base...>/year/semester/course/week/file.mp4.
    file_index = len(parts) - 1
    week_index = len(parts) - 2
    course_index = len(parts) - 3

    for index, minimum in ((file_index, 24), (week_index, 24), (course_index, 32)):
        overflow = len(str(Path(*parts))) - limit
        if overflow <= 0:
            break
        current = parts[index]
        suffix = Path(current).suffix if index == file_index else ""
        stem = Path(current).stem if index == file_index else current
        keep = max(minimum, len(stem) - overflow - 8)
        parts[index] = f"{stem[:keep].rstrip('. _-')}{suffix}" or "file"

    fitted = Path(*parts)
    if len(str(fitted)) <= limit:
        return fitted
    return _shorten_filename_path(fitted, len(str(fitted)) - limit)


def _shorten_filename_path(path: Path, overflow: int) -> Path:
    suffix = path.suffix
    keep = max(24, len(path.stem) - overflow - 8)
    return path.with_name(f"{path.stem[:keep].rstrip('. _-')}{suffix}")
