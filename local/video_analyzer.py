"""
Video Analyzer Module
Detects frame changes and weights transcript words based on visual changes
"""

import cv2
import numpy as np
from pathlib import Path
import json
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
import re


class WeightedWord:
    """Represents a word with its importance weight"""
    def __init__(self, word: str, start: float, end: float, weight: float = 1.0):
        self.word = word
        self.start = start
        self.end = end
        self.weight = weight


class VideoAnalyzer:
    """Analyzes video to detect frame changes and weight transcript words"""
    
    def __init__(self, 
                 change_threshold: float = 0.85,
                 sample_interval: float = 0.5,
                 comparison_size: Tuple[int, int] = (160, 90),
                 weight_window: float = 3.0,
                 change_weight_multiplier: float = 3.0):
        """
        Args:
            change_threshold: Similarity below this = frame change detected (0-1)
            sample_interval: Sample frames every N seconds
            comparison_size: Resize frames to this size for faster comparison
            weight_window: Window (seconds) around frame change to apply weight boost
            change_weight_multiplier: How much to multiply weight for words during changes
        """
        self.change_threshold = change_threshold
        self.sample_interval = sample_interval
        self.comparison_size = comparison_size
        self.weight_window = weight_window
        self.change_weight_multiplier = change_weight_multiplier
    
    def detect_frame_changes(self, video_path: Path) -> List[float]:
        """
        Detect timestamps where significant frame changes occur.
        
        Returns:
            List of timestamps (in seconds) where frame changes were detected
        """
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        cap = cv2.VideoCapture(str(video_path))
        
        if not cap.isOpened():
            raise ValueError(f"Could not open video: {video_path}")
        
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0
        
        # Calculate frame interval for sampling
        frame_skip = max(1, int(fps * self.sample_interval))
        
        change_timestamps = []
        previous_frame = None
        frame_count = 0
        samples_checked = 0
        
        print(f"Detecting frame changes in video...")
        print(f"  Duration: {duration:.1f}s | FPS: {fps:.1f} | Total frames: {total_frames}")
        print(f"  Sample interval: {self.sample_interval}s (every {frame_skip} frames)")
        print(f"  Change threshold: {self.change_threshold}")
        
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            
            frame_count += 1
            
            # Skip frames for sampling
            if frame_count % frame_skip != 0:
                continue
            
            samples_checked += 1
            current_time = frame_count / fps
            
            # Progress update
            if samples_checked % 50 == 0:
                progress = (frame_count / total_frames) * 100 if total_frames > 0 else 0
                print(f"  Progress: {progress:.1f}% | Changes detected: {len(change_timestamps)}")
            
            # Resize and convert to grayscale for comparison
            small_frame = cv2.resize(frame, self.comparison_size)
            gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
            
            if previous_frame is not None:
                similarity = self._calculate_similarity(previous_frame, gray)
                
                # Frame change detected when similarity drops below threshold
                if similarity < self.change_threshold:
                    change_timestamps.append(current_time)
                    print(f"  ✓ Frame change at {current_time:.2f}s (similarity: {similarity:.3f})")
            
            previous_frame = gray
        
        cap.release()
        print(f"✓ Detected {len(change_timestamps)} frame changes (checked {samples_checked} samples)")
        return change_timestamps
    
    def _calculate_similarity(self, frame1: np.ndarray, frame2: np.ndarray) -> float:
        """Calculate similarity between two frames using mean absolute difference"""
        if frame1.shape != frame2.shape:
            return 0.0
        
        diff = cv2.absdiff(frame1, frame2)
        mean_diff = np.mean(diff) / 255.0
        return 1.0 - mean_diff
    
    def load_transcript_with_words(self, transcript_path: Path) -> List[Dict]:
        """
        Load transcript with word-level timestamps.
        
        Returns:
            List of word dicts with 'word', 'start', 'end' keys
        """
        if not transcript_path.exists():
            return []
        
        words = []
        
        try:
            with open(transcript_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # Whisper JSON format with segments
                if 'segments' in data:
                    for segment in data['segments']:
                        # Check if segment has word-level timestamps
                        if 'words' in segment:
                            for word_data in segment['words']:
                                words.append({
                                    'word': word_data.get('word', '').strip(),
                                    'start': word_data.get('start', 0),
                                    'end': word_data.get('end', 0)
                                })
                        else:
                            # Fallback: estimate word timings from segment
                            segment_text = segment.get('text', '').strip()
                            segment_start = segment.get('start', 0)
                            segment_end = segment.get('end', 0)
                            
                            segment_words = segment_text.split()
                            if segment_words:
                                duration_per_word = (segment_end - segment_start) / len(segment_words)
                                for i, word in enumerate(segment_words):
                                    words.append({
                                        'word': word,
                                        'start': segment_start + i * duration_per_word,
                                        'end': segment_start + (i + 1) * duration_per_word
                                    })
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Error parsing transcript JSON: {e}")
        
        return words
    
    def weight_transcript_by_changes(self, 
                                     transcript_words: List[Dict], 
                                     change_timestamps: List[float]) -> List[WeightedWord]:
        """
        Assign weights to transcript words based on proximity to frame changes.
        Words near frame changes get higher weights.
        
        Args:
            transcript_words: List of word dicts with 'word', 'start', 'end'
            change_timestamps: List of timestamps where frame changes occurred
        
        Returns:
            List of WeightedWord objects with weight assigned
        """
        weighted_words = []
        
        for word_data in transcript_words:
            word = word_data['word']
            start = word_data['start']
            end = word_data['end']
            word_mid = (start + end) / 2
            
            # Base weight
            weight = 1.0
            
            # Check proximity to any frame change
            for change_time in change_timestamps:
                # Calculate distance to this change
                distance = abs(word_mid - change_time)
                
                if distance <= self.weight_window:
                    # Word is within the weight window of a frame change
                    # Apply weight boost (stronger boost for closer words)
                    proximity_factor = 1.0 - (distance / self.weight_window)
                    boost = 1.0 + (self.change_weight_multiplier - 1.0) * proximity_factor
                    weight = max(weight, boost)  # Use maximum boost if near multiple changes
            
            weighted_words.append(WeightedWord(word, start, end, weight))
        
        return weighted_words
    
    def extract_important_keywords(self, 
                                   weighted_words: List[WeightedWord],
                                   min_weight: float = 1.5,
                                   context_words: int = 3) -> List[Dict]:
        """
        Extract important keywords/phrases based on weights.
        Groups consecutive high-weight words into phrases.
        
        Args:
            weighted_words: List of WeightedWord objects
            min_weight: Minimum weight to consider a word important
            context_words: Number of context words to include around important words
        
        Returns:
            List of important phrases with their timestamps and weights
        """
        important_phrases = []
        i = 0
        
        while i < len(weighted_words):
            word = weighted_words[i]
            
            if word.weight >= min_weight:
                # Found an important word, gather context
                start_idx = max(0, i - context_words)
                end_idx = i + 1
                
                # Extend to include consecutive important words
                while end_idx < len(weighted_words) and weighted_words[end_idx].weight >= min_weight:
                    end_idx += 1
                
                # Add trailing context
                end_idx = min(len(weighted_words), end_idx + context_words)
                
                # Build phrase
                phrase_words = weighted_words[start_idx:end_idx]
                phrase_text = ' '.join([w.word for w in phrase_words])
                phrase_start = phrase_words[0].start
                phrase_end = phrase_words[-1].end
                max_weight = max(w.weight for w in phrase_words)
                
                # Clean up phrase
                phrase_text = self._clean_phrase(phrase_text)
                
                if phrase_text.strip():
                    important_phrases.append({
                        'phrase': phrase_text,
                        'start': phrase_start,
                        'end': phrase_end,
                        'weight': max_weight
                    })
                
                # Skip to after this phrase
                i = end_idx
            else:
                i += 1
        
        # Remove duplicates and merge overlapping phrases
        important_phrases = self._merge_overlapping_phrases(important_phrases)
        
        return important_phrases
    
    def _clean_phrase(self, text: str) -> str:
        """Clean up a phrase text"""
        # Remove excessive whitespace
        text = ' '.join(text.split())
        # Remove leading/trailing punctuation
        text = text.strip('.,!?;:')
        return text
    
    def _merge_overlapping_phrases(self, phrases: List[Dict]) -> List[Dict]:
        """Merge phrases that overlap in time"""
        if not phrases:
            return []
        
        # Sort by start time
        sorted_phrases = sorted(phrases, key=lambda x: x['start'])
        merged = [sorted_phrases[0]]
        
        for phrase in sorted_phrases[1:]:
            last = merged[-1]
            
            # Check if overlapping
            if phrase['start'] <= last['end'] + 0.5:  # 0.5s tolerance
                # Merge phrases
                last['phrase'] = f"{last['phrase']} ... {phrase['phrase']}"
                last['end'] = max(last['end'], phrase['end'])
                last['weight'] = max(last['weight'], phrase['weight'])
            else:
                merged.append(phrase)
        
        return merged
    
    def analyze_video(self, video_path: Path, transcript_path: Path, output_dir: Path) -> Dict:
        """
        Full analysis pipeline:
        1. Detect frame changes in video
        2. Load transcript with word timestamps
        3. Weight words based on proximity to frame changes
        4. Extract important keywords/phrases
        5. Save results
        
        Args:
            video_path: Path to video file
            transcript_path: Path to transcript JSON file
            output_dir: Output directory for results
        
        Returns:
            Dict with analysis results
        """
        print(f"\n{'='*60}")
        print(f"Video Frame Change Analysis")
        print(f"{'='*60}")
        print(f"Video: {video_path.name}")
        print(f"{'='*60}\n")
        
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Step 1: Detect frame changes
        print("\n[Step 1] Detecting frame changes...")
        change_timestamps = self.detect_frame_changes(video_path)
        
        if not change_timestamps:
            print("No significant frame changes detected.")
            return {'change_timestamps': [], 'weighted_words': [], 'important_phrases': []}
        
        # Step 2: Load transcript
        print("\n[Step 2] Loading transcript with word timestamps...")
        transcript_words = self.load_transcript_with_words(transcript_path)
        
        if not transcript_words:
            print("No transcript words found. Make sure transcript has word-level timestamps.")
            # Save just the change timestamps
            result = {
                'change_timestamps': change_timestamps,
                'total_changes': len(change_timestamps),
                'weighted_words': [],
                'important_phrases': []
            }
            self._save_results(result, output_dir)
            return result
        
        print(f"  Loaded {len(transcript_words)} words from transcript")
        
        # Step 3: Weight words by frame changes
        print("\n[Step 3] Weighting words by proximity to frame changes...")
        weighted_words = self.weight_transcript_by_changes(transcript_words, change_timestamps)
        
        high_weight_count = sum(1 for w in weighted_words if w.weight > 1.0)
        print(f"  {high_weight_count} words have elevated weights")
        
        # Step 4: Extract important keywords/phrases
        print("\n[Step 4] Extracting important keywords/phrases...")
        important_phrases = self.extract_important_keywords(weighted_words)
        print(f"  Found {len(important_phrases)} important phrases")
        
        # Prepare results
        result = {
            'video': str(video_path.name),
            'change_timestamps': change_timestamps,
            'total_changes': len(change_timestamps),
            'total_words': len(weighted_words),
            'important_phrases': important_phrases,
            'weighted_words': [
                {
                    'word': w.word,
                    'start': w.start,
                    'end': w.end,
                    'weight': round(w.weight, 2)
                }
                for w in weighted_words
            ],
            'settings': {
                'change_threshold': self.change_threshold,
                'sample_interval': self.sample_interval,
                'weight_window': self.weight_window,
                'change_weight_multiplier': self.change_weight_multiplier
            }
        }
        
        # Step 5: Save results
        self._save_results(result, output_dir)
        
        print(f"\n{'='*60}")
        print(f"Analysis Complete")
        print(f"  Frame changes: {len(change_timestamps)}")
        print(f"  Important phrases: {len(important_phrases)}")
        print(f"{'='*60}\n")
        
        return result
    
    def _save_results(self, result: Dict, output_dir: Path):
        """Save analysis results to files"""
        # Save full JSON results
        json_path = output_dir / "frame_change_analysis.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n  Full results saved to: {json_path}")
        
        # Save human-readable summary
        summary_path = output_dir / "important_content.txt"
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write("=" * 60 + "\n")
            f.write("IMPORTANT CONTENT (Based on Frame Changes)\n")
            f.write("=" * 60 + "\n\n")
            
            f.write(f"Total frame changes detected: {result.get('total_changes', 0)}\n")
            f.write(f"Total words analyzed: {result.get('total_words', 0)}\n\n")
            
            f.write("-" * 60 + "\n")
            f.write("KEY PHRASES (words spoken during visual changes)\n")
            f.write("-" * 60 + "\n\n")
            
            for i, phrase in enumerate(result.get('important_phrases', []), 1):
                timestamp = phrase['start']
                minutes = int(timestamp // 60)
                seconds = timestamp % 60
                f.write(f"[{minutes:02d}:{seconds:05.2f}] (weight: {phrase['weight']:.1f})\n")
                f.write(f"  \"{phrase['phrase']}\"\n\n")
        
        print(f"  Summary saved to: {summary_path}")
        
        # Save weighted transcript (full text with weights marked)
        weighted_path = output_dir / "weighted_transcript.txt"
        with open(weighted_path, 'w', encoding='utf-8') as f:
            f.write("WEIGHTED TRANSCRIPT\n")
            f.write("Words in [BRACKETS] have high importance (spoken during frame changes)\n")
            f.write("=" * 60 + "\n\n")
            
            current_line = []
            for word_data in result.get('weighted_words', []):
                word = word_data['word']
                weight = word_data['weight']
                
                if weight > 1.5:
                    current_line.append(f"[{word}]")
                else:
                    current_line.append(word)
                
                # Line break every ~80 chars
                if len(' '.join(current_line)) > 80:
                    f.write(' '.join(current_line) + '\n')
                    current_line = []
            
            if current_line:
                f.write(' '.join(current_line) + '\n')
        
        print(f"  Weighted transcript saved to: {weighted_path}")
