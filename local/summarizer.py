"""
Summarization module using Ollama or LM Studio
"""
import os
import requests
import json
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Summarizer:
    """Handles text summarization using Ollama or LM Studio"""
    
    def __init__(self, provider: str = None, base_url: Optional[str] = None, model: str = None):
        """
        Initialize summarizer
        
        Args:
            provider: "ollama" or "lmstudio" (default: from SUMMARIZER_PROVIDER env var or "ollama")
            base_url: Base URL for the API (default: from SUMMARIZER_BASE_URL env var or localhost)
            model: Model name to use (default: from SUMMARIZER_MODEL env var or "llama2")
        """
        # Get values from environment variables if not provided
        self.provider = (provider or os.getenv('SUMMARIZER_PROVIDER', 'ollama')).lower()
        self.model = model or os.getenv('SUMMARIZER_MODEL', 'llama2')
        
        if base_url:
            self.base_url = base_url
        else:
            env_url = os.getenv('SUMMARIZER_BASE_URL')
            if env_url:
                self.base_url = env_url
            elif self.provider == "ollama":
                self.base_url = "http://localhost:11434"
            elif self.provider == "lmstudio":
                self.base_url = "http://localhost:1234"
            else:
                self.base_url = "http://localhost:11434"
    
    def summarize(self, text: str, max_length: int = 500) -> Optional[str]:
        """
        Summarize text using the configured provider
        
        Args:
            text: Text to summarize
            max_length: Maximum length of summary (approximate)
            
        Returns:
            Summarized text or None if failed
        """
        if self.provider == "ollama":
            return self._summarize_ollama(text, max_length)
        elif self.provider == "lmstudio":
            return self._summarize_lmstudio(text, max_length)
        else:
            print(f"Unknown provider: {self.provider}")
            return None
    
    def _summarize_ollama(self, text: str, max_length: int) -> Optional[str]:
        """Summarize using Ollama API"""
        try:
            prompt = f"""다음 강의 내용을 요약해주세요. 핵심 내용과 주요 포인트를 포함하여 약 {max_length}자 이내로 요약해주세요.

강의 내용:
{text}

요약:"""
            
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False
                },
                timeout=120
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get('response', '').strip()
            else:
                print(f"Ollama API error: {response.status_code}")
                return None
                
        except requests.exceptions.ConnectionError:
            print(f"Could not connect to Ollama at {self.base_url}")
            print("Make sure Ollama is running and the model is downloaded.")
            return None
        except Exception as e:
            print(f"Error summarizing with Ollama: {e}")
            return None
    
    def _summarize_lmstudio(self, text: str, max_length: int) -> Optional[str]:
        """Summarize using LM Studio API (OpenAI-compatible)"""
        try:
            prompt = f"""다음 강의 내용을 요약해주세요. 핵심 내용과 주요 포인트를 포함하여 약 {max_length}자 이내로 요약해주세요.

강의 내용:
{text}

요약:"""
            
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant that summarizes lecture content in Korean."},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": max_length,
                    "temperature": 0.7
                },
                timeout=120
            )
            
            if response.status_code == 200:
                result = response.json()
                if 'choices' in result and len(result['choices']) > 0:
                    return result['choices'][0]['message']['content'].strip()
                else:
                    print("LM Studio API returned unexpected format")
                    return None
            else:
                print(f"LM Studio API error: {response.status_code}")
                return None
                
        except requests.exceptions.ConnectionError:
            print(f"Could not connect to LM Studio at {self.base_url}")
            print("Make sure LM Studio is running and a model is loaded.")
            return None
        except Exception as e:
            print(f"Error summarizing with LM Studio: {e}")
            return None
    
    def check_connection(self) -> bool:
        """Check if the API is accessible"""
        try:
            if self.provider == "ollama":
                response = requests.get(f"{self.base_url}/api/tags", timeout=5)
                return response.status_code == 200
            elif self.provider == "lmstudio":
                response = requests.get(f"{self.base_url}/v1/models", timeout=5)
                return response.status_code == 200
            return False
        except:
            return False

