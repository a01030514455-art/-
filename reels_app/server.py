"""내 PC에서만 열리는 웹앱 서버(파이썬 표준 라이브러리만 사용). 실행: python -m reels_app"""
import json
import mimetypes
import shutil
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import pipeline
from .config import APP_DIR, IMAGES_DIR, OUTPUT_DIR, ROOT, load_settings

STATIC = APP_DIR / "static"
ALLOWED_FILE_ROOTS = [OUTPUT_DIR.resolve(), IMAGES_DIR.resolve()]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _file(self, path: Path):
        if not path.is_file():
            return self._json({"error": "not found"}, 404)
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng and rng.startswith("bytes="):  # 영상 탐색(seek) 지원
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(b))
            end = int(b) if a and b else size - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            return self._file(STATIC / "index.html")
        if path.startswith("/files/"):
            p = (ROOT / path[len("/files/"):]).resolve()
            if not any(p.is_relative_to(r) for r in ALLOWED_FILE_ROOTS):
                return self._json({"error": "forbidden"}, 403)
            return self._file(p)
        if path == "/api/status":
            s = load_settings()
            return self._json({"ffmpeg": bool(shutil.which("ffmpeg")), "tts_engine": s["tts_engine"],
                               "ai_model": s["ai_model"], "jobs": pipeline.list_jobs()})
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[:2] == ["api", "jobs"]:
            try:
                job = pipeline.get(parts[2])
            except KeyError:
                return self._json({"error": "작업을 찾을 수 없습니다"}, 404)
            if len(parts) == 3:
                return self._json(job.public())
            if parts[3] == "ai_estimate":
                if not job.post:
                    return self._json({"error": "먼저 글을 추출하세요"}, 400)
                return self._json(job.ai_estimate())
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        parts = urlparse(self.path).path.strip("/").split("/")
        try:
            body = self._body()
            if parts == ["api", "jobs"]:
                url = (body.get("url") or "").strip()
                if not url:
                    return self._json({"error": "블로그 주소를 입력하세요"}, 400)
                job = pipeline.create(url)
                job.start_extract()
                return self._json({"id": job.id})
            if len(parts) == 4 and parts[:2] == ["api", "jobs"]:
                job = pipeline.get(parts[2])
                action = parts[3]
                if action == "script":
                    job.update_script(body)
                    return self._json({"ok": True})
                if action == "ai_script":
                    if body.get("approved") is not True:  # 승인 없이는 절대 유료 호출하지 않음
                        return self._json({"error": "예상 비용 승인이 필요합니다"}, 400)
                    job.start_ai()
                    return self._json({"ok": True})
                if action == "render":
                    job.start_render(body.get("tts_engine"))
                    return self._json({"ok": True})
        except KeyError:
            return self._json({"error": "작업을 찾을 수 없습니다"}, 404)
        except RuntimeError as e:
            return self._json({"error": str(e)}, 409)
        return self._json({"error": "not found"}, 404)


def main(open_browser=True):
    s = load_settings()
    OUTPUT_DIR.mkdir(exist_ok=True)
    IMAGES_DIR.mkdir(exist_ok=True)
    srv = ThreadingHTTPServer((s["host"], s["port"]), Handler)
    url = f"http://{s['host']}:{s['port']}/"
    print(f"블로그 릴스 메이커 실행 중: {url}  (종료: Ctrl+C)")
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
