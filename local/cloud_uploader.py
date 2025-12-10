"""
Cloud storage uploader for direct API uploads
Supports OneDrive and Google Drive API
"""
import os
import requests
from pathlib import Path
from typing import Optional, Dict
import json


class CloudUploader:
    """Direct uploads to cloud storage via API"""
    
    def __init__(self, cloud_type: str = 'onedrive', access_token: Optional[str] = None):
        """
        Args:
            cloud_type: 'onedrive' or 'gdrive'
            access_token: OAuth access token
        """
        self.cloud_type = cloud_type
        self.access_token = access_token
    
    def upload_file(self, file_path: Path, remote_path: str = None) -> Dict:
        """Upload file to cloud storage"""
        if not self.access_token:
            return {
                'success': False,
                'message': 'No access token. Authenticate with cloud storage first.'
            }
        
        if not file_path.exists():
            return {
                'success': False,
                'message': f'File not found: {file_path}'
            }
        
        if self.cloud_type == 'onedrive':
            return self._upload_to_onedrive(file_path, remote_path)
        elif self.cloud_type == 'gdrive':
            return self._upload_to_gdrive(file_path, remote_path)
        else:
            return {
                'success': False,
                'message': f'Unknown cloud type: {self.cloud_type}'
            }
    
    def _upload_to_onedrive(self, file_path: Path, remote_path: str = None) -> Dict:
        """Upload file to OneDrive using Microsoft Graph API"""
        try:
            file_size = file_path.stat().st_size
            
            if file_size < 4 * 1024 * 1024:
                if remote_path:
                    upload_path = f"/me/drive/root:/{remote_path}/{file_path.name}:/content"
                else:
                    upload_path = f"/me/drive/root:/{file_path.name}:/content"
                
                url = f"https://graph.microsoft.com/v1.0{upload_path}"
                
                headers = {
                    'Authorization': f'Bearer {self.access_token}',
                    'Content-Type': 'application/octet-stream'
                }
                
                with open(file_path, 'rb') as f:
                    response = requests.put(url, data=f, headers=headers, timeout=300)
                
                if response.status_code in [200, 201]:
                    return {
                        'success': True,
                        'message': f'Uploaded to OneDrive: {file_path.name}',
                        'url': response.json().get('webUrl')
                    }
                else:
                    return {
                        'success': False,
                        'message': f'OneDrive upload failed: {response.status_code} - {response.text}'
                    }
            else:
                return {
                    'success': False,
                    'message': 'Large file upload not yet implemented. Use files < 4MB or configure local sync.'
                }
        
        except Exception as e:
            return {
                'success': False,
                'message': f'OneDrive upload error: {str(e)}'
            }
    
    def _upload_to_gdrive(self, file_path: Path, remote_path: str = None) -> Dict:
        """Upload file to Google Drive using Google Drive API"""
        try:
            metadata = {
                'name': file_path.name
            }
            
            file_size = file_path.stat().st_size
            if file_size < 5 * 1024 * 1024:
                url = 'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart'
                
                headers = {
                    'Authorization': f'Bearer {self.access_token}'
                }
                
                boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
                headers['Content-Type'] = f'multipart/related; boundary={boundary}'
                
                body = (
                    f'--{boundary}\r\n'
                    f'Content-Type: application/json; charset=UTF-8\r\n\r\n'
                    f'{json.dumps(metadata)}\r\n'
                    f'--{boundary}\r\n'
                    f'Content-Type: application/octet-stream\r\n\r\n'
                ).encode('utf-8')
                
                with open(file_path, 'rb') as f:
                    body += f.read()
                
                body += f'\r\n--{boundary}--\r\n'.encode('utf-8')
                
                response = requests.post(url, data=body, headers=headers, timeout=300)
                
                if response.status_code in [200, 201]:
                    file_data = response.json()
                    return {
                        'success': True,
                        'message': f'Uploaded to Google Drive: {file_path.name}',
                        'url': file_data.get('webViewLink')
                    }
                else:
                    return {
                        'success': False,
                        'message': f'Google Drive upload failed: {response.status_code} - {response.text}'
                    }
            else:
                return {
                    'success': False,
                    'message': 'Large file upload not yet implemented. Use files < 5MB or configure local sync.'
                }
        
        except Exception as e:
            return {
                'success': False,
                'message': f'Google Drive upload error: {str(e)}'
            }
    
    def get_auth_url(self, redirect_uri: str) -> str:
        """Get OAuth authorization URL"""
        if self.cloud_type == 'onedrive':
            client_id = os.getenv('ONEDRIVE_CLIENT_ID', '')
            scopes = 'files.readwrite offline_access'
            return (
                f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
                f"?client_id={client_id}"
                f"&response_type=code"
                f"&redirect_uri={redirect_uri}"
                f"&response_mode=query"
                f"&scope={scopes}"
            )
        elif self.cloud_type == 'gdrive':
            client_id = os.getenv('GDRIVE_CLIENT_ID', '')
            scopes = 'https://www.googleapis.com/auth/drive.file'
            return (
                f"https://accounts.google.com/o/oauth2/v2/auth"
                f"?client_id={client_id}"
                f"&redirect_uri={redirect_uri}"
                f"&response_type=code"
                f"&scope={scopes}"
                f"&access_type=offline"
            )
        else:
            return ''

