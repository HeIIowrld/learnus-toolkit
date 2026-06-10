"""
Whisper transcription module for lecture videos
"""
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from learnus.utils import find_ffmpeg


NPU_EXECUTION_PROVIDERS = {
    'VitisAIExecutionProvider',
    'QNNExecutionProvider'
}


def get_transcription_environment() -> dict:
    """Inspect local transcription runtime availability."""
    info = {
        'backend_requested': (os.getenv('WHISPER_BACKEND') or 'auto').strip().lower() or 'auto',
        'backend_active': 'openai',
        'backend_reason': '기본 OpenAI Whisper 경로를 사용합니다.',
        'whisper_installed': False,
        'onnxruntime_installed': False,
        'onnxruntime_providers': [],
        'npu_provider_available': False,
        'npu_provider_names': [],
    }

    try:
        import whisper  # noqa: F401
        info['whisper_installed'] = True
    except Exception:
        info['whisper_installed'] = False

    if importlib.util.find_spec('onnxruntime'):
        try:
            import onnxruntime as ort
            providers = ort.get_available_providers()
            npu_providers = [provider for provider in providers if provider in NPU_EXECUTION_PROVIDERS]
            info['onnxruntime_installed'] = True
            info['onnxruntime_providers'] = providers
            info['npu_provider_available'] = bool(npu_providers)
            info['npu_provider_names'] = npu_providers
        except Exception as exc:
            info['onnxruntime_installed'] = True
            info['backend_reason'] = f'ONNX Runtime 감지 중 오류: {exc}'

    requested_backend = info['backend_requested']
    if requested_backend == 'onnx':
        if info['onnxruntime_installed'] and info['npu_provider_available']:
            info['backend_active'] = 'onnx'
            info['backend_reason'] = 'ONNX Runtime의 NPU 실행 공급자가 감지되었습니다.'
        elif info['onnxruntime_installed']:
            info['backend_reason'] = (
                'ONNX Runtime은 설치되어 있지만 사용할 수 있는 NPU 실행 공급자가 없습니다. '
                'OpenAI Whisper로 폴백합니다.'
            )
        else:
            info['backend_reason'] = (
                'ONNX Runtime이 설치되어 있지 않아 OpenAI Whisper로 폴백합니다.'
            )
    elif requested_backend in {'auto', 'npu'}:
        if info['onnxruntime_installed'] and info['npu_provider_available']:
            info['backend_active'] = 'onnx'
            info['backend_reason'] = 'NPU 실행 공급자를 감지해 ONNX 백엔드를 우선 선택했습니다.'
        else:
            info['backend_reason'] = (
                'NPU 실행 공급자가 보이지 않아 OpenAI Whisper를 사용합니다.'
            )
    else:
        info['backend_active'] = 'openai'

    return info


class WhisperTranscriber:
    """Handles transcription using OpenAI Whisper"""
    
    def __init__(self, model: Optional[str] = None):
        """
        Initialize Whisper transcriber
        
        Args:
            model: Whisper model size (tiny, base, small, medium, large)
        """
        self.model = model or (os.getenv('WHISPER_MODEL') or 'medium').strip()
        self.last_error = None
        self.default_language = self._get_default_language()
        self.runtime_info = get_transcription_environment()
        self.backend = self.runtime_info['backend_active']
        self.backend_reason = self.runtime_info['backend_reason']
        self._check_whisper_installed()

    def _get_default_language(self) -> Optional[str]:
        """Return the preferred Whisper language. None enables auto-detection."""
        raw_language = (os.getenv('WHISPER_LANGUAGE') or '').strip().lower()

        if not raw_language:
            return None

        if raw_language in {'auto', 'mixed', 'multilingual'}:
            return None

        if ',' in raw_language or '/' in raw_language:
            return None

        return raw_language
    
    def _check_whisper_installed(self):
        """Check if whisper is installed"""
        try:
            self.last_error = None
            import whisper
            self.whisper_available = True
        except ImportError:
            self.whisper_available = False
            print("Warning: OpenAI Whisper not installed. Install: pip install openai-whisper")

    def _warn_if_backend_fell_back(self):
        """Log when NPU/ONNX was requested but OpenAI Whisper is active."""
        if self.backend != 'openai':
            self.last_error = (
                'ONNX/NPU backend is not implemented yet in this project. '
                'Falling back to OpenAI Whisper.'
            )
            print(self.last_error)
            self.backend = 'openai'

        if self.runtime_info.get('backend_requested') in {'onnx', 'auto', 'npu'}:
            print(f"Transcription backend: {self.backend} ({self.backend_reason})")

    def _ensure_ffmpeg_on_path(self) -> bool:
        """Make the detected ffmpeg executable visible to OpenAI Whisper."""
        ffmpeg_cmd = find_ffmpeg()
        if not ffmpeg_cmd:
            self.last_error = (
                "ffmpeg not found. Install ffmpeg or set FFMPEG_PATH, "
                "then restart the app. Whisper transcription requires ffmpeg."
            )
            print(self.last_error)
            return False

        ffmpeg_path = Path(ffmpeg_cmd).resolve()
        ffmpeg_dir_path = ffmpeg_path.parent

        if os.name == 'nt' and ffmpeg_path.name.lower() != 'ffmpeg.exe':
            shim_dir = Path(__file__).resolve().parents[2] / '.runtime-bin'
            shim_dir.mkdir(exist_ok=True)
            shim_path = shim_dir / 'ffmpeg.exe'
            if not shim_path.exists() or shim_path.stat().st_size != ffmpeg_path.stat().st_size:
                try:
                    if shim_path.exists():
                        shim_path.unlink()
                    os.link(ffmpeg_path, shim_path)
                except OSError:
                    shutil.copy2(ffmpeg_path, shim_path)
            ffmpeg_dir_path = shim_dir

        ffmpeg_dir = str(ffmpeg_dir_path)
        path_entries = os.environ.get('PATH', '').split(os.pathsep)
        normalized_entries = {str(Path(entry).resolve()).lower() for entry in path_entries if entry}
        if ffmpeg_dir.lower() not in normalized_entries:
            os.environ['PATH'] = ffmpeg_dir + os.pathsep + os.environ.get('PATH', '')

        return True
    
    def transcribe_video(self, video_path: Path, output_path: Optional[Path] = None,
                        language: Optional[str] = None) -> Optional[Path]:
        """
        Transcribe a video file using Whisper
        
        Args:
            video_path: Path to video file
            output_path: Optional path for transcript file (default: video_path with .txt extension)
            language: Language code. None uses Whisper auto-detection.
            
        Returns:
            Path to transcript file or None if failed
        """
        if not self.whisper_available:
            print("Whisper is not available. Please install it first.")
            return None
        
        if not video_path.exists():
            print(f"Video file not found: {video_path}")
            return None
        
        try:
            self._warn_if_backend_fell_back()
            if not self._ensure_ffmpeg_on_path():
                return None
            import whisper
            
            # Load model
            print(f"Loading Whisper model: {self.model}")
            model = whisper.load_model(self.model)
            language = self.default_language if language is None else language
            
            # Transcribe
            print(f"Transcribing video: {video_path.name}")
            transcribe_kwargs = {
                'task': 'transcribe'
            }
            if language:
                transcribe_kwargs['language'] = language

            result = model.transcribe(str(video_path), **transcribe_kwargs)
            
            # Determine output path
            if output_path is None:
                output_path = video_path.with_suffix('.txt')
            
            # Save transcript
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(result['text'])
            
            # Also save with timestamps if available
            srt_path = output_path.with_suffix('.srt')
            json_path = output_path.with_suffix('.json')
            if 'segments' in result:
                self._save_srt(result['segments'], srt_path)
                # Save JSON with full result for video analysis
                import json
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)
            
            print(f"Transcript saved: {output_path}")
            detected_language = result.get('language')
            if detected_language:
                print(f"Detected language: {detected_language}")
            if srt_path.exists():
                print(f"SRT file saved: {srt_path}")
            if json_path.exists():
                print(f"JSON transcript saved: {json_path}")
            
            return output_path
            
        except Exception as e:
            self.last_error = str(e)
            print(f"Error transcribing video: {e}")
            return None
    
    def _save_srt(self, segments: list, output_path: Path):
        """Save transcript as SRT subtitle file"""
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                for i, segment in enumerate(segments, 1):
                    start = self._format_timestamp(segment['start'])
                    end = self._format_timestamp(segment['end'])
                    text = segment['text'].strip()
                    
                    f.write(f"{i}\n")
                    f.write(f"{start} --> {end}\n")
                    f.write(f"{text}\n\n")
        except Exception as e:
            print(f"Error saving SRT: {e}")
    
    def _format_timestamp(self, seconds: float) -> str:
        """Format seconds to SRT timestamp format (HH:MM:SS,mmm)"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    
    def extract_audio(self, video_path: Path, audio_path: Optional[Path] = None) -> Optional[Path]:
        """
        Extract audio from video file using ffmpeg
        
        Args:
            video_path: Path to video file
            audio_path: Optional path for audio file (default: video_path with .wav extension)
            
        Returns:
            Path to audio file or None if failed
        """
        if not video_path.exists():
            print(f"Video file not found: {video_path}")
            return None
        
        try:
            ffmpeg_cmd = find_ffmpeg()
            if not ffmpeg_cmd:
                self.last_error = (
                    "ffmpeg not found. Install ffmpeg or set FFMPEG_PATH, "
                    "then restart the app. Audio extraction requires ffmpeg."
                )
                print(self.last_error)
                return None
            
            # Determine output path
            if audio_path is None:
                audio_path = video_path.with_suffix('.wav')
            
            # Extract audio using ffmpeg
            cmd = [
                ffmpeg_cmd,
                '-i', str(video_path),
                '-vn',  # No video
                '-acodec', 'pcm_s16le',  # WAV format
                '-ar', '16000',  # 16kHz sample rate (good for speech)
                '-ac', '1',  # Mono
                str(audio_path),
                '-y'  # Overwrite
            ]
            
            process = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            if process.returncode == 0:
                print(f"Audio extracted: {audio_path}")
                return audio_path
            else:
                self.last_error = f"ffmpeg error: {process.stderr}"
                print(f"ffmpeg error: {process.stderr}")
                return None
                
        except Exception as e:
            self.last_error = str(e)
            print(f"Error extracting audio: {e}")
            return None
    
    def transcribe_audio(self, audio_path: Path, output_path: Optional[Path] = None,
                        language: Optional[str] = None) -> Optional[Path]:
        """
        Transcribe an audio file using Whisper
        
        Args:
            audio_path: Path to audio file
            output_path: Optional path for transcript file
            language: Language code. None uses Whisper auto-detection.
            
        Returns:
            Path to transcript file or None if failed
        """
        if not self.whisper_available:
            print("Whisper is not available. Please install it first.")
            return None
        
        if not audio_path.exists():
            print(f"Audio file not found: {audio_path}")
            return None
        
        try:
            self.last_error = None
            self._warn_if_backend_fell_back()
            if not self._ensure_ffmpeg_on_path():
                return None
            import whisper
            
            # Load model
            print(f"Loading Whisper model: {self.model}")
            model = whisper.load_model(self.model)
            language = self.default_language if language is None else language
            
            # Transcribe
            print(f"Transcribing audio: {audio_path.name}")
            transcribe_kwargs = {
                'task': 'transcribe'
            }
            if language:
                transcribe_kwargs['language'] = language

            result = model.transcribe(str(audio_path), **transcribe_kwargs)
            
            # Determine output path
            if output_path is None:
                output_path = audio_path.with_suffix('.txt')
            
            # Save transcript
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(result['text'])
            
            # Also save with timestamps if available
            srt_path = output_path.with_suffix('.srt')
            json_path = output_path.with_suffix('.json')
            if 'segments' in result:
                self._save_srt(result['segments'], srt_path)
                # Save JSON with full result for video analysis
                import json
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)
            
            print(f"Transcript saved: {output_path}")
            detected_language = result.get('language')
            if detected_language:
                print(f"Detected language: {detected_language}")
            if srt_path.exists():
                print(f"SRT file saved: {srt_path}")
            if json_path.exists():
                print(f"JSON transcript saved: {json_path}")
            
            return output_path
            
        except Exception as e:
            self.last_error = str(e)
            print(f"Error transcribing audio: {e}")
            return None

