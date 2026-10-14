"""수동 편집 모듈 검증: 문구가 지정한 시간·위치에만 합성되는지, 썸네일 저장, 추천 3개.

실행: python tests/test_editing.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from reels_app import editing  # noqa: E402
from reels_app.config import ffmpeg_exe  # noqa: E402


def frame(video, t, out):
    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-ss", str(t), "-i", str(video), "-frames:v", "1", str(out)], check=True)
    return Image.open(out).convert("RGB")


def main():
    d = Path(tempfile.mkdtemp(prefix="reels_edit_"))
    base = d / "reel_base.mp4"
    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x202020:size=1080x1920:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "4", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(base)], check=True)
    ov = editing.clean_overlay({"text": "테스트", "size": 120, "color": "#ffffff", "bg": "#ff0000", "bg_opacity": 1,
                                "shadow": False, "stroke": False, "x": 0.5, "y": 0.5, "start": 1, "end": 2}, 4)
    out = editing.compose(base, [ov], d / "reel.mp4", log=lambda m: None)
    # 문구 상자 가장자리(빨간 배경 부분)를 확인
    img, (x, y) = editing.placed(ov)
    px = (x + int(ov["size"] * 0.3) + 12, y + img.height // 2)  # 왼쪽 여백(배경만 있는 곳)
    on = frame(out, 1.5, d / "a.png").getpixel(px)
    off = frame(out, 3.0, d / "b.png").getpixel(px)
    assert on[0] > 180 and on[1] < 80, on
    assert off[0] < 60, off
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True).stdout.split()
    assert probe == ["h264", "aac"], probe
    print("문구 합성 OK: 1~2초에만 표시, 음성 유지")

    # 썸네일: clean 프레임 + 문구, PNG/JPG
    (d / "frames").mkdir()
    for t in range(4):
        im = Image.new("RGB", (1080, 1920), (40 * t, 120, 200 - 30 * t))
        ImageDraw.Draw(im).rectangle((200, 600, 880, 1300), fill=(250, 250, 250) if t == 2 else (90, 90, 90))
        im.save(d / "frames" / f"clean_{t:03d}.jpg")
    for fmt in ("png", "jpg"):
        p = editing.make_thumbnail(d, {"time": 2, "text": "썸네일\n문구", "size": 130, "color": "#111111", "bg": "#F6C10A",
                                       "x": 0.5, "y": 0.2, "format": fmt})
        with Image.open(p) as im:
            assert im.size == (1080, 1920) and p.suffix == "." + fmt, (im.size, p)
    print("썸네일 OK: PNG/JPG 1080×1920")

    tl = [{"start": 0, "end": 1, "role": "hook", "kind": "hook"}, {"start": 1, "end": 2, "role": "why", "kind": "body"},
          {"start": 2, "end": 3, "role": "result", "kind": "body"}, {"start": 3, "end": 4, "role": "cta", "kind": "cta"}]
    rec = editing.recommend(d, tl)
    assert len(rec) == 3 and rec[0]["time"] == 2 and all(r["time"] != 3 for r in rec), rec  # 결과 장면 우선, 엔딩 제외
    print("추천 OK:", [(r["time"], r["reason"]) for r in rec])

    editing.TEMPLATES_FILE = d / "tpl.json"
    t = editing.save_template({"name": "내 문구", "text": "안녕", "size": 80, "color": "#ffffff"})
    assert t[-1]["name"] == "내 문구" and not t[-1]["builtin"]
    assert all(x["name"] != "내 문구" for x in editing.delete_template("내 문구"))
    print("템플릿 OK\nEDITING OK")


if __name__ == "__main__":
    main()
