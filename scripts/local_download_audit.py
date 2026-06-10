"""Audit and download LearnUs files/videos through the local Python pipeline."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from learnus.auth import LearnUsAuth
from learnus.downloads import VideoDownloader
from learnus.scraping import LearnUsScraper, CourseInfo, LectureInfo
from learnus.utils import sanitize_filename


DOWNLOAD_DIR = ROOT / "downloads"
REPORT_DIR = ROOT / "reports" / "audits"
REPORT_LATEST = REPORT_DIR / "local_download_audit_latest.json"
SEMESTER_CODES = ["20", "10", "11", "21"]
SEMESTER_NAMES = {
    "10": "1학기",
    "11": "여름학기",
    "20": "2학기",
    "21": "겨울학기",
}


def main() -> int:
    args = parse_args()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().isoformat(timespec="seconds")
    report_path = REPORT_DIR / f"local_download_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    report: dict[str, Any] = {
        "started_at": started_at,
        "finished_at": None,
        "report_path": str(report_path),
        "options": vars(args),
        "login": False,
        "discovered_semesters": [],
        "totals": empty_totals(),
        "courses": [],
        "failures": [],
    }

    try:
        auth = LearnUsAuth()
        print("Logging in with .env credentials...")
        if not auth.login():
            report["failures"].append({"stage": "login", "reason": "Login failed"})
            write_report(report, report_path)
            return 1
        report["login"] = True

        session = auth.get_session()
        scraper = LearnUsScraper(session)
        downloader = VideoDownloader(str(DOWNLOAD_DIR))

        courses = discover_courses(scraper, args.years_back, args.start_year)
        report["discovered_semesters"] = summarize_semesters(courses)
        report["totals"]["courses"] = len(courses)
        write_report(report, report_path)

        if args.scan_only:
            print(f"Discovered {len(courses)} courses. Scan-only mode, not downloading.")
            report["finished_at"] = datetime.now().isoformat(timespec="seconds")
            write_report(report, report_path)
            return 0

        print(f"Discovered {len(courses)} courses across {len(report['discovered_semesters'])} semesters.")
        for index, course in enumerate(courses, start=1):
            if args.max_courses and index > args.max_courses:
                break
            print(f"\n[{index}/{len(courses)}] {course.year} {course.semester} {course.course_name}")
            course_result = audit_course(
                scraper=scraper,
                downloader=downloader,
                course=course,
                download_materials=not args.videos_only,
                download_videos=not args.materials_only,
                overwrite=args.overwrite,
            )
            report["courses"].append(course_result)
            merge_totals(report["totals"], course_result["totals"])
            report["failures"].extend(course_result["failures"])
            write_report(report, report_path)

        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        write_report(report, report_path)

        failed = report["totals"]["files_failed"] + report["totals"]["videos_failed"]
        print_summary(report)
        return 0 if failed == 0 else 2
    except KeyboardInterrupt:
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        report["failures"].append({"stage": "interrupted", "reason": "KeyboardInterrupt"})
        write_report(report, report_path)
        return 130
    except Exception as exc:
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        report["failures"].append({"stage": "fatal", "reason": repr(exc)})
        write_report(report, report_path)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download/audit LearnUs local files and lecture videos.")
    parser.add_argument("--years-back", type=int, default=6, help="Number of years to scan from start year.")
    parser.add_argument("--start-year", type=int, default=datetime.now().year, help="Newest year to scan.")
    parser.add_argument("--scan-only", action="store_true", help="Only discover courses/semesters.")
    parser.add_argument("--materials-only", action="store_true", help="Download materials/assignments only.")
    parser.add_argument("--videos-only", action="store_true", help="Download videos only.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing downloaded files.")
    parser.add_argument("--max-courses", type=int, default=0, help="Limit courses for a trial run.")
    return parser.parse_args()


def discover_courses(scraper: LearnUsScraper, years_back: int, start_year: int) -> list[CourseInfo]:
    found: dict[tuple[str, str, str], CourseInfo] = {}
    for year in range(start_year, start_year - years_back, -1):
        for semester in SEMESTER_CODES:
            courses = scraper.parse_course_list(year=str(year), semester=semester)
            if not courses:
                continue
            for course in courses:
                course.year = str(year)
                course.semester = SEMESTER_NAMES.get(str(semester), str(semester))
                key = (course.course_id, course.year, course.semester)
                found[key] = course

    return sorted(
        found.values(),
        key=lambda item: (item.year, semester_sort_key(item.semester), item.course_name),
        reverse=True,
    )


def semester_sort_key(name: str) -> int:
    order = {"2학기": 4, "여름학기": 3, "1학기": 2, "겨울학기": 1}
    return order.get(name, 0)


def summarize_semesters(courses: list[CourseInfo]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], int] = {}
    for course in courses:
        key = (course.year, course.semester)
        buckets[key] = buckets.get(key, 0) + 1
    return [
        {"year": year, "semester": semester, "course_count": count}
        for (year, semester), count in sorted(buckets.items(), key=lambda item: item[0], reverse=True)
    ]


def audit_course(
    *,
    scraper: LearnUsScraper,
    downloader: VideoDownloader,
    course: CourseInfo,
    download_materials: bool,
    download_videos: bool,
    overwrite: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "course_id": course.course_id,
        "course_name": course.course_name,
        "year": course.year,
        "semester": course.semester,
        "totals": empty_totals(include_courses=False),
        "files": [],
        "videos": [],
        "failures": [],
    }

    used_paths: dict[Path, str] = {}
    used_video_paths: dict[Path, str] = {}

    if download_materials:
        try:
            content = scraper.parse_course_content(course.course_id)
            if content.get("error"):
                add_failure(result, "course_content", course, None, content.get("error", "parse failed"))
            for section in content.get("sections", []):
                download_section_files(scraper, course, section, result, used_paths, overwrite)
        except Exception as exc:
            add_failure(result, "course_content", course, None, repr(exc))

    if download_videos:
        try:
            lectures = scraper.parse_lecture_list(course.course_url)
            for lecture in lectures:
                download_lecture(scraper, downloader, course, lecture, result, overwrite, used_video_paths)
        except Exception as exc:
            add_failure(result, "lecture_list", course, None, repr(exc))

    return result


def download_section_files(
    scraper: LearnUsScraper,
    course: CourseInfo,
    section: dict[str, Any],
    result: dict[str, Any],
    used_paths: dict[Path, str],
    overwrite: bool,
) -> None:
    section_title = safe_part(section.get("title") or "General")

    for material in section.get("materials", []):
        material_type = material.get("type", "file")
        if material_type == "folder":
            folder_data = scraper.parse_folder_page(material["url"])
            folder_dir = base_week_dir(course, section_title) / "Materials" / safe_part(material.get("name") or "Folder")
            save_description(folder_data.get("description"), folder_dir / "folder_description.txt", result)
            files = folder_data.get("files", [])
            if not files:
                result["totals"]["empty_containers"] += 1
            for item in files:
                save_path = unique_path(folder_dir / file_name(item.get("name"), item.get("url")), item.get("url", ""), used_paths)
                record_file(scraper, course, item, save_path, result, overwrite, "folder_file")
        elif material_type == "board" or "mod/ubboard" in material.get("url", ""):
            board_data = scraper.parse_board_page(material["url"])
            board_dir = base_week_dir(course, section_title) / "Materials" / safe_part(material.get("name") or "Board")
            save_description(board_data.get("description"), board_dir / "board_description.txt", result)
            files = board_data.get("files", [])
            if not files:
                result["totals"]["empty_containers"] += 1
            for item in files:
                save_path = unique_path(board_dir / file_name(item.get("name"), item.get("url")), item.get("url", ""), used_paths)
                record_file(scraper, course, item, save_path, result, overwrite, "board_file")
        else:
            save_dir = base_week_dir(course, section_title) / "Materials"
            name = file_name(material.get("name"), material.get("url"), material.get("extension"))
            save_path = unique_path(save_dir / name, material.get("url", ""), used_paths)
            record_file(scraper, course, material, save_path, result, overwrite, "material")

    for assignment in section.get("assignments", []):
        assign_dir = base_week_dir(course, section_title) / "Assignments" / safe_part(assignment.get("name") or "Assignment")
        assign_data = scraper.parse_assignment_page(assignment.get("url"))
        save_description(assign_data.get("description"), assign_dir / "assignment_description.txt", result)

        for item in assign_data.get("requirements", []):
            save_dir = assign_dir / "Assignment Attachments"
            save_path = unique_path(save_dir / file_name(item.get("name"), item.get("url")), item.get("url", ""), used_paths)
            record_file(scraper, course, item, save_path, result, overwrite, "assignment_requirement")

        for item in assign_data.get("submissions", []):
            save_dir = assign_dir / "My Submissions"
            save_path = unique_path(save_dir / file_name(item.get("name"), item.get("url")), item.get("url", ""), used_paths)
            record_file(scraper, course, item, save_path, result, overwrite, "assignment_submission")


def record_file(
    scraper: LearnUsScraper,
    course: CourseInfo,
    item: dict[str, Any],
    save_path: Path,
    result: dict[str, Any],
    overwrite: bool,
    kind: str,
) -> None:
    result["totals"]["files_seen"] += 1
    save_path = fit_path(save_path)
    item_result = {
        "kind": kind,
        "name": item.get("name") or save_path.name,
        "url": item.get("url", ""),
        "path": relative_download_path(save_path),
        "status": None,
        "bytes": 0,
        "reason": "",
    }

    if is_non_downloadable_external_url(item.get("url", ""), save_path.name):
        item_result["status"] = "skipped_external_link"
        item_result["reason"] = "External web link is not a downloadable file"
        result["totals"]["files_skipped_external"] += 1
        result["files"].append(item_result)
        return

    if save_path.exists() and save_path.stat().st_size > 0 and not overwrite:
        item_result["status"] = "existing"
        item_result["bytes"] = save_path.stat().st_size
        result["totals"]["files_existing"] += 1
        result["files"].append(item_result)
        return

    ok = scraper.download_file(item.get("url", ""), str(save_path))
    if ok and save_path.exists() and save_path.stat().st_size > 0:
        item_result["status"] = "downloaded"
        item_result["bytes"] = save_path.stat().st_size
        result["totals"]["files_downloaded"] += 1
    else:
        item_result["status"] = "failed"
        item_result["reason"] = getattr(scraper, "last_error", "") or "download_file returned false"
        result["totals"]["files_failed"] += 1
        add_failure(result, kind, course, item_result, item_result["reason"])
    result["files"].append(item_result)


def download_lecture(
    scraper: LearnUsScraper,
    downloader: VideoDownloader,
    course: CourseInfo,
    lecture: LectureInfo,
    result: dict[str, Any],
    overwrite: bool,
    used_paths: dict[Path, str],
) -> None:
    result["totals"]["videos_seen"] += 1
    output_path = downloader.get_output_path(course.year, course.semester, course.course_name, lecture.week, lecture.title)
    output_path = unique_video_path(output_path, lecture.activity_url or lecture.lecture_id or lecture.title, used_paths)
    item_result = {
        "lecture_id": lecture.lecture_id,
        "title": lecture.title,
        "week": lecture.week,
        "activity_url": lecture.activity_url,
        "path": relative_download_path(output_path),
        "status": None,
        "bytes": 0,
        "video_url": "",
        "reason": "",
    }

    if output_path.exists() and output_path.stat().st_size > 0 and not overwrite:
        item_result["status"] = "existing"
        item_result["bytes"] = output_path.stat().st_size
        result["totals"]["videos_existing"] += 1
        result["videos"].append(item_result)
        return

    if is_likely_non_video_url_item(lecture):
        item_result["status"] = "skipped_non_video"
        item_result["reason"] = "URL activity is not a downloadable video"
        result["totals"]["videos_skipped_non_video"] += 1
        result["videos"].append(item_result)
        return

    video_url = scraper.extract_video_url(lecture)
    item_result["video_url"] = video_url or ""
    if not video_url:
        if is_likely_non_video_url_item(lecture):
            item_result["status"] = "skipped_non_video"
            item_result["reason"] = "mod/url item did not resolve to media"
            result["totals"]["videos_skipped_non_video"] += 1
        else:
            item_result["status"] = "failed"
            item_result["reason"] = getattr(scraper, "last_error", "") or "Could not extract video URL"
            result["totals"]["videos_failed"] += 1
            add_failure(result, "video_extract", course, item_result, item_result["reason"])
        result["videos"].append(item_result)
        return

    ok = downloader.download_video(video_url, output_path, scraper.session)
    if ok and output_path.exists() and output_path.stat().st_size > 0:
        item_result["status"] = "downloaded"
        item_result["bytes"] = output_path.stat().st_size
        result["totals"]["videos_downloaded"] += 1
    else:
        item_result["status"] = "failed"
        item_result["reason"] = downloader.last_error or "download_video returned false"
        result["totals"]["videos_failed"] += 1
        add_failure(result, "video_download", course, item_result, item_result["reason"])
    result["videos"].append(item_result)


def is_likely_non_video_url_item(lecture: LectureInfo) -> bool:
    title = (lecture.title or "").lower()
    if re.search(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', title):
        return True
    if "mod/url" not in (lecture.activity_url or ""):
        return False
    video_words = ["video", "vod", "lecture", "recording", "recorded", "동영상", "영상", "녹화", "panopto"]
    return not any(word in title for word in video_words)


def save_description(text: str | None, path: Path, result: dict[str, Any]) -> None:
    if not text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    result["totals"]["descriptions_saved"] += 1


def base_week_dir(course: CourseInfo, section_title: str) -> Path:
    return (
        DOWNLOAD_DIR
        / safe_part(course.year or "Unknown")
        / safe_part(course.semester or "Unknown")
        / safe_part(course.course_name or f"course-{course.course_id}")
        / safe_part(section_title or "General")
    )


def safe_part(value: str) -> str:
    return shorten_component(sanitize_filename(value or "General"), 90)


def file_name(name: str | None, url: str | None, extension: str | None = None) -> str:
    cleaned = safe_part(name or url_name(url) or "file")
    ext = (extension or "").strip()
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    if ext and not Path(cleaned).suffix:
        cleaned = f"{cleaned}{ext}"
    return shorten_filename(cleaned, 120)


def shorten_component(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip('. _-') or "file"


def shorten_filename(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    path = Path(value)
    suffix = path.suffix
    stem_limit = max(20, limit - len(suffix))
    return f"{path.stem[:stem_limit].rstrip('. _-')}{suffix}"


def fit_path(path: Path, limit: int = 240) -> Path:
    if len(str(path)) <= limit:
        return path
    overflow = len(str(path)) - limit
    suffix = path.suffix
    keep = max(24, len(path.stem) - overflow - 8)
    return path.with_name(f"{path.stem[:keep].rstrip('. _-')}{suffix}")


def is_non_downloadable_external_url(url: str, filename: str) -> bool:
    if not url:
        return True
    if any(host in url for host in ["ys.learnus.org", "drive.google.com", "colab.research.google.com"]):
        return False
    file_pattern = r'\.(pdf|docx?|pptx?|xlsx?|zip|rar|7z|py|r|c|cpp|h|java|txt|csv|ipynb|hwp|hwpx)(?:[?#]|$)'
    if re.search(file_pattern, url, re.I):
        return False
    if Path(filename).suffix.lower() in {
        '.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.zip', '.rar',
        '.7z', '.py', '.r', '.c', '.cpp', '.h', '.java', '.txt', '.csv', '.ipynb',
        '.hwp', '.hwpx',
    }:
        return False
    return True


def url_name(url: str | None) -> str:
    if not url:
        return ""
    path = url.split("?", 1)[0].rstrip("/")
    return path.rsplit("/", 1)[-1]


def unique_path(path: Path, url: str, used_paths: dict[Path, str]) -> Path:
    path = path.with_name(safe_part(path.name))
    key = path.resolve()
    if key not in used_paths:
        used_paths[key] = url
        return path
    if used_paths[key] == url:
        return path

    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        resolved = candidate.resolve()
        if resolved not in used_paths:
            used_paths[resolved] = url
            return candidate
        counter += 1


def unique_video_path(path: Path, identity: str, used_paths: dict[Path, str]) -> Path:
    key = path.resolve()
    if key not in used_paths:
        used_paths[key] = identity
        return path
    if used_paths[key] == identity:
        return path

    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = fit_path(path.with_name(f"{stem}_{counter}{suffix}"))
        resolved = candidate.resolve()
        if resolved not in used_paths:
            used_paths[resolved] = identity
            return candidate
        counter += 1


def relative_download_path(path: Path) -> str:
    try:
        return str(path.relative_to(DOWNLOAD_DIR)).replace("\\", "/")
    except ValueError:
        return str(path)


def add_failure(
    result: dict[str, Any],
    stage: str,
    course: CourseInfo,
    item: dict[str, Any] | None,
    reason: str,
) -> None:
    failure = {
        "stage": stage,
        "course_id": course.course_id,
        "course_name": course.course_name,
        "year": course.year,
        "semester": course.semester,
        "reason": reason,
    }
    if item:
        failure.update({
            "name": item.get("name") or item.get("title"),
            "url": item.get("url") or item.get("activity_url"),
            "path": item.get("path"),
        })
    result["failures"].append(failure)


def empty_totals(*, include_courses: bool = True) -> dict[str, int]:
    totals = {
        "files_seen": 0,
        "files_downloaded": 0,
        "files_existing": 0,
        "files_failed": 0,
        "files_skipped_external": 0,
        "empty_containers": 0,
        "videos_seen": 0,
        "videos_downloaded": 0,
        "videos_existing": 0,
        "videos_failed": 0,
        "videos_skipped_non_video": 0,
        "descriptions_saved": 0,
    }
    if include_courses:
        totals["courses"] = 0
    return totals


def merge_totals(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + int(value)


def write_report(report: dict[str, Any], report_path: Path) -> None:
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    REPORT_LATEST.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(payload, encoding="utf-8")
    REPORT_LATEST.write_text(payload, encoding="utf-8")


def print_summary(report: dict[str, Any]) -> None:
    totals = report["totals"]
    print("\n=== Local download audit summary ===")
    print(f"Courses: {totals.get('courses', 0)}")
    print(
        "Files: "
        f"seen {totals['files_seen']}, "
        f"downloaded {totals['files_downloaded']}, "
        f"existing {totals['files_existing']}, "
        f"failed {totals['files_failed']}, "
        f"skipped_external {totals['files_skipped_external']}, "
        f"empty_containers {totals['empty_containers']}"
    )
    print(
        "Videos: "
        f"seen {totals['videos_seen']}, "
        f"downloaded {totals['videos_downloaded']}, "
        f"existing {totals['videos_existing']}, "
        f"failed {totals['videos_failed']}, "
        f"skipped_non_video {totals['videos_skipped_non_video']}"
    )
    print(f"Report: {report['report_path']}")


if __name__ == "__main__":
    sys.exit(main())
