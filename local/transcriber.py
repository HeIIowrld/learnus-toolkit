"""
Whisper transcription module for lecture videos
"""
import os
import subprocess
from pathlib import Path
from typing import Optional
import tempfile


class WhisperTranscriber:
    """Handles transcription using OpenAI Whisper"""
    
    def __init__(self, model: str = "base"):
        """
        Initialize Whisper transcriber
        
        Args:
            model: Whisper model size (tiny, base, small, medium, large)
        """
        self.model = model
        self._check_whisper_installed()
    
    def _check_whisper_installed(self):
        """Check if whisper is installed"""
        try:
            import whisper
            self.whisper_available = True
        except ImportError:
            self.whisper_available = False
            print("Warning: OpenAI Whisper not installed. Install: pip install openai-whisper")
    
    def transcribe_video(self, video_path: Path, output_path: Optional[Path] = None, 
                        language: Optional[str] = "ko") -> Optional[Path]:
        """
        Transcribe a video file using Whisper
        
        Args:
            video_path: Path to video file
            output_path: Optional path for transcript file (default: video_path with .txt extension)
            language: Language code (default: "ko" for Korean)
            
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
            import whisper
            
            # Load model
            print(f"Loading Whisper model: {self.model}")
            model = whisper.load_model(self.model)
            
            # Transcribe
            print(f"Transcribing video: {video_path.name}")
            result = model.transcribe(
                str(video_path),
                language=language,
                task="transcribe"
            )
            
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
            if srt_path.exists():
                print(f"SRT file saved: {srt_path}")
            if json_path.exists():
                print(f"JSON transcript saved: {json_path}")
            
            return output_path
            
        except Exception as e:
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
            # Check if ffmpeg is available
            try:
                subprocess.run(['ffmpeg', '-version'], 
                             capture_output=True, 
                             check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                print("ffmpeg not found. Please install ffmpeg to extract audio.")
                return None
            
            # Determine output path
            if audio_path is None:
                audio_path = video_path.with_suffix('.wav')
            
            # Extract audio using ffmpeg
            cmd = [
                'ffmpeg',
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
                print(f"ffmpeg error: {process.stderr}")
                return None
                
        except Exception as e:
            print(f"Error extracting audio: {e}")
            return None
    
    def transcribe_audio(self, audio_path: Path, output_path: Optional[Path] = None, 
                        language: Optional[str] = "ko") -> Optional[Path]:
        """
        Transcribe an audio file using Whisper
        
        Args:
            audio_path: Path to audio file
            output_path: Optional path for transcript file
            language: Language code (default: "ko" for Korean)
            
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
            import whisper
            
            # Load model
            print(f"Loading Whisper model: {self.model}")
            model = whisper.load_model(self.model)
            
            # Transcribe
            print(f"Transcribing audio: {audio_path.name}")
            result = model.transcribe(
                str(audio_path),
                language=language,
                task="transcribe"
            )
            
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
            if srt_path.exists():
                print(f"SRT file saved: {srt_path}")
            if json_path.exists():
                print(f"JSON transcript saved: {json_path}")
            
            return output_path
            
        except Exception as e:
            print(f"Error transcribing audio: {e}")
            return None

