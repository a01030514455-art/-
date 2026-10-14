"""로컬 픽스처로 추출 → 무료 대본 → 음성 → 영상 전 과정을 검증한다.

실행: python tests/test_e2e.py [--tts local|edge|auto]
실제 네이버 접속 없이 동작하므로, 실제 블로그 확인은 앱에서 링크를 넣어 따로 해야 한다.
"""
import argparse
import functools
import json
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from make_fixture import make  # noqa: E402
from reels_app import extractor, pipeline, script_rules  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tts", default="local")
    ap.add_argument("--speed", type=float, default=None)
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="reels_fixture_"))
    n_imgs = make(tmp)
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *x):
            pass
    handler = functools.partial(Quiet, directory=str(tmp))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/post.html"

    # URL 정규화
    assert extractor.normalize_url("https://blog.naver.com/abc/223456789")[0] == "https://m.blog.naver.com/abc/223456789"
    assert extractor.normalize_url("blog.naver.com/PostView.naver?blogId=abc&logNo=1")[1:] == ("abc", "1")
    try:
        extractor.normalize_url("https://blog.naver.com/abc")
        raise AssertionError("블로그 홈 주소는 거부해야 함")
    except extractor.ExtractError:
        pass

    post = extractor.extract(url, log=lambda m: None, out_root=tmp / "out", _allow_any_host=True)
    rep = post["report"]
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    assert rep["body_ok"], rep
    assert rep["images_found"] == n_imgs, (rep["images_found"], n_imgs)
    assert rep["images_downloaded"] == n_imgs
    assert rep["excluded_images"] == 1  # 본문 속 이모티콘. 프로필은 본문 밖, 스티커/링크 카드는 요소째 제외
    assert set(rep["skipped_components"]) == {"스티커", "링크 미리보기"}
    assert [b["index"] for b in post["images"]] == list(range(1, n_imgs + 1))
    big = [b for b in post["images"] if b["w"] >= 1200]
    assert len(big) == n_imgs, "원본 해상도로 저장되어야 함"
    assert post["images"][0]["caption"] == "들뜬 욕실 바닥 타일"

    script = script_rules.build(post, pipeline.load_settings())
    for s in script["scenes"]:
        print(f"[{s['kind']}] ({s['label']}) {s['narration']}  사진={[im['index'] for im in s['images']]}")
    assert script["scenes"][0]["kind"] == "hook" and script["scenes"][-1]["kind"] == "cta"
    assert not any("공감과 댓글" in s["narration"] for s in script["scenes"])

    job = pipeline.Job(url)
    job.post = post
    job.state["script"] = script
    job._render(a.tts, a.speed)
    r = job.state["result"]
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                       "stream=codec_name,width,height,r_frame_rate,pix_fmt:format=duration",
                                       "-of", "json", r["video"]], capture_output=True, text=True).stdout)
    v = next(s for s in probe["streams"] if s["codec_name"] == "h264")
    assert (v["width"], v["height"], v["r_frame_rate"], v["pix_fmt"]) == (1080, 1920, "30/1", "yuv420p"), v
    assert any(s["codec_name"] == "aac" for s in probe["streams"])
    dur = float(probe["format"]["duration"])
    print(f"\n영상: {r['video']}  {dur:.1f}초  사진 {r['images_used']}/{r['images_total']}  음성 {r['tts_engines']}")
    for w in r["warnings"]:
        print("주의:", w)
    assert 40 <= dur <= 60.5, dur
    assert r["images_used"] == n_imgs and r["photo_seconds"] > 0  # 본문 사진 전부를 한 장씩 한 번
    ovs = job.state["overlays"]
    assert any(o["auto"] == "sub" for o in ovs) and any(o["auto"] == "label" for o in ovs)  # 자동 문구도 편집 가능
    print("E2E OK")
    srv.shutdown()


if __name__ == "__main__":
    main()
