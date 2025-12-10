"""
Authentication module for LearnUs (Yonsei LMS)
Adapted from yontil login logic
"""
import re
import json
import os
import requests
from bs4 import BeautifulSoup
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_der_public_key
from cryptography.hazmat.backends import default_backend
from dotenv import load_dotenv
import binascii

# Load environment variables
load_dotenv()


LEARNUS_ORIGIN = 'https://ys.learnus.org'
INFRA_ORIGIN = 'https://infra.yonsei.ac.kr'


class LearnUsAuth:
    """Handles authentication with LearnUs"""
    
    def __init__(self, cookies_dict=None):
        """
        Initialize with optional cookies from browser session
        
        Args:
            cookies_dict: Dictionary of cookie name-value pairs from browser
        """
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        # If cookies provided, set them in the session
        if cookies_dict:
            for name, value in cookies_dict.items():
                self.session.cookies.set(name, value, domain='.learnus.org')
    
    def create_session_from_cookies(self, cookies_dict: dict) -> bool:
        """
        Create authenticated session from browser cookies
        
        Args:
            cookies_dict: Dictionary of cookie name-value pairs
            
        Returns:
            True if session is valid, False otherwise
        """
        try:
            # Set cookies in session
            for name, value in cookies_dict.items():
                self.session.cookies.set(name, value, domain='.learnus.org')
            
            # Verify session by accessing LearnUs
            response = self.session.get('https://ys.learnus.org/', timeout=10)
            
            # Check if we're logged in (not redirected to login page)
            if 'login' in response.url.lower() or '로그인' in response.text[:1000]:
                return False
            
            return True
        except Exception as e:
            print(f"Error creating session from cookies: {e}")
            return False
    
    def parse_input_tags(self, html: str) -> dict:
        """Parse input tags from HTML form"""
        soup = BeautifulSoup(html, 'html.parser')
        inputs = {}
        for input_tag in soup.find_all('input'):
            name = input_tag.get('name')
            value = input_tag.get('value', '')
            if name:
                inputs[name] = value
        return inputs
    
    def string_to_hex(self, raw: bytes) -> str:
        """Convert bytes to hex string (uppercase)"""
        return binascii.hexlify(raw).decode('utf-8').upper()
    
    def rsa_encrypt(self, message: str, modulus_hex: str, exponent_hex: str) -> bytes:
        """Encrypt message using RSA public key"""
        try:
            # Try using pycryptodome if available (better RSA support)
            from Crypto.PublicKey import RSA
            from Crypto.Cipher import PKCS1_v1_5
            
            # Convert hex strings to integers
            modulus = int(modulus_hex, 16)
            exponent = int(exponent_hex, 16)
            
            # Create RSA key object
            key = RSA.construct((modulus, exponent))
            
            # Encrypt
            cipher = PKCS1_v1_5.new(key)
            encrypted = cipher.encrypt(message.encode('utf-8'))
            return encrypted
        except ImportError:
            # Fallback to cryptography library
            from cryptography.hazmat.primitives.asymmetric import rsa
            
            # Convert hex strings to integers
            modulus = int(modulus_hex, 16)
            exponent = int(exponent_hex, 16)
            
            # Create public key from modulus and exponent
            public_numbers = rsa.RSAPublicNumbers(exponent, modulus)
            public_key = public_numbers.public_key(default_backend())
            
            # Encrypt the message
            encrypted = public_key.encrypt(
                message.encode('utf-8'),
                padding.PKCS1v15()
            )
            return encrypted
    
    def login(self, username: str = None, password: str = None) -> bool:
        """
        Authenticate with LearnUs
        
        Args:
            username: Yonsei ID (optional, will use LEARNUS_USERNAME from .env if not provided)
            password: Password (optional, will use LEARNUS_PASSWORD from .env if not provided)
            
        Returns:
            True if login successful, False otherwise
        """
        # Get credentials from environment variables if not provided
        if username is None:
            username = os.getenv('LEARNUS_USERNAME')
        if password is None:
            password = os.getenv('LEARNUS_PASSWORD')
        
        if not username or not password:
            print("Error: Username and password are required.")
            print("Please provide them as arguments or set LEARNUS_USERNAME and LEARNUS_PASSWORD in .env file")
            return False
        try:
            # Step 1: Get initial login page
            response = self.session.get(
                f'{LEARNUS_ORIGIN}/passni/sso/spLogin2.php',
                headers={'Referer': 'https://ys.learnus.org'},
                timeout=10
            )
            data1 = self.parse_input_tags(response.text)
            
            if 'S1' not in data1:
                print("Failed to get S1 token")
                return False
            
            # Step 2: Get SSO challenge and RSA key
            response = self.session.post(
                f'{INFRA_ORIGIN}/sso/PmSSOService',
                data={
                    'app_id': 'ednetYonsei',
                    'retUrl': 'https://ys.learnus.org',
                    'failUrl': 'https://ys.learnus.org',
                    'baseUrl': 'https://ys.learnus.org',
                    'S1': data1['S1'],
                    'refererUrl': 'https://ys.learnus.org',
                },
                timeout=10
            )
            
            html = response.text
            sso_challenge_match = re.search(r"var ssoChallenge\s*=\s*'([^']+)'", html)
            key_match = re.search(r"rsa\.setPublic\(\s*'([^']+)',\s*'([^']+)'", html, re.IGNORECASE)
            
            if not sso_challenge_match or not key_match:
                print("Failed to extract SSO challenge or RSA key")
                return False
            
            sso_challenge = sso_challenge_match.group(1)
            key_modulus = key_match.group(1)
            key_exponent = key_match.group(2)
            
            # Step 3: Encrypt credentials and authenticate
            login_data = {
                'userid': username,
                'userpw': password,
                'ssoChallenge': sso_challenge
            }
            
            encrypted = self.rsa_encrypt(json.dumps(login_data), key_modulus, key_exponent)
            E2 = self.string_to_hex(encrypted)
            
            response = self.session.post(
                f'{INFRA_ORIGIN}/sso/PmSSOAuthService',
                data={
                    'app_id': 'ednetYonsei',
                    'retUrl': 'https://ys.learnus.org',
                    'failUrl': 'https://ys.learnus.org',
                    'baseUrl': 'https://ys.learnus.org',
                    'loginType': 'invokeID',
                    'E2': E2,
                    'refererUrl': 'https://ys.learnus.org',
                },
                timeout=10
            )
            
            data4 = self.parse_input_tags(response.text)
            
            if 'E3' not in data4 or 'E4' not in data4:
                print("Authentication failed - invalid response")
                return False
            
            # Step 4: Complete login
            response = self.session.post(
                f'{LEARNUS_ORIGIN}/passni/sso/spLoginData.php',
                data={
                    'app_id': 'ednetYonsei',
                    'retUrl': 'https://ys.learnus.org',
                    'failUrl': 'https://ys.learnus.org',
                    'baseUrl': 'https://ys.learnus.org',
                    'E3': data4['E3'],
                    'E4': data4['E4'],
                    'S2': data4.get('S2', ''),
                    'CLTID': data4.get('CLTID', ''),
                    'refererUrl': 'https://ys.learnus.org',
                },
                timeout=10
            )
            
            # Step 5: Final login process
            response = self.session.get(
                f'{LEARNUS_ORIGIN}/passni/spLoginProcess.php',
                timeout=10
            )
            
            # Check if login was successful by checking if we can access the dashboard
            response = self.session.get(f'{LEARNUS_ORIGIN}/', timeout=10)
            if 'login' in response.url.lower() or '로그인' in response.text:
                print("Login verification failed")
                return False
            
            print("Login successful!")
            return True
            
        except Exception as e:
            print(f"Login error: {e}")
            return False
    
    def get_session(self) -> requests.Session:
        """Get the authenticated session"""
        return self.session

