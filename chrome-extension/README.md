# LearnUs Contents Downloader - Chrome Extension

브라우저 확장 프로그램. Python 서버 불필요.

## 기능

- LearnUs 인증 (자동 세션 감지)
- 강의 영상 다운로드
- 강의 자동 탐색
- 디렉토리 구조: `year-semester-course_name`
- 설정 페이지
- 다운로드 제어 (일시정지, 재개, 중지)

## 설치

1. `chrome://extensions/` 접속
2. 개발자 모드 활성화
3. "압축해제된 확장 프로그램을 로드합니다" 클릭
4. `chrome-extension` 폴더 선택

## 사용 방법

1. 확장 프로그램 아이콘 클릭 (새 탭 열림)
2. 로그인:
   - 브라우저에 LearnUs 세션이 있으면 자동 로그인
   - 없으면 Yonsei ID와 비밀번호 입력
3. 강의 선택 후 다운로드
4. HLS 스트림(.m3u8)은 Python 버전 사용 필요

## 다운로드 위치 설정

1. 확장 프로그램 아이콘 우클릭 → 옵션
2. 서브디렉토리 이름 입력 또는 비워두기
3. Save Download Settings 클릭

## 자격 증명 저장

- 사용자명: Chrome 로컬 저장소에 평문 저장
- 비밀번호: 저장하지 않음
- 삭제: 옵션 페이지 또는 Logout 버튼

## 제한사항

- HLS 스트림(.m3u8): Python 버전 사용 필요
- 인증: jsencrypt 라이브러리 사용
- 파일 시스템: 직접 접근 불가, Chrome 다운로드 API 사용

## 문제 해결

- 확장 프로그램 로드 오류: `chrome://extensions/`에서 오류 확인
- 자동 로그인 실패: 옵션 페이지에서 자격 증명 확인
- 강의 로드 실패: LearnUs 로그인 상태 확인
- 다운로드 실패: 브라우저 콘솔 확인, HLS는 Python 버전 사용

## 개발

1. 파일 수정
2. `chrome://extensions/`에서 새로고침
3. 콘솔(F12)에서 오류 확인

## 파일 구조

```
chrome-extension/
├── manifest.json
├── app.html
├── app.js
├── app.css
├── background.js
├── injected.js
├── content.js
├── options.html
├── options.js
├── icons/
└── README.md
```

jsencrypt 라이브러리: MIT 라이선스 (https://github.com/travist/jsencrypt)
