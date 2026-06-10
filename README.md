# LearnUs 다운로드 도우미

연세대학교 LearnUs 강의 영상, 학습자료, 과제를 내려받고 로컬에서 전사와 간단한 분석을 실행하는 도구입니다.

## 빠른 실행

Python 3.10 이상이 필요합니다. 설치 스크립트는 `.venv` 가상환경을 만들고 필요한 패키지를 설치한 뒤 FFmpeg와 Whisper 사용 가능 여부를 확인합니다.

Windows:

```powershell
.\scripts\bootstrap.cmd
.\scripts\run_app.cmd
```

macOS/Linux:

```bash
chmod +x scripts/bootstrap.sh scripts/run_app.sh
./scripts/bootstrap.sh
./scripts/run_app.sh
```

실행 후 브라우저에서 `http://localhost:5000`으로 접속합니다.

전역 `python app.py`는 환경에 따라 다른 Python을 잡을 수 있으므로 배포 실행은 위의 `scripts/run_app.*` 스크립트를 사용합니다.

## 환경 설정

`.env.example`을 `.env`로 복사한 뒤 계정 정보를 입력합니다.

```env
LEARNUS_USERNAME=your_yonsei_id
LEARNUS_PASSWORD=your_password
WHISPER_MODEL=medium
WHISPER_LANGUAGE=auto
WHISPER_BACKEND=auto
```

`imageio-ffmpeg`가 함께 설치되므로 별도 FFmpeg 설치 없이도 전사와 HLS 다운로드가 동작하도록 구성되어 있습니다. 시스템에 FFmpeg를 직접 설치했다면 `FFMPEG_PATH`로 지정할 수도 있습니다.

## 주요 기능

- LearnUs 강의 영상, 학습자료, 과제 다운로드
- `downloads/연도/학기/강좌명/주차` 구조로 파일 정리
- `.env` 계정 정보 또는 브라우저 쿠키 기반 로그인
- Whisper 기반 텍스트 전사와 SRT 자막 생성
- 전사 텍스트 요약과 영상 프레임 분석

## Chrome/Edge 확장

`chrome_extension/` 폴더를 Chrome 또는 Edge의 확장 프로그램 관리 화면에서 “압축해제된 확장 프로그램 로드”로 선택합니다.

- Chrome: `chrome://extensions`
- Edge: `edge://extensions`

확장은 LearnUs 강의 페이지에서 바로 실행되며 로컬 Flask 서버에 의존하지 않습니다. 브라우저 로그인 세션을 사용해 학습자료 일괄 다운로드, 각 영상 옆 다운로드 버튼, 영상 일괄 다운로드 버튼을 제공합니다.

MP4 같은 직접 영상 URL은 Chromium 계열 브라우저의 다운로드 API로 저장합니다. HLS(`.m3u8`) 영상은 확장 프로그램의 offscreen 문서에서 플레이리스트와 세그먼트를 받아 하나의 `.ts` 또는 fragmented `.mp4` 파일로 병합한 뒤 저장합니다. DRM, SAMPLE-AES, 서버가 특수 요청 헤더를 강제하는 영상은 브라우저 확장만으로 처리되지 않을 수 있습니다.

## 프로젝트 구조

```text
learnus/
├── app.py
├── requirements.txt
├── learnus/
│   ├── auth.py
│   ├── utils.py
│   ├── scraping/
│   │   └── scraper.py
│   ├── downloads/
│   │   ├── downloader.py
│   │   └── migration.py
│   └── processing/
│       ├── transcriber.py
│       ├── summarizer.py
│       └── video_analyzer.py
├── scripts/
│   ├── bootstrap.ps1
│   ├── bootstrap.cmd
│   ├── run_app.ps1
│   ├── run_app.cmd
│   ├── bootstrap.sh
│   └── run_app.sh
├── chrome_extension/
├── templates/
├── reports/
├── downloads/
└── data/
```

## 전사 설정

- `WHISPER_MODEL`: `tiny`, `base`, `small`, `medium`, `large` 중 선택합니다.
- `WHISPER_LANGUAGE`: 자동 감지는 `auto`, 한국어 고정은 `ko`, 영어 고정은 `en`을 사용합니다.
- `WHISPER_BACKEND`: 현재 기본 실행 경로는 `openai-whisper`입니다. `auto`, `openai` 값을 권장합니다.
