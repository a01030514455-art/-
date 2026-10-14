"""내 PC에서만 열리는 웹앱 서버(파이썬 표준 라이브러리만 사용). 실행: python -m reels_app"""
import json
import mimetypes
import os
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import editing, hooks, images, pipeline, stock, voices
from .config import APP_VERSION, APP_DIR, IMAGES_DIR, OUTPUT_DIR, ROOT, ffmpeg_exe, load_settings

STATIC = APP_DIR / "static"
ALLOWED_FILE_ROOTS = [OUTPUT_DIR.resolve(), IMAGES_DIR.resolve()]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
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
        if ctype.startswith("text/html"):
            self.send_header("Cache-Control", "no-store")  # 업데이트 후 예전 화면이 남지 않게
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
        self._safe(self._get)

    def do_POST(self):
        self._safe(self._post)

    def _safe(self, fn):
        """어떤 오류가 나도 연결을 끊지 않고 원인을 화면에 돌려준다(검은 창과 output/app_error.log에도 기록)."""
        try:
            fn()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            tb = traceback.format_exc()
            print(tb)
            try:
                with open(OUTPUT_DIR / "app_error.log", "a", encoding="utf-8") as f:
                    f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + self.command + " " + self.path + "\n" + tb + "\n")
            except OSError:
                pass
            try:
                self._json({"error": f"앱 내부 오류: {type(e).__name__}: {e}  (자세한 내용: output/app_error.log)"}, 500)
            except Exception:
                pass

    def _get(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            html = (STATIC / "index.html").read_text(encoding="utf-8").replace("{{APP_VERSION}}", APP_VERSION).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")  # 업데이트 후 예전 화면이 남지 않게
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            return self.wfile.write(html)
        if path.startswith("/files/"):
            p = (ROOT / path[len("/files/"):]).resolve()
            if not any(p.is_relative_to(r) for r in ALLOWED_FILE_ROOTS):
                return self._json({"error": "forbidden"}, 403)
            return self._file(p)
        qs = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        if path == "/api/templates":
            return self._json({"templates": editing.load_templates(), "safe_zones": editing.SAFE_ZONES})
        if path == "/api/voices":
            return self._json(voices.list_voices(qs.get("engine", "edge"), load_settings()))
        if path == "/api/usage":
            s = load_settings()
            az = voices.azure_conf(s)
            return self._json({**voices.month_usage(), "azure_limit": voices.AZURE_FREE_CHARS, "azure_tier": az["tier"],
                               "azure_ready": bool(az["key"] and az["region"])})
        if path == "/api/status":
            s = load_settings()
            try:
                has_ffmpeg = bool(ffmpeg_exe())
            except RuntimeError:
                has_ffmpeg = False
            return self._json({"ffmpeg": has_ffmpeg, "tts_engine": s["tts_engine"],
                               "topics": hooks.choices(), "hook_styles": hooks.HOOK_STYLES,
                               "voice_default": {"engine": s["tts_engine"], "voice": s.get("tts_voice"),
                                                 "speed": s["tts_speed"], "volume": s.get("tts_volume", 1.0)},
                               "azure_ready": all(voices.azure_conf(s)[k] for k in ("key", "region")),
                               "azure_tier": voices.azure_conf(s)["tier"], "azure_region": voices.azure_conf(s)["region"],
                               "stock_ready": {k: bool(v) for k, v in stock.keys(s).items()},
                               "ai_ready": bool(s.get("anthropic_key") or os.environ.get("ANTHROPIC_API_KEY")),
                               "tts_speed": s["tts_speed"],
                               "ai_model": s["ai_model"], "jobs": pipeline.list_jobs()})
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[:2] == ["api", "jobs"]:
            try:
                job = pipeline.get(parts[2])
            except KeyError:
                return self._json({"error": "작업을 찾을 수 없습니다"}, 404)
            if len(parts) == 3:
                return self._json(job.public())
            if parts[3] == "frames":
                try:
                    return self._json(job.frames())
                except RuntimeError as e:
                    return self._json({"error": str(e)}, 409)
            if parts[3] == "ai_estimate":
                if not job.post:
                    return self._json({"error": "먼저 글을 추출하세요"}, 400)
                return self._json(job.ai_estimate())
        return self._json({"error": "not found"}, 404)

    def _post(self):
        parts = urlparse(self.path).path.strip("/").split("/")
        qs = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        try:
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "stock_import":
                n = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(n)
                job = pipeline.get(parts[2])
                return self._json(job.stock_import(data, qs.get("name", "clip.mp4"), qs.get("source_url", ""),
                                                   qs.get("author", ""), qs.get("query", "")))
            if parts == ["api", "settings", "voice"]:
                body = self._body()
                upd = {"tts_engine": body.get("engine") or "auto", "tts_voice": body.get("voice") or "ko-KR-SunHiNeural",
                       "tts_speed": round(min(1.6, max(0.7, float(body.get("speed") or 1.1))), 2),
                       "tts_volume": round(min(1.5, max(0.5, float(body.get("volume") or 1.0))), 2)}
                voices.save_local_settings(upd)
                return self._json({"ok": True, "saved": upd})
            if parts == ["api", "settings", "keys"]:
                body = self._body()
                upd = {k: str(body[k]).strip() for k in ("azure_key", "azure_region", "azure_tier", "pexels_key", "pixabay_key", "anthropic_key")
                       if body.get(k) not in (None, "")}
                if upd.get("azure_tier") and upd["azure_tier"] not in ("F0", "S0"):
                    return self._json({"error": "Azure 티어는 F0 또는 S0만 가능합니다"}, 400)
                voices.save_local_settings(upd)
                voices._voice_cache.clear()
                return self._json({"ok": True, "saved": sorted(upd)})
            if parts == ["api", "templates"]:
                return self._json({"templates": editing.save_template(self._body())})
            if parts == ["api", "templates", "delete"]:
                return self._json({"templates": editing.delete_template(self._body().get("name", ""))})
            if parts == ["api", "preview_voice"]:
                body = self._body()
                return self._json(pipeline.preview(body.get("text") or "", body, OUTPUT_DIR / "voice_preview"))
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "upload":
                n = int(self.headers.get("Content-Length") or 0)
                if n > images.MAX_UPLOAD:
                    return self._json({"error": "사진 파일이 너무 큽니다(40MB 이하)."}, 413)
                data = self.rfile.read(n)
                try:
                    job = pipeline.get(parts[2])
                except KeyError:
                    return self._json({"error": "작업을 찾을 수 없습니다"}, 404)
                return self._json(job.add_upload(data))
            body = self._body()
            if parts == ["api", "jobs"]:
                url = (body.get("url") or "").strip()
                if not url:
                    return self._json({"error": "블로그 주소를 입력하세요"}, 400)
                job = pipeline.create(url)
                job.start_extract()
                return self._json({"id": job.id})
            if len(parts) == 4 and parts[:2] == ["api", "jobs"]:
                try:
                    job = pipeline.get(parts[2])
                except KeyError:
                    return self._json({"error": "작업을 찾을 수 없습니다"}, 404)
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
                    job.start_render(body.get("tts_engine"), body.get("tts_speed"),
                                     {"voice": body.get("voice"), "volume": body.get("volume")},
                                     bool(body.get("auto_stock")), bool(body.get("approved")))
                    return self._json({"ok": True})
                if action == "overlays":
                    return self._json({"overlays": job.save_overlays(body.get("overlays") or [], body.get("auto_style"))})
                if action == "apply_overlays":
                    job.save_overlays(body.get("overlays") or job.state.get("overlays", []), body.get("auto_style"))
                    job.start_apply_overlays()
                    return self._json({"ok": True})
                if action == "thumbnail":
                    return self._json(job.thumbnail(body))
                if action == "stock_search":
                    return self._json(stock.search(body.get("provider", "pexels"), body.get("query", ""), load_settings()))
                if action == "stock_add":
                    return self._json(job.stock_add(body.get("item") or {}, body.get("query", "")))
                if action == "stock_remove":
                    return self._json(job.stock_remove(body.get("clip_id", "")))
                if action == "rebuild":
                    return self._json(job.rebuild_script(body.get("topic_id") or "auto",
                                                         body.get("hook_style") or "question"))
                if action == "image_delete":
                    return self._json(job.delete_image(body.get("index")))
                if action == "image_edit":
                    return self._json(job.edit_image(body.get("index"), body.get("edits") or {}))
                if action == "preview":
                    return self._json(job.preview_voice(body.get("text") or "", body))
        except pipeline.ApprovalNeeded as e:
            return self._json({"error": str(e), "need_approval": e.info}, 402)
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
    print(f"Blog Reels Maker {APP_VERSION} running: {url}  (stop: Ctrl+C)")
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
