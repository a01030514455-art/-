"""Azure 음성·무료 스톡 영상 기능을 가짜(모의) 서버로 검증한다. 실제 Azure/Pexels/Pixabay에는 접속하지 않는다.

실행: python tests/test_voice_stock.py
"""
import functools
import io
import json
import math
import re
import struct
import subprocess
import sys
import tempfile
import threading
import wave
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from make_fixture import make  # noqa: E402
from reels_app import extractor, pipeline, stock, voices  # noqa: E402
from reels_app.config import DEFAULTS, ffmpeg_exe  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="reels_vs_"))
CLIP = TMP / "clip.mp4"
SEEN = {"ssml": [], "keys": set()}


def wav_bytes(seconds):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        n = int(seconds * 48000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i * 2 * math.pi * 220 / 48000))) for i in range(n)))
    return buf.getvalue()


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json", code=200, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        base = f"http://127.0.0.1:{self.server.server_port}"
        if self.path.startswith("/cognitiveservices/voices/list"):
            assert self.headers["Ocp-Apim-Subscription-Key"] == "testkey"
            vs = [{"ShortName": "ko-KR-SunHiNeural", "LocalName": "선히", "Gender": "Female", "Locale": "ko-KR", "VoiceType": "Neural"},
                  {"ShortName": "ko-KR-InJoonNeural", "LocalName": "인준", "Gender": "Male", "Locale": "ko-KR", "VoiceType": "Neural"},
                  {"ShortName": "en-US-JennyNeural", "LocalName": "Jenny", "Gender": "Female", "Locale": "en-US"}]
            return self._send(json.dumps(vs).encode())
        if self.path.startswith("/v1/videos/search"):
            assert self.headers["Authorization"] == "pexkey"
            SEEN["keys"].add("pexels")
            data = {"videos": [{"id": 111, "url": "https://www.pexels.com/video/111/", "image": base + "/thumb.jpg", "duration": 3,
                                "user": {"name": "Tester", "url": "https://www.pexels.com/@tester"},
                                "video_files": [{"link": base + "/files/clip.mp4", "width": 1080, "height": 1920},
                                                {"link": base + "/files/big.mp4", "width": 2160, "height": 3840}]}]}
            return self._send(json.dumps(data).encode(), headers={"X-Ratelimit-Remaining": "199"})
        if self.path.startswith("/api/videos/"):
            assert "key=pixkey" in self.path
            data = {"hits": [{"id": 222, "pageURL": "https://pixabay.com/videos/id-222/", "user": "pixuser", "user_id": 9,
                              "duration": 3, "tags": "bathroom",
                              "videos": {"medium": {"url": base + "/files/clip.mp4", "width": 1280, "height": 720, "thumbnail": base + "/t.jpg"}}}]}
            return self._send(json.dumps(data).encode())
        if self.path.startswith("/files/clip.mp4"):
            return self._send(CLIP.read_bytes(), "video/mp4")
        self._send(b"{}", code=404)

    def do_POST(self):
        if self.path.startswith("/cognitiveservices/v1"):
            assert self.headers["Ocp-Apim-Subscription-Key"] == "testkey"
            assert self.headers["X-Microsoft-OutputFormat"] == "riff-48khz-16bit-mono-pcm"
            ssml = self.rfile.read(int(self.headers["Content-Length"])).decode()
            SEEN["ssml"].append(ssml)
            text = re.sub(r"<[^>]+>", "", ssml)
            return self._send(wav_bytes(max(0.6, len(text) / 7)), "audio/wav")
        self._send(b"{}", code=404)


def main():
    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30",
                    "-t", "3", "-pix_fmt", "yuv420p", str(CLIP)], check=True)
    mock = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{mock.server_port}"

    # 설정을 테스트용으로 바꿈(실제 settings.local.json은 건드리지 않음)
    settings = json.loads(json.dumps(DEFAULTS))
    settings.update({"azure_key": "testkey", "azure_region": "koreacentral", "azure_endpoint": base, "azure_tier": "F0",
                     "pexels_key": "pexkey", "pixabay_key": "pixkey", "tts_engine": "azure",
                     "tts_voice": "ko-KR-InJoonNeural", "tts_volume": 1.2})
    pipeline.load_settings = lambda: json.loads(json.dumps(settings))
    voices.USAGE_FILE = TMP / "tts_usage.json"
    stock.CACHE_DIR = TMP / "cache"
    stock.LOG_FILE = TMP / "stock_log.jsonl"
    stock.PEXELS_API = base + "/v1/videos/search"
    stock.PIXABAY_API = base + "/api/videos/"
    stock.DOWNLOAD_HOSTS = ("127.0.0.1",)

    # 1) 음성 목록: 한국어만, 남녀 구분
    vl = voices.list_voices("azure", settings)
    assert not vl["error"], vl
    assert [v["name"] for v in vl["voices"]] == ["ko-KR-InJoonNeural", "ko-KR-SunHiNeural"], vl
    assert {v["gender_ko"] for v in vl["voices"]} == {"남성", "여성"}
    print("음성 목록 OK:", [(v["gender_ko"], v["label"]) for v in vl["voices"]])

    # 2) 글자 수·한도 판단
    ssml = voices.azure_ssml("안녕 AB", "ko-KR-InJoonNeural", 1.1, 1.2)
    assert "rate='+10%'" in ssml and "volume='+20%'" in ssml
    assert voices.billable_chars(ssml) == len(ssml) - len("<speak version='1.0' xml:lang='ko-KR'><voice name='ko-KR-InJoonNeural'>") - len("</voice></speak>") + 2
    assert voices.azure_precheck(1000, settings)["level"] == "ok"
    voices.USAGE_FILE.write_text(json.dumps({voices._month(): {"azure_free": 450_000}}))
    assert voices.azure_precheck(1000, settings)["level"] == "warn"
    assert voices.azure_precheck(60_000, settings)["level"] == "block"
    assert voices.azure_precheck(1000, dict(settings, azure_tier="S0"))["level"] == "billable"
    voices.USAGE_FILE.unlink()
    print("한도 판단 OK: ok / 80% 경고 / 초과 차단 / S0 과금 승인")

    # 3) 작업 준비(로컬 픽스처 글)
    fx = TMP / "site"
    make(fx)

    class Q(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    site = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Q, directory=str(fx)))
    threading.Thread(target=site.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{site.server_port}/post.html"
    job = pipeline.Job(url)
    job.post = extractor.extract(url, log=lambda m: None, out_root=TMP / "imgs", _allow_any_host=True)
    assert job.post["report"]["body_ok"], job.post["report"]["errors"]
    job._save_post()
    from reels_app import script_rules
    job.state["script"] = script_rules.build(job.post, settings)

    # 4) 스톡 검색·추가·기록
    r = stock.search("pexels", "욕실", settings)
    assert r["query_en"] == "bathroom renovation" and r["items"][0]["width"] == 1080, r  # 4K 제외, FHD 선택
    assert r["quota_remaining"] == "199"
    r2 = stock.search("pexels", "욕실", settings)
    assert r2["cache"] == "hit"  # 24시간 캐시
    p = stock.search("pixabay", "누수", settings)
    assert p["items"][0]["author"] == "pixuser"
    mk = stock.search("mixkit", "타일", settings)
    assert mk["manual"] and not mk["items"]  # 자동 다운로드 없음
    clip = job.stock_add(r["items"][0], "욕실")
    assert clip["license"] == "Pexels License" and Path(clip["file"]).exists()
    try:
        stock.download(dict(r["items"][0], download="https://evil.example.com/x.mp4"), job.dir, "x")
        raise AssertionError("허용되지 않은 주소는 거부해야 함")
    except RuntimeError:
        pass
    try:
        job.stock_import(b"x", "a.mp4", "https://example.com/v", "", "")
        raise AssertionError("Mixkit 페이지 주소가 아니면 거부해야 함")
    except RuntimeError:
        pass
    job.stock_import(CLIP.read_bytes(), "mixkit.mp4", "https://mixkit.co/free-stock-video/test-123/", "Mixkit 작가", "타일")
    log = [json.loads(x) for x in stock.LOG_FILE.read_text(encoding="utf-8").splitlines()]
    assert {x["provider"] for x in log} == {"pexels", "mixkit"} and all(x["license_url"] for x in log)
    print("스톡 OK: 검색·캐시·FHD 선택·출처 기록·주소 제한·Mixkit 수동 등록")

    # 5) Azure F0로 영상 만들기 + 보조 영상 자동 삽입
    job.start_render("azure", 1.1, {"voice": "ko-KR-InJoonNeural", "volume": 1.2}, auto_stock=True)
    import time
    while job.busy:
        time.sleep(0.5)
    assert job.state["status"] == "done", job.state["error"]
    res = job.state["result"]
    assert res["clips_used"], "설명 장면에 보조 영상이 들어가야 함"
    assert Path(res["credits"]).read_text(encoding="utf-8").startswith("보조 영상 출처")
    assert all("ko-KR-InJoonNeural" in s and "volume='+20%'" in s for s in SEEN["ssml"])
    u = job.state["usage"]
    assert u["tts_free"] and u["tts_breakdown"].get("azure_free", 0) > 0, u
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,r_frame_rate,codec_name",
                                       "-of", "json", res["video"]], capture_output=True, text=True).stdout)
    v = next(s for s in probe["streams"] if s["codec_name"] == "h264")
    assert (v["width"], v["height"], v["r_frame_rate"]) == (1080, 1920, "30/1")
    assert 40 <= res["seconds"] <= 60.5, res["seconds"]
    print(f"Azure F0 렌더 OK: {res['seconds']}초, 보조 영상 {len(res['clips_used'])}개, 무료 처리 {u['tts_breakdown']}")

    # 6) S0(유료)는 승인 없이 시작하지 않음
    settings["azure_tier"] = "S0"
    try:
        job.start_render("azure", 1.0, {"voice": "ko-KR-SunHiNeural"})
        raise AssertionError("S0는 승인 필요")
    except pipeline.ApprovalNeeded as e:
        assert e.info["level"] == "billable" and e.info["billable_usd"] > 0
        print("S0 승인 요청 OK:", e.info["message"][:60], "…")
    print("VOICE+STOCK OK", res["video"])
    mock.shutdown()
    site.shutdown()


if __name__ == "__main__":
    main()
