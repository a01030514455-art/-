"""수동 편집: 추가 문구(텍스트 박스) 합성, 썸네일 제작, 썸네일 후보 자동 추천, 문구 템플릿.

비용 원칙
- AI·음성 API를 다시 부르지 않는다. 문구는 이미 만든 기본 영상(reel_base.mp4) 위에 FFmpeg overlay로만 합성한다.
- 썸네일 후보 추천은 무료 이미지 분석(선명도·대비·채도·밝기 + 장면 역할)으로 한다.

좌표 규칙(화면과 서버가 똑같이 사용)
- x, y: 문구 상자 '가운데'의 위치. 1080×1920 화면 대비 0~1 비율.
- size: 1080 폭 기준 글자 크기(px).
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageStat

from .config import OUTPUT_DIR, ffmpeg_exe
from .render import FPS, H, W, font

TEMPLATES_FILE = OUTPUT_DIR / "text_templates.json"

DEFAULT_STYLE = {"text": "문구를 입력하세요", "size": 72, "color": "#ffffff", "bg": "", "bg_opacity": 0.85,
                 "shadow": True, "stroke": True, "x": 0.5, "y": 0.3, "start": 0.0, "end": 3.0,
                 # 자동으로 만든 문구: auto = "sub"(자막) | "label"(상단·엔딩 문구) | ""(직접 추가). hl = 강조 단어
                 "auto": "", "hl": [], "hl_color": "", "base_y": None, "base_size": None}
AUTO_GROUPS = {"sub": "자동 자막", "label": "자동 상단·엔딩 문구"}

BUILTIN_TEMPLATES = [
    {"name": "노란 강조 박스", "text": "부분 보수로 비용 절감!", "size": 76, "color": "#111111", "bg": "#F6C10A",
     "bg_opacity": 1.0, "shadow": False, "stroke": False, "y": 0.2},
    {"name": "BEFORE 라벨", "text": "BEFORE", "size": 84, "color": "#ffffff", "bg": "#D64545", "bg_opacity": 0.95,
     "shadow": False, "stroke": False, "x": 0.25, "y": 0.24},
    {"name": "AFTER 라벨", "text": "AFTER", "size": 84, "color": "#ffffff", "bg": "#507AFF", "bg_opacity": 0.95,
     "shadow": False, "stroke": False, "x": 0.25, "y": 0.24},
    {"name": "문의 유도", "text": "상담 문의는 프로필 링크", "size": 60, "color": "#ffffff", "bg": "#000000",
     "bg_opacity": 0.6, "shadow": False, "stroke": False, "y": 0.68},
    {"name": "흰 글씨 + 그림자", "text": "시공 현장 공개", "size": 88, "color": "#ffffff", "bg": "", "shadow": True,
     "stroke": True, "y": 0.2},
]

# 릴스/쇼츠 화면에서 앱 UI(상단 메뉴·하단 설명·오른쪽 버튼)에 가려지기 쉬운 영역(1080×1920 기준, 대략값)
SAFE_ZONES = {
    "reels": {"name": "인스타 릴스", "top": 220, "bottom": 420, "left": 60, "right": 140},
    "shorts": {"name": "유튜브 쇼츠", "top": 200, "bottom": 380, "left": 60, "right": 140},
}


def clean_overlay(o: dict, duration: float) -> dict:
    out = dict(DEFAULT_STYLE)
    out.update({k: o[k] for k in DEFAULT_STYLE if k in o})
    out["id"] = str(o.get("id") or f"ov{int(time.time() * 1000) % 10**9}")
    out["text"] = str(out["text"])[:300]
    out["size"] = int(min(220, max(20, float(out["size"]))))
    for k in ("x", "y"):
        out[k] = round(min(1.0, max(0.0, float(out[k]))), 4)
    out["bg_opacity"] = round(min(1.0, max(0.0, float(out["bg_opacity"]))), 2)
    out["start"] = round(min(duration, max(0.0, float(out["start"]))), 2)
    out["end"] = round(min(duration, max(out["start"] + 0.1, float(out["end"]))), 2)
    for k in ("color", "bg"):
        v = str(out[k] or "")
        out[k] = v if (v == "" and k == "bg") or (v.startswith("#") and len(v) in (4, 7)) else DEFAULT_STYLE[k]
    out["shadow"] = bool(out["shadow"])
    out["stroke"] = bool(out["stroke"])
    out["auto"] = out["auto"] if out["auto"] in AUTO_GROUPS else ""
    out["hl"] = [str(h)[:20] for h in (out["hl"] or []) if str(h).strip()][:5]
    hc = str(out["hl_color"] or "")
    out["hl_color"] = hc if hc.startswith("#") and len(hc) in (4, 7) else ""
    out["base_y"] = out["y"] if out["base_y"] is None else round(float(out["base_y"]), 4)
    out["base_size"] = out["size"] if out["base_size"] is None else int(out["base_size"])
    return out


def apply_auto_style(o: dict, style: dict) -> dict:
    """그룹 설정(위치 이동·크기 비율·색 등)을 자동 문구 하나에 적용."""
    st = (style or {}).get(o.get("auto"), {})
    if not st:
        return o
    o = dict(o)
    if "dy" in st:
        o["y"] = round(min(1.0, max(0.0, o["base_y"] + float(st["dy"]))), 4)
    if "dx" in st:
        o["x"] = round(min(1.0, max(0.0, 0.5 + float(st["dx"]))), 4)
    if "scale" in st:
        o["size"] = int(min(220, max(20, o["base_size"] * float(st["scale"]))))
    for k in ("color", "hl_color", "bg", "bg_opacity", "shadow", "stroke"):
        if k in st:
            o[k] = st[k]
    return o


def _rgb(hexs):
    h = hexs.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def text_box(o: dict) -> Image.Image:
    """문구 상자 하나를 투명 PNG로 그린다(화면 미리보기와 같은 규칙: 줄 간격 1.2, 배경 여백 0.35em/0.18em)."""
    size = o["size"]
    f = font(size)
    lines = o["text"].split("\n") or [""]
    lh = int(size * 1.2)
    widths = [f.getlength(l) for l in lines]
    tw, th = int(max(widths + [1])), lh * len(lines)
    padx, pady = (int(size * 0.35), int(size * 0.18)) if o["bg"] else (int(size * 0.1), int(size * 0.05))
    stroke = max(2, int(size * 0.06)) if o["stroke"] else 0
    margin = int(size * 0.3)
    bw, bh = tw + padx * 2 + stroke * 2, th + pady * 2 + stroke * 2
    img = Image.new("RGBA", (bw + margin * 2, bh + margin * 2), (0, 0, 0, 0))
    if o["bg"]:
        ImageDraw.Draw(img).rounded_rectangle((margin, margin, margin + bw, margin + bh), radius=int(size * 0.2),
                                              fill=_rgb(o["bg"]) + (int(255 * o["bg_opacity"]),))
    txt = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(txt)
    color = _rgb(o["color"]) + (255,)
    hl_col = _rgb(o["hl_color"]) + (255,) if o.get("hl_color") else color
    hls = o.get("hl") or []
    y = margin + pady + stroke
    for line, lw in zip(lines, widths):
        x = margin + (bw - lw) / 2
        words = line.split(" ")
        for wi, word in enumerate(words):  # 강조 단어만 다른 색
            c = hl_col if any(h in word for h in hls) else color
            d.text((x, y + (lh - size) / 2 - size * 0.08), word, font=f, fill=c,
                   stroke_width=stroke, stroke_fill=(10, 10, 12, 255))
            x += f.getlength(word + (" " if wi < len(words) - 1 else ""))
        y += lh
    if o["shadow"]:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sh.paste((0, 0, 0, 170), (0, 0), txt.split()[3])
        sh = sh.filter(ImageFilter.GaussianBlur(max(2, size * 0.06)))
        off = int(size * 0.05)
        img.alpha_composite(sh, (off, off))
    img.alpha_composite(txt)
    return img


def placed(o: dict):
    """(PNG, 왼쪽 위 좌표)"""
    img = text_box(o)
    return img, (int(o["x"] * W - img.width / 2), int(o["y"] * H - img.height / 2))


def compose(base: Path, overlays: list, out: Path, log=print, progress=None) -> Path:
    """기본 영상 위에 문구를 시간대별로 합성. 문구가 없으면 그대로 복사."""
    if not overlays:
        shutil.copyfile(base, out)
        return out
    layers = [(o["start"], o["end"]) + placed(o) for o in overlays]  # 문구 그림은 한 번만 만든다
    tmp = out.with_suffix(".tmp.mp4")
    dec = subprocess.Popen([ffmpeg_exe(), "-v", "error", "-i", str(base), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    enc = subprocess.Popen([ffmpeg_exe(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-i", str(base), "-map", "0:v", "-map", "1:a?", "-c:v", "libx264",
                            "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "copy",
                            "-movflags", "+faststart", str(tmp)], stdin=subprocess.PIPE)
    size, n = W * H * 3, 0
    try:
        while True:
            buf = dec.stdout.read(size)
            if len(buf) < size:
                break
            t = n / FPS
            active = [(img, pos) for s, e, img, pos in layers if s <= t <= e]
            if active:
                fr = Image.frombytes("RGB", (W, H), buf)
                for img, pos in active:
                    fr.paste(img, pos, img)
                buf = fr.tobytes()
            enc.stdin.write(buf)
            n += 1
            if progress and n % 30 == 0:
                progress(n)
        enc.stdin.close()
        if enc.wait() != 0:
            raise RuntimeError("문구 합성 인코딩 실패")
    finally:
        dec.kill()
        dec.wait()
    tmp.replace(out)
    log(f"문구 {len(overlays)}개 합성 완료")
    return out


# ---------------- 썸네일 ----------------
def frame_at(job_dir: Path, t: float, clean=True) -> Path:
    """썸네일용 프레임. clean이면 자막·라벨 없는 프레임(렌더링 때 1초마다 저장한 것)을 쓴다."""
    frames = job_dir / "frames"
    sec = int(round(t))
    p = frames / f"clean_{sec:03d}.jpg"
    if clean and p.exists():
        return p
    src = job_dir / "reel.mp4"  # 문구가 들어간 최종본에서 잡는다
    if not src.exists():
        src = job_dir / "reel_base.mp4"
    frames.mkdir(exist_ok=True)
    out = frames / f"full_{t:07.2f}.jpg"
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                        "-q:v", "2", str(out)], check=True)
    return out


def make_thumbnail(job_dir: Path, spec: dict) -> Path:
    t = float(spec.get("time", 0))
    base = Image.open(frame_at(job_dir, t, bool(spec.get("clean", True)))).convert("RGB").resize((W, H))
    canvas = base.convert("RGBA")
    if (spec.get("text") or "").strip():
        o = clean_overlay(dict(spec, start=0, end=1), 9999)
        img, pos = placed(o)
        canvas.alpha_composite(img, pos)
    fmt = "jpg" if str(spec.get("format", "png")).lower() in ("jpg", "jpeg") else "png"
    out = job_dir / f"thumbnail.{fmt}"
    if fmt == "jpg":
        canvas.convert("RGB").save(out, quality=92)
    else:
        canvas.save(out)
    return out


def recommend(job_dir: Path, timeline: list, n=3) -> list:
    """무료 이미지 분석으로 썸네일 후보 3개: 선명도·대비·채도·밝기 + 장면 역할(결과·훅 가산), 서로 다른 장면에서."""
    frames = sorted((job_dir / "frames").glob("clean_*.jpg"))
    role_bonus = {"result": 18, "hook": 10, "process": 6, "point": 4, "situation": 4}
    scored = []
    for f in frames:
        t = int(f.stem.split("_")[1])
        sc = next((s for s in timeline if s["start"] <= t < s["end"]), None)
        if not sc or sc.get("kind") == "cta":
            continue
        im = Image.open(f).convert("RGB").resize((135, 240))
        g = im.convert("L")
        sharp = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).mean[0]
        contrast = ImageStat.Stat(g).stddev[0]
        bright = ImageStat.Stat(g).mean[0]
        sat = ImageStat.Stat(im.convert("HSV").split()[1]).mean[0]
        score = sharp * 1.2 + contrast * 0.5 + sat * 0.25 - abs(bright - 128) * 0.3 + role_bonus.get(sc.get("role"), 0)
        scored.append({"time": t, "score": round(score, 1), "scene": timeline.index(sc), "role": sc.get("role", ""),
                       "label": sc.get("label", ""), "url_name": f.name})
    picks, ranked = [], sorted(scored, key=lambda c: -c["score"])
    # 1순위: 서로 다른 장면이면서 3초 이상 떨어진 것 → 부족하면 다른 장면이기만 하면 → 그래도 부족하면 아무거나
    for rule in (lambda c, p: c["scene"] != p["scene"] and abs(c["time"] - p["time"]) >= 3,
                 lambda c, p: c["scene"] != p["scene"], lambda c, p: c["time"] != p["time"]):
        for c in ranked:
            if len(picks) < n and c not in picks and all(rule(c, p) for p in picks):
                picks.append(c)
    reasons = {"result": "결과(완성) 장면", "hook": "첫 장면(훅)", "process": "작업 과정 장면", "point": "핵심 포인트 장면"}
    for p in picks:
        p["reason"] = reasons.get(p["role"], "선명하고 대비가 좋은 장면")
    return picks


# ---------------- 문구 템플릿 ----------------
def load_templates() -> list:
    try:
        user = json.loads(TEMPLATES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        user = []
    return [dict(t, builtin=True) for t in BUILTIN_TEMPLATES] + [dict(t, builtin=False) for t in user]


def save_template(t: dict) -> list:
    name = str(t.get("name") or "").strip()[:40]
    if not name:
        raise RuntimeError("템플릿 이름을 입력하세요.")
    try:
        user = json.loads(TEMPLATES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        user = []
    keep = {k: t[k] for k in ("text", "size", "color", "bg", "bg_opacity", "shadow", "stroke", "x", "y") if k in t}
    user = [u for u in user if u["name"] != name] + [dict(keep, name=name)]
    TEMPLATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    TEMPLATES_FILE.write_text(json.dumps(user, ensure_ascii=False, indent=1), encoding="utf-8")
    return load_templates()


def delete_template(name: str) -> list:
    try:
        user = json.loads(TEMPLATES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        user = []
    TEMPLATES_FILE.write_text(json.dumps([u for u in user if u["name"] != name], ensure_ascii=False, indent=1),
                              encoding="utf-8")
    return load_templates()
