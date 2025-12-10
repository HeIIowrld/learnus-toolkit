# LearnUs Contents Downloader - Web Version

서버 배포용 웹 버전. 로컬 처리 기능 제외.

## 기능

- 강의 영상 다운로드
- 자료 및 과제 다운로드
- 클라우드 동기화 (OneDrive/Google Drive)
- LLM API 설정 (OpenAI, Google, Ollama)

## 제한사항

- AI 전사 없음
- 비디오 분석 없음

## 설치 요구사항

- Python 3.8 이상
- FFmpeg (비디오 다운로드 필수)
  - Windows: https://ffmpeg.org/download.html
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt-get install ffmpeg`

## 설치

```bash
pip install -r requirements.txt
```

제외된 패키지:
- openai-whisper
- opencv-python
- numpy

## 사용 방법

1. `.env` 파일 생성 (선택사항):
```env
LEARNUS_USERNAME=your_yonsei_id
LEARNUS_PASSWORD=your_password
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...
```

2. 애플리케이션 실행:
```bash
python app.py
```

3. 브라우저에서 `http://localhost:5000` 접속

## LLM API 설정

Settings에서 LLM API 설정:
- OpenAI (GPT-4, GPT-3.5)
- Google (Gemini Pro)
- Ollama (로컬/원격)

## 배포

### Proxmox LXC 배포

#### 사전 요구사항

- Proxmox LXC 컨테이너 (Debian/Ubuntu 권장)
- 루트 또는 sudo 권한
- 인터넷 연결

#### 설치

**1. 파일 업로드**

```bash
# SCP 사용 예시
scp -r web/* user@your-lxc-ip:/opt/learnus-web/
```

또는 Git 사용:
```bash
git clone <your-repo>
cd learnus-web
```

**2. 설정 실행**

```bash
cd /opt/learnus-web
chmod +x setup.sh
./setup.sh
```

설정 스크립트가 다음을 수행:
- 시스템 패키지 설치
- Python 가상 환경 생성
- 패키지 설치
- 다운로드 디렉토리 생성
- .env 파일 템플릿 생성

**3. 실행**

직접 실행:
```bash
./run.sh
```

systemd 서비스:
```bash
sudo chmod +x install-service.sh
sudo ./install-service.sh
sudo systemctl start learnus-web
sudo systemctl enable learnus-web
```

#### 설정

**환경 변수 (.env)**

```env
LEARNUS_USERNAME=your_yonsei_id
LEARNUS_PASSWORD=your_password
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...
LLM_PROVIDER=openai
```

**방화벽 설정**

```bash
# UFW 사용 시
sudo ufw allow 5000/tcp

# iptables 사용 시
sudo iptables -A INPUT -p tcp --dport 5000 -j ACCEPT
```

#### 서비스 관리

```bash
# 시작
sudo systemctl start learnus-web

# 중지
sudo systemctl stop learnus-web

# 재시작
sudo systemctl restart learnus-web

# 상태 확인
sudo systemctl status learnus-web

# 로그 확인
sudo journalctl -u learnus-web -f

# 자동 시작
sudo systemctl enable learnus-web
```

#### Nginx 리버스 프록시 (선택사항)

```nginx
server {
    listen 80;
    server_name learnus.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Let's Encrypt로 SSL 인증서 발급:
```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d learnus.yourdomain.com
```

#### 업데이트

```bash
cd /opt/learnus-web
git pull  # Git 사용 시
# 또는 새 파일로 교체

source venv/bin/activate
pip install -r requirements.txt --upgrade

# 서비스 사용 시
sudo systemctl restart learnus-web
```

#### 문제 해결

**포트 사용 중**
```bash
# 다른 프로세스 확인
sudo lsof -i :5000
# 또는
sudo netstat -tulpn | grep 5000
```

**권한 오류**
```bash
chmod +x setup.sh run.sh install-service.sh
chmod -R 755 /opt/learnus-web
```

**로그 확인**
```bash
# systemd 서비스 로그
sudo journalctl -u learnus-web -n 100

# 직접 실행 시 터미널 출력 확인
```

#### 백업
```bash
# 다운로드된 파일
tar -czf learnus-backup-$(date +%Y%m%d).tar.gz downloads/

# 설정 파일
cp .env .env.backup
```

#### 보안

- `.env` 파일 권한: `chmod 600 .env`
- 방화벽: 필요한 포트만 개방
- 정기 업데이트 권장

## 특징

- 경량화된 웹 서버 배포용
- 무거운 의존성 제외
- 전사/분석 기능 없음
