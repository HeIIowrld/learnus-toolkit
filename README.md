# LearnUs Toolkit

Yonsei University LearnUs 플랫폼에서 강의 영상 및 자료를 다운로드하는 도구.

## 버전

- **Local Version** (`local/`) - 로컬 실행 버전
- **Web Version** (`web/`) - 서버 배포 버전
- **Chrome Extension** (`chrome-extension/`) - 브라우저 확장 프로그램

## 빠른 시작

### 로컬 버전

```bash
cd local
pip install -r requirements.txt
python app.py
```

브라우저에서 `http://localhost:5000` 접속

### 웹 버전 (Proxmox LXC 배포)

```bash
cd web
chmod +x setup.sh
./setup.sh
./run.sh
```

배포 가이드는 `web/README.md` 참고

### Chrome Extension

1. Chrome에서 `chrome://extensions/` 접속
2. "개발자 모드" 활성화
3. "압축해제된 확장 프로그램을 로드합니다" 클릭
4. `chrome-extension` 폴더 선택

## 기능

- 강의 영상 다운로드 (HLS 스트림 지원)
- 강의 자료 및 과제 다운로드
- 디렉토리 구조: `year/semester/course/week`
- 브라우저 쿠키 인증
- 다중 처리 다운로드
- 진행 상황 모니터링
- 클라우드 스토리지 동기화 (OneDrive/Google Drive)
- 로컬 버전: AI 전사 및 비디오 분석

## 프로젝트 구조

```
learnus/
├── local/              # 로컬 버전 (모든 기능)
├── web/                # 웹 버전 (배포용)
├── chrome-extension/   # 브라우저 확장 프로그램
└── README.md          # 이 파일
```

## 문서

- 로컬 버전: `local/README.md`
- 웹 버전: `web/README.md`
- Chrome Extension: `chrome-extension/README.md`
