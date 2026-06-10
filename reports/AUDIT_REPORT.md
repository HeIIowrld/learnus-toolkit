# LearnUs App Audit Report

Date: 2026-06-10

## Executive Summary

- `.env` credentials were used through the app login endpoint. Login succeeded.
- Current default semester resolved to 2026 1학기 with 6 courses.
- Course material/assignment downloads were executed for all 6 current-semester courses.
- Download tasks reported 0 failures.
- Transcription initially failed because OpenAI Whisper could not find `ffmpeg.exe`; this was fixed and verified.
- `.venv` is the active environment; duplicate `venv` was removed.
- UI served successfully, rendered JavaScript passed `node --check`, and downloaded-file serving was verified.

## Download Results

| Course | Task status | Completed units | Failed units | Top-level items |
| --- | ---: | ---: | ---: | ---: |
| 딥러닝개론및응용 (AIC3100.01-00) | completed | 11 | 0 | 3 |
| 경영정보시스템 (BIZ3189.05-00) | completed | 4 | 0 | 1 |
| 생산시스템분석 (IIE4105.01-00) | completed | 29 | 0 | 9 |
| 비즈니스프로그래밍 (BIZ3198.02-00) | completed | 0 | 0 | 0 |
| 전략경영 (BIZ3147.01-00) | completed | 36 | 0 | 10 |
| AI와오퍼레이션애널리틱스 (BIZ3347.01-00) | completed | 105 | 0 | 28 |

Notes:

- 비즈니스프로그래밍 had no material or assignment items to download.
- Completed units can exceed top-level items because folders and assignments expand into multiple attachment/submission files.
- Saved 2026/1학기 course folders use the canonical `강좌명 (학정번호)` naming style.

## UI Audit

Checks performed:

- `GET /` returned HTTP 200.
- Rendered inline JavaScript passed `node --check`.
- `/api/downloads` returned downloaded file metadata.
- `/api/files/<path>` successfully served a downloaded Korean-path file.
- `/api/available-semesters` now returns non-null Korean semester names even from cache.

Fixes applied:

- Removed duplicate `pauseBtn`/`resumeBtn` IDs in the task status UI by separating global task controls from unified download controls.
- Fixed cached semester display names so UI dropdowns no longer show `null`.

Remaining UI risk:

- Some visible strings in `templates/index.html` are already mojibake/corrupted Korean. Functional checks pass, but UI copy should be cleaned in a separate text pass.

## Browser Extension Audit

Checks performed:

- Added `scripts/extension_render_audit.py` to render the unpacked extension against a local LearnUs-shaped HTTPS page.
- Verified extension UI rendering in Microsoft Edge 149: panel injection, Korean text display, material/video counts, inline video download button, and collapse/expand behavior.
- Verified controlled downloads through the extension in Edge: one PDF material and one MP4 video were saved into the temporary profile download folder.
- Saved render screenshots under `reports/screenshots/`; they are ignored as audit artifacts.

Findings:

- Google Chrome 149 on this machine ignored command-line unpacked extension loading during automation with `--load-extension is not allowed in Google Chrome`.
- The same extension loaded and worked in Microsoft Edge using the same source directory, so the failure is specific to Chrome's automated launch policy here, not to the extension files.

## Python And File Structure

Changes applied:

- Centralized course download folder naming in `app.py` so video, material, and assignment downloads use the same course folder name.
- Removed old folder migration and alias lookup paths; downloads now use only the canonical `year/semester/course/week` structure.
- Fixed `.gitignore` so active root source files are no longer ignored by broad output-folder rules.
- Added `.runtime-bin/` and runtime cache files to ignore rules.

Remaining structure risk:

- `app.py` is still a large monolithic Flask file. A future low-risk split should move routes into blueprints and services such as `services/downloads.py`, `services/transcription.py`, and `services/courses.py`.

## Transcription Audit

Initial result:

- `extract_audio()` succeeded using bundled `imageio-ffmpeg`.
- `transcribe_audio()` failed with `[WinError 2]` because OpenAI Whisper calls `ffmpeg` by executable name.

Fix applied:

- `WhisperTranscriber` now creates/uses a runtime `ffmpeg.exe` shim for the bundled ffmpeg executable and prepends it to `PATH`.

Verification:

- Generated a 1-second local test video.
- Extracted audio successfully.
- Transcribed with Whisper `tiny`.
- Confirmed `.txt`, `.srt`, and `.json` transcript files were created.

## Environment Cleanup

- Active environment: `.venv`
- Removed duplicate environment: `venv`
- `.venv` remains and is used by the running server.

## Current Server

- Running at `http://localhost:5000`
- Started with `.venv`
- Login and environment checks passed after restart.
