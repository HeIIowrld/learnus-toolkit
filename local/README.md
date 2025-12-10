# LearnUs Contents Downloader - Local Version

로컬 실행 버전. AI 전사 및 비디오 분석 포함.

## 기능

- 강의 영상 다운로드
- 자료 및 과제 다운로드
- AI 전사 (Whisper)
- 비디오 분석
- AI 요약
- 클라우드 동기화 (OneDrive/Google Drive)

## 설치 요구사항

- Python 3.8 이상
- FFmpeg (비디오 처리 필수)
  - Windows: https://ffmpeg.org/download.html
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt-get install ffmpeg`

## 설치

```bash
pip install -r requirements.txt
```

## 사용 방법

1. `.env` 파일 생성 (선택사항):
```env
LEARNUS_USERNAME=your_yonsei_id
LEARNUS_PASSWORD=your_password
```

2. 애플리케이션 실행:
```bash
python app.py
```

3. 브라우저에서 `http://localhost:5000` 접속

## 제한사항

- AI 전사: 높은 CPU/GPU 리소스 필요
- 비디오 분석: 영상당 수 분 소요
- 모든 처리: 로컬 환경에서만 수행
- 서버 배포: `web/` 버전 사용
