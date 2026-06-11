"""Render-audit the Chrome extension against a local LearnUs-shaped page."""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
AUDIT_DIR = REPORT_DIR / "audits"
SCREENSHOT_DIR = REPORT_DIR / "screenshots"
RUNTIME_BASE = Path(tempfile.gettempdir()) / "learnus_extension_audit"
RUNTIME = RUNTIME_BASE / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
CERT = RUNTIME / "cert.pem"
KEY = RUNTIME / "key.pem"
EXTENSION_SOURCE = (ROOT / "chrome_extension").resolve()
EXTENSION_DIR = RUNTIME / "extension"
EXPECTED_MATERIALS = "5"
EXPECTED_VIDEOS = "1"
EXPECTED_DOWNLOADS = 8

COURSE_HTML = """<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><title>테스트 강의 - LearnUs</title></head>
<body>
  <div class="page-header-headings"><h1>테스트 강의 (TST1000.01-00)</h1></div>
  <ul class="topics">
    <li id="section-1" class="section main clearfix">
      <span id="section-name-1" class="sectionname">1주차 [테스트]</span>
      <div class="activityinstance">
        <a href="/mod/vod/viewer.php?id=111"><span class="instancename">샘플 영상 동영상</span></a>
      </div>
      <div class="activityinstance">
        <a href="/pluginfile.php/111/mod_resource/content/1/slides.pdf"><span class="instancename">강의자료.pdf 파일</span></a>
      </div>
      <div class="activityinstance">
        <a href="/mod/ubfile/view.php?id=222"><span class="instancename">Exercise Script</span></a>
      </div>
      <div class="activityinstance">
        <a href="/mod/folder/view.php?id=333"><span class="instancename">Practice Files</span></a>
      </div>
      <div class="activityinstance">
        <a href="/mod/ubboard/view.php?id=444"><span class="instancename">Class Board</span></a>
      </div>
      <div class="activityinstance">
        <a href="/mod/assign/view.php?id=555"><span class="instancename">Homework Assignment</span></a>
      </div>
    </li>
  </ul>
</body>
</html>"""

VIEWER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"></head><body>
<video controls><source src="https://ys.learnus.org/video/sample.mp4" type="video/mp4"></video>
<script>window.videoUrl = "https://ys.learnus.org/video/sample.mp4";</script>
</body></html>"""

SAMPLE_MP4 = (
    b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    b"\x00\x00\x00\x08free"
    b"\x00\x00\x00\x10mdatlearnus-test"
)


def ensure_report_dirs() -> None:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def relative_artifact_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


class TestPageHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/course/view.php":
            self._send(200, "text/html; charset=utf-8", COURSE_HTML.encode("utf-8"))
        elif path == "/mod/vod/viewer.php":
            self._send(200, "text/html; charset=utf-8", VIEWER_HTML.encode("utf-8"))
        elif path == "/mod/ubfile/view.php":
            self._redirect("/pluginfile.php/222/mod_ubfile/content/0/exercise.ipynb?forcedownload=1")
        elif path == "/mod/folder/view.php":
            self._send(
                200,
                "text/html; charset=utf-8",
                b"""<!doctype html><html><body>
                <a href="/pluginfile.php/333/mod_folder/content/0/data.csv?forcedownload=1">data.csv</a>
                <a href="/pluginfile.php/333/mod_folder/content/0/notebook.ipynb?forcedownload=1">notebook.ipynb</a>
                </body></html>""",
            )
        elif path == "/mod/ubboard/view.php" and "bwid" not in urllib.parse.parse_qs(parsed.query):
            self._send(
                200,
                "text/html; charset=utf-8",
                b"""<!doctype html><html><body>
                <a href="/mod/ubboard/view.php?id=444&amp;bwid=777">Board Post</a>
                </body></html>""",
            )
        elif path == "/mod/ubboard/view.php":
            self._send(
                200,
                "text/html; charset=utf-8",
                b"""<!doctype html><html><body>
                <a href="/pluginfile.php/444/mod_ubboard/attachment/777/board.csv?forcedownload=1">board.csv</a>
                </body></html>""",
            )
        elif path == "/mod/assign/view.php":
            self._send(
                200,
                "text/html; charset=utf-8",
                b"""<!doctype html><html><body>
                <a href="/pluginfile.php/555/mod_assign/introattachment/0/guide.pdf?forcedownload=1">guide.pdf</a>
                <a href="/pluginfile.php/555/assignsubmission_file/submission_files/1/submission.docx?forcedownload=1">submission.docx</a>
                </body></html>""",
            )
        elif path.endswith(".pdf"):
            self._send(200, "application/pdf", b"%PDF-1.4\n% test pdf\n")
        elif path.endswith(".ipynb"):
            self._send(200, "application/x-ipynb+json", b'{"cells":[],"metadata":{},"nbformat":4,"nbformat_minor":5}\n')
        elif path.endswith(".csv"):
            self._send(200, "text/csv", b"x,y\n1,2\n")
        elif path.endswith(".docx"):
            self._send(200, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"docx-test\n")
        elif path == "/video/sample.mp4":
            self._send(200, "video/mp4", SAMPLE_MP4)
        else:
            self._send(404, "text/plain", b"not found")

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WebSocketCDP:
    def __init__(self, ws_url: str):
        parsed = urllib.parse.urlparse(ws_url)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        self.sock.settimeout(10)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            response += self.sock.recv(4096)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError(response[:200])
        self.next_id = 1

    def close(self) -> None:
        self.sock.close()

    def command(self, method: str, params: dict | None = None, timeout: int = 15) -> dict:
        message_id = self.next_id
        self.next_id += 1
        self._send_frame(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                message = self._recv_message()
            except socket.timeout:
                continue
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result", {})
        raise TimeoutError(method)

    def _send_frame(self, payload: str) -> None:
        data = payload.encode("utf-8")
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def _recv_exact(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise EOFError("websocket closed")
            data += chunk
        return data

    def _recv_message(self) -> dict:
        chunks: list[bytes] = []
        while True:
            first, second = self._recv_exact(2)
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            masked = bool(second & 0x80)
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 8:
                raise EOFError("websocket closed")
            if opcode in (1, 0):
                chunks.append(payload)
                if fin:
                    return json.loads(b"".join(chunks).decode("utf-8"))


def main() -> int:
    browsers = find_browsers()
    if not browsers:
        print(json.dumps({"ok": False, "reason": "Chrome or Edge executable not found"}, ensure_ascii=False))
        return 1

    ensure_report_dirs()
    prepare_runtime()
    server = start_https_server()
    https_port = server.server_address[1]
    port_suffix = "" if https_port == 443 else f":{https_port}"
    test_url = f"https://ys.learnus.org{port_suffix}/course/view.php?id=123"
    result = {"tested_url": test_url, "ok": False, "attempts": []}

    try:
        for browser in browsers:
            for variant in extension_launch_variants():
                attempt = run_extension_attempt(browser, test_url, variant)
                result["attempts"].append(attempt)
                result.update(attempt.get("info") or {})
                if attempt.get("ok"):
                    result.update(
                        {
                            "ok": True,
                            "browser": attempt["browser_name"],
                            "headless": attempt["headless"],
                            "disable_extensions_except": attempt["disable_extensions_except"],
                            "screenshot": attempt["screenshot"],
                            "download_ok": attempt.get("download_ok", False),
                        }
                    )
                    write_result(result)
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return 0

        result["manual_injection"] = run_manual_injection_audit(browsers[0], test_url)
        write_result(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["manual_injection"].get("ok") else 1
    finally:
        server.shutdown()


def run_extension_attempt(browser: Path, test_url: str, variant: dict) -> dict:
    profile = reset_profile(f"profile-{browser.stem}-{variant['name']}")
    remote_port = free_port()
    process = launch_chromium(browser, profile, remote_port, variant, load_extension=True)
    cdp = None
    attempt = {
        "browser_name": browser.name,
        "browser": str(browser),
        "variant": variant["name"],
        "headless": variant["headless"],
        "disable_extensions_except": variant["disable_extensions_except"],
        "ok": False,
    }

    try:
        version = wait_json(remote_port, "/json/version", timeout=20)
        attempt["browser_version"] = version.get("Browser", "")
        attempt["extension_targets_before"] = extension_targets(remote_port, version)

        target = create_page(remote_port, test_url)
        cdp = WebSocketCDP(target["webSocketDebuggerUrl"])
        cdp.command("Page.enable")
        cdp.command("Runtime.enable")

        info = wait_for_audit_info(cdp)
        attempt["info"] = info
        attempt["extension_targets_after"] = extension_targets(remote_port, version)

        if info.get("panel"):
            screenshot_path = SCREENSHOT_DIR / "extension_render_audit.png"
            screenshot = cdp.command(
                "Page.captureScreenshot",
                {"format": "png", "captureBeyondViewport": True},
                timeout=20,
            )["data"]
            screenshot_path.write_bytes(base64.b64decode(screenshot))

            toggle = evaluate(cdp, TOGGLE_EXPRESSION)
            minimized_path = SCREENSHOT_DIR / "extension_render_audit_minimized.png"
            minimized = cdp.command(
                "Page.captureScreenshot",
                {"format": "png", "captureBeyondViewport": True},
                timeout=20,
            )["data"]
            minimized_path.write_bytes(base64.b64decode(minimized))

            safe_evaluate(cdp, TOGGLE_EXPRESSION)
            download_probe = run_download_probe(cdp, profile)
            downloaded_files = download_probe.get("downloaded_files", [])

            attempt["toggle"] = toggle
            attempt["screenshot"] = relative_artifact_path(screenshot_path)
            attempt["minimized_screenshot"] = relative_artifact_path(minimized_path)
            attempt["download_probe"] = download_probe
            attempt["downloaded_files"] = downloaded_files
            attempt["render_ok"] = (
                info.get("materials") == EXPECTED_MATERIALS
                and info.get("videos") == EXPECTED_VIDEOS
                and info.get("inlineButtons") == 1
                and bool(toggle.get("minimized"))
            )
            attempt["download_ok"] = (
                download_probe.get("materialsDone")
                and download_probe.get("videosDone")
                and len(downloaded_files) >= EXPECTED_DOWNLOADS
            )
            attempt["ok"] = attempt["render_ok"] and attempt["download_ok"]
    except Exception as exc:
        attempt["error"] = repr(exc)
    finally:
        if cdp:
            cdp.close()
        attempt["stderr_tail"] = stop_process(process)
        attempt["profile_extensions"] = read_profile_extension_state(profile)

    return attempt


def run_manual_injection_audit(browser: Path, test_url: str) -> dict:
    profile = reset_profile("profile-manual-injection")
    remote_port = free_port()
    variant = {"name": "manual", "headless": False, "disable_extensions_except": False}
    process = launch_chromium(browser, profile, remote_port, variant, load_extension=False)
    cdp = None
    result = {"ok": False, "browser_name": browser.name, "browser": str(browser)}

    try:
        version = wait_json(remote_port, "/json/version", timeout=20)
        result["browser_version"] = version.get("Browser", "")
        target = create_page(remote_port, test_url)
        cdp = WebSocketCDP(target["webSocketDebuggerUrl"])
        cdp.command("Page.enable")
        cdp.command("Runtime.enable")
        wait_for_document(cdp)

        css = (EXTENSION_SOURCE / "content.css").read_text(encoding="utf-8")
        content_js = (EXTENSION_SOURCE / "content.js").read_text(encoding="utf-8")
        inject_css = (
            "(() => {"
            "const style = document.createElement('style');"
            f"style.textContent = {json.dumps(css)};"
            "document.head.appendChild(style);"
            "})()"
        )
        cdp.command("Runtime.evaluate", {"expression": inject_css, "awaitPromise": True})
        cdp.command(
            "Runtime.evaluate",
            {"expression": content_js + "\n//# sourceURL=learnus-content-audit.js", "awaitPromise": True},
        )

        info = wait_for_audit_info(cdp)
        screenshot_path = SCREENSHOT_DIR / "extension_render_audit_manual.png"
        screenshot = cdp.command(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": True},
            timeout=20,
        )["data"]
        screenshot_path.write_bytes(base64.b64decode(screenshot))

        toggle = evaluate(cdp, TOGGLE_EXPRESSION) if info.get("panel") else {}
        minimized_path = SCREENSHOT_DIR / "extension_render_audit_manual_minimized.png"
        minimized = cdp.command(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": True},
            timeout=20,
        )["data"]
        minimized_path.write_bytes(base64.b64decode(minimized))

        result.update(
            {
                "info": info,
                "toggle": toggle,
                "screenshot": relative_artifact_path(screenshot_path),
                "minimized_screenshot": relative_artifact_path(minimized_path),
                "ok": (
                    info.get("panel")
                    and info.get("materials") == EXPECTED_MATERIALS
                    and info.get("videos") == EXPECTED_VIDEOS
                    and info.get("inlineButtons") == 1
                    and bool(toggle.get("minimized"))
                ),
            }
        )
    except Exception as exc:
        result["error"] = repr(exc)
    finally:
        if cdp:
            cdp.close()
        result["stderr_tail"] = stop_process(process)

    return result


AUDIT_EXPRESSION = """(() => {
  const panel = document.getElementById('learnus-downloader-panel');
  return {
    href: location.href,
    ready: document.readyState,
    bodyText: document.body?.innerText?.slice(0, 120) || '',
    panel: !!panel,
    title: document.querySelector('.learnus-downloader-title')?.textContent || '',
    materials: document.querySelector('[data-count="materials"]')?.textContent || '',
    videos: document.querySelector('[data-count="videos"]')?.textContent || '',
    inlineButtons: document.querySelectorAll('.learnus-inline-video-button').length,
    status: document.querySelector('[data-status]')?.textContent || ''
  };
})()"""

TOGGLE_EXPRESSION = """(() => {
  const button = document.querySelector('[data-action="toggle"]');
  button?.click();
  return {
    minimized: document.getElementById('learnus-downloader-panel')?.classList.contains('learnus-downloader-minimized') || false,
    label: button?.textContent || ''
  };
})()"""

STATUS_EXPRESSION = """(() => ({
  status: document.querySelector('[data-status]')?.textContent || '',
  busyButtons: Array.from(document.querySelectorAll('#learnus-downloader-panel button[disabled]')).length
}))()"""


def find_browsers() -> list[Path]:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    seen: set[Path] = set()
    browsers: list[Path] = []
    for candidate in candidates:
        if candidate.exists() and candidate not in seen:
            browsers.append(candidate)
            seen.add(candidate)
    return browsers


def extension_launch_variants() -> list[dict]:
    return [
        {"name": "headful-restricted", "headless": False, "disable_extensions_except": True},
        {"name": "headful-unrestricted", "headless": False, "disable_extensions_except": False},
        {"name": "headless-restricted", "headless": True, "disable_extensions_except": True},
    ]


def prepare_runtime() -> None:
    RUNTIME.mkdir(exist_ok=True)
    if EXTENSION_DIR.exists():
        shutil.rmtree(EXTENSION_DIR)
    shutil.copytree(EXTENSION_SOURCE, EXTENSION_DIR)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "KR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LearnUs Downloader Test"),
            x509.NameAttribute(NameOID.COMMON_NAME, "ys.learnus.org"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=7))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("ys.learnus.org")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    KEY.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


def reset_profile(name: str) -> Path:
    profile = RUNTIME / name
    if profile.exists():
        shutil.rmtree(profile)
    profile.mkdir(parents=True)
    default_dir = profile / "Default"
    default_dir.mkdir(parents=True)
    (default_dir / "Preferences").write_text(
        json.dumps(
            {
                "download": {
                    "default_directory": str(download_dir_for_profile(profile)),
                    "directory_upgrade": True,
                    "prompt_for_download": False,
                },
                "profile": {
                    "default_content_setting_values": {
                        "automatic_downloads": 1,
                    }
                },
                "safebrowsing": {
                    "enabled": False,
                    "disable_download_protection": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return profile


def download_dir_for_profile(profile: Path) -> Path:
    path = profile / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def start_https_server() -> ThreadingHTTPServer:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 443), TestPageHandler)
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), TestPageHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(CERT), str(KEY))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def launch_chromium(
    browser: Path,
    profile: Path,
    remote_port: int,
    variant: dict,
    *,
    load_extension: bool,
) -> subprocess.Popen:
    args = [
        str(browser),
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={remote_port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        "--ignore-certificate-errors",
        "--allow-insecure-localhost",
        "--disable-features=HttpsFirstBalancedModeAutoEnable,DownloadBubble,DownloadBubbleV2,msDownloadsHub",
        "--disable-sync",
        "--safebrowsing-disable-download-protection",
        "--window-size=1280,900",
        "--enable-logging=stderr",
        "--log-level=0",
        "--host-resolver-rules=MAP ys.learnus.org 127.0.0.1",
        "about:blank",
    ]
    if load_extension:
        args.insert(2, f"--load-extension={EXTENSION_DIR}")
        if variant["disable_extensions_except"]:
            args.insert(3, f"--disable-extensions-except={EXTENSION_DIR}")
    if variant["headless"]:
        args.insert(-1, "--headless=new")

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )


def stop_process(process: subprocess.Popen) -> str:
    if process.poll() is None:
        process.terminate()
    try:
        _, stderr = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        _, stderr = process.communicate(timeout=5)
    text = (stderr or b"").decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-25:])


def wait_json(port: int, path: str, timeout: int) -> dict:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"Chrome CDP not ready: {last_error}")


def create_page(port: int, url: str) -> dict:
    quoted = urllib.parse.quote(url, safe="")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{quoted}", method="PUT")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def extension_targets(port: int, version: dict) -> list[dict]:
    ws_url = version.get("webSocketDebuggerUrl")
    if not ws_url:
        return []
    cdp = WebSocketCDP(ws_url)
    try:
        targets = cdp.command("Target.getTargets", timeout=10).get("targetInfos", [])
    finally:
        cdp.close()
    interesting = []
    for target in targets:
        url = target.get("url", "")
        if url.startswith("chrome-extension://") or target.get("type") in {"service_worker", "background_page"}:
            interesting.append(
                {
                    "type": target.get("type", ""),
                    "title": target.get("title", ""),
                    "url": url,
                }
            )
    return interesting


def wait_for_document(cdp: WebSocketCDP) -> None:
    deadline = time.time() + 10
    while time.time() < deadline:
        info = evaluate(cdp, "(() => ({ ready: document.readyState }))()")
        if info.get("ready") == "complete":
            return
        time.sleep(0.25)


def wait_for_audit_info(cdp: WebSocketCDP) -> dict:
    info = {}
    for _ in range(40):
        info = evaluate(cdp, AUDIT_EXPRESSION)
        if info.get("panel"):
            break
        time.sleep(0.25)
    return info


def run_download_probe(cdp: WebSocketCDP, profile: Path) -> dict:
    material_click = safe_evaluate(
        cdp,
        """(() => {
          const button = document.querySelector('[data-action="download-materials"]');
          button?.click();
          return { clicked: !!button };
        })()""",
        timeout=5,
    )
    material_status = poll_status(cdp, "자료 다운로드 요청 완료") if not material_click.get("error") else {
        "ok": False,
        "status": material_click.get("error", ""),
    }

    video_click = safe_evaluate(
        cdp,
        """(() => {
          const button = document.querySelector('.learnus-inline-video-button');
          button?.click();
          return { clicked: !!button };
        })()""",
        timeout=5,
    )
    video_status = poll_status(cdp, "영상 다운로드 요청 완료") if not video_click.get("error") else {
        "ok": False,
        "status": video_click.get("error", ""),
    }
    downloaded_files = wait_for_downloaded_files(download_dir_for_profile(profile), expected_count=EXPECTED_DOWNLOADS)

    return {
        "materialClick": material_click,
        "videoClick": video_click,
        "materialsDone": material_status.get("ok", False),
        "videosDone": video_status.get("ok", False),
        "materialStatus": material_status.get("status", ""),
        "videoStatus": video_status.get("status", ""),
        "downloaded_files": downloaded_files,
    }


def poll_status(cdp: WebSocketCDP, expected: str, timeout: int = 20) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = evaluate(cdp, STATUS_EXPRESSION)
        status = last.get("status", "")
        if expected in status:
            return {"ok": True, "status": status}
        if status.startswith("오류:"):
            return {"ok": False, "status": status}
        time.sleep(0.25)
    return {"ok": False, "status": last.get("status", "timeout"), "timeout": True}


def safe_evaluate(cdp: WebSocketCDP, expression: str, timeout: int = 15) -> dict:
    try:
        return evaluate(cdp, expression, timeout=timeout)
    except Exception as exc:
        return {"error": repr(exc)}


def evaluate(cdp: WebSocketCDP, expression: str, timeout: int = 15) -> dict:
    result = cdp.command(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
        timeout=timeout,
    )["result"]
    return result.get("value") or {}


def wait_for_downloaded_files(download_dir: Path, expected_count: int, timeout: int = 20) -> list[dict]:
    deadline = time.time() + timeout
    files: list[Path] = []
    while time.time() < deadline:
        files = [
            path
            for path in download_dir.rglob("*")
            if path.is_file() and not path.name.endswith((".crdownload", ".tmp"))
        ]
        partials = list(download_dir.rglob("*.crdownload"))
        if len(files) >= expected_count and not partials:
            break
        time.sleep(0.25)

    return [
        {
            "path": str(path.relative_to(download_dir)).replace("\\", "/"),
            "bytes": path.stat().st_size,
        }
        for path in sorted(files)
    ]


def read_profile_extension_state(profile: Path) -> dict:
    state = {}
    for relative in ("Default/Preferences", "Default/Secure Preferences", "Local State"):
        path = profile / relative
        if not path.exists():
            state[relative] = {"exists": False}
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            state[relative] = {"exists": True, "error": repr(exc)}
            continue
        settings = data.get("extensions", {}).get("settings", {})
        state[relative] = {
            "exists": True,
            "extension_ids": list(settings.keys()),
            "settings_count": len(settings),
        }
    return state


def write_result(result: dict) -> None:
    ensure_report_dirs()
    safe_result = sanitize_json_value(result)
    (AUDIT_DIR / "extension_render_audit.json").write_text(
        json.dumps(safe_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def sanitize_json_value(value):
    if isinstance(value, str):
        return "".join(ch for ch in value if ord(ch) >= 32 or ch in "\n\r\t")
    if isinstance(value, list):
        return [sanitize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_json_value(item) for key, item in value.items()}
    return value


if __name__ == "__main__":
    sys.exit(main())
