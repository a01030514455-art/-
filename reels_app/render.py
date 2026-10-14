"""대본 → 9:16 FHD 30fps H.264 MP4. AI 없이 Python(Pillow) + FFmpeg만 사용.

레이아웃(레퍼런스 측정값, 1080×1920 기준)
- 배경: 위→아래 어두운 그라데이션(테마)
- 상단 라벨: 중심 y≈360, 흰색 굵은 글씨 + 그림자 / 첫 장면은 노란 큰 제목
- 사진 밴드: 중심 y≈980. 1장이면 가로 전체(세로 760~1140), 2장이면 좌우 나란히
- 자막: 2줄, 중심 y≈1450/1555, 흰색 굵은 글씨 + 검은 외곽선, 핵심 단어만 강조색
- 엔딩: 상단 상담 문구 + 검은 패널 속 로고 + CTA 자막
- 사진은 천천히 확대/이동(켄 번스), 컷 전환
"""
import re
import shutil
import subprocess
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from . import tts
from .config import bold_font_path, ffmpeg_exe, theme
from .script_rules import speech_len

W, H, FPS = 1080, 1920, 30
GAP_AFTER = 0.22          # 장면 사이 숨 고르기(초)
CTA_HOLD = 1.2            # 엔딩 화면 추가 유지(초)
MIN_PANEL_SEC = 1.3       # 사진 한 컷 최소 노출 시간
SUB_Y = (1450, 1555)
LABEL_Y = 360
BAND_CY = 980

_fonts = {}


def font(size):
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(bold_font_path(), size)
    return _fonts[size]


# ---------- 텍스트 ----------
def _wrap(words, f, max_w):
    lines, cur = [], []
    for w in words:
        trial = " ".join(cur + [w])
        if cur and f.getlength(trial) > max_w:
            lines.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def subtitle_chunks(text, size=92, max_w=960):
    """내레이션을 화면에 2줄씩 뜨는 자막 조각으로 나눈다."""
    f = font(size)
    lines = _wrap(text.split(), f, max_w * 0.82)  # 레퍼런스처럼 한 줄을 짧게
    chunks = [lines[i:i + 2] for i in range(0, len(lines), 2)]
    # 마지막 조각이 한 단어뿐이면 앞 조각과 합쳐 어색함 줄이기
    if (len(chunks) >= 2 and len(chunks[-1]) == 1 and len(chunks[-1][0]) == 1 and len(chunks[-2]) == 2
            and f.getlength(" ".join(chunks[-2][1] + chunks[-1][0])) <= max_w):  # 화면 폭을 넘으면 합치지 않음
        chunks[-2][1] = chunks[-2][1] + chunks[-1][0]
        chunks.pop()
    return [[" ".join(l) for l in c] for c in chunks]


def _shadowed_text_layer(lines, size, colors_fn, stroke=6, line_gap=1.13, max_w=W - 40):
    """여러 줄 텍스트 레이어. 화면 폭을 넘으면 글자 크기를 자동으로 줄인다."""
    while size > 30 and max(font(size).getlength(l) for l in lines) + stroke * 2 > max_w:
        size -= 4
    return _text_layer(lines, size, colors_fn, stroke, line_gap)


def _text_layer(lines, size, colors_fn, stroke=6, line_gap=1.13):
    """여러 줄 텍스트를 투명 레이어로 그린다. colors_fn(word)->RGB. 반환: (RGBA 이미지, 줄 높이)."""
    f = font(size)
    lh = int(size * line_gap)
    pad = 30
    width = int(max(f.getlength(l) for l in lines)) + pad * 2 + stroke * 2
    height = lh * len(lines) + pad * 2
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(layer), ImageDraw.Draw(shadow)
    for li, line in enumerate(lines):
        x = (width - f.getlength(line)) / 2
        y = pad + li * lh
        ds.text((x + 4, y + 6), line, font=f, fill=(0, 0, 0, 170), stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
        for wi, word in enumerate(line.split(" ")):
            token = word + (" " if wi < len(line.split(" ")) - 1 else "")
            d.text((x, y), word, font=f, fill=colors_fn(word) + (255,), stroke_width=stroke, stroke_fill=(10, 10, 12, 255))
            x += f.getlength(token)
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    shadow.alpha_composite(layer)
    return shadow


def _paste_center(base, layer, cx, cy):
    pos = (int(cx - layer.width / 2), int(cy - layer.height / 2))
    if base.mode == "RGBA":
        base.alpha_composite(layer, pos)
    else:
        base.paste(layer, pos, layer)


# ---------- 사진 ----------
def _cover(im, bw, bh, focus=(0.5, 0.5)):
    """칸을 꽉 채우도록 자르되, 사용자가 지정한 중심점(focus, 0~1)이 최대한 가운데 오게 한다."""
    r = max(bw / im.width, bh / im.height)
    im = im.resize((max(bw, round(im.width * r)), max(bh, round(im.height * r))), Image.LANCZOS)
    x = int(min(max(focus[0] * im.width - bw / 2, 0), im.width - bw))
    y = int(min(max(focus[1] * im.height - bh / 2, 0), im.height - bh))
    return im.crop((x, y, x + bw, y + bh))


class Panel:
    """한 컷(사진 1장 또는 2장). 프레임마다 천천히 확대/이동."""
    OVERSCAN = 1.10

    def __init__(self, files, idx, detail=False, focus_map=None):
        ims = [ImageOps.exif_transpose(Image.open(f)).convert("RGB") for f in files]
        focus = [tuple((focus_map or {}).get(f) or (0.5, 0.5)) for f in files]
        if detail:  # 같은 사진을 이어 쓸 때는 레퍼런스처럼 다른 부분을 확대한 컷으로 바꾼다
            fx, fy = [(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0), (0.5, 0.5)][idx % 5]
            ims = [im.crop((int(im.width * 0.3 * fx), int(im.height * 0.3 * fy),
                            int(im.width * (0.7 + 0.3 * fx)), int(im.height * (0.7 + 0.3 * fy)))) for im in ims]
        self.zoom_in = idx % 2 == 0
        self.pan = [(1, 0), (-1, 0), (0, 1), (0, -1)][idx % 4]
        if len(ims) == 1:
            im = ims[0]
            bh = int(min(1140, max(760, W * im.height / im.width)))
            self.boxes = [(0, BAND_CY - bh // 2, W, bh)]
        else:
            ims = ims[:2]
            bw, bh, gap = (W - 12) // 2, 760, 12
            self.boxes = [(0, BAND_CY - bh // 2, bw, bh), (bw + gap, BAND_CY - bh // 2, bw, bh)]
        self.src = [_cover(im, int(b[2] * self.OVERSCAN), int(b[3] * self.OVERSCAN), fc if not detail else (0.5, 0.5))
                    for im, b, fc in zip(ims, self.boxes, focus)]

    def draw(self, frame, t):
        """t: 0~1 컷 내부 진행률."""
        e = t * t * (3 - 2 * t)  # 부드러운 가감속
        k = 1 + 0.07 * (e if self.zoom_in else 1 - e)
        for src, (x, y, bw, bh) in zip(self.src, self.boxes):
            cw, ch = src.width / k, src.height / k
            mx, my = (src.width - cw) / 2, (src.height - ch) / 2
            cx = mx + self.pan[0] * mx * (e - 0.5)
            cy = my + self.pan[1] * my * (e - 0.5)
            crop = src.resize((bw, bh), Image.BILINEAR, box=(cx, cy, cx + cw, cy + ch))
            frame.paste(crop, (x, y))


class ClipPanel:
    """무료 스톡 영상 보조 장면: 사진 밴드 자리에 영상을 채워 재생(짧으면 반복)."""
    BH = 1140

    def __init__(self, path):
        self.y = BAND_CY - self.BH // 2
        self.size = W * self.BH * 3
        self.proc = subprocess.Popen(
            [ffmpeg_exe(), "-v", "error", "-stream_loop", "-1", "-i", str(path), "-an", "-vf",
             f"scale={W}:{self.BH}:force_original_aspect_ratio=increase,crop={W}:{self.BH},fps={FPS}",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        self.last = None

    def draw(self, frame, t):
        buf = self.proc.stdout.read(self.size)
        if len(buf) == self.size:
            self.last = Image.frombytes("RGB", (W, self.BH), buf)
        if self.last is not None:
            frame.paste(self.last, (0, self.y))

    def close(self):
        self.proc.kill()
        self.proc.wait()


# ---------- 오디오 ----------
def _concat_wavs(items, out_path, total_frames_sec):
    """[(wav, 시작초)] → 하나의 48k 모노 wav."""
    rate = 48000
    buf = bytearray(int(total_frames_sec * rate) * 2)
    for f, start in items:
        with wave.open(f) as w:
            assert w.getframerate() == rate and w.getsampwidth() == 2 and w.getnchannels() == 1
            data = w.readframes(w.getnframes())
        off = int(start * rate) * 2
        buf[off:off + len(data)] = data[: max(0, len(buf) - off)]
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(buf))


def _srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


# ---------- 메인 ----------
def render(script, work: Path, settings, log=print, progress=lambda p, m="": None, encoder_preset="medium"):
    work.mkdir(parents=True, exist_ok=True)
    th = theme(settings)
    hl, title_col = tuple(th["highlight"]), tuple(th["title"])
    scenes = [s for s in script["scenes"] if s["narration"].strip()]

    # 1) 음성
    voices, engines, tts_usage = [], set(), {}
    for i, s in enumerate(scenes):
        progress(5 + 25 * i / len(scenes), f"음성 생성 {i + 1}/{len(scenes)}")
        v = tts.synth(s["narration"], work / "tts", settings, log)
        voices.append(v)
        engines.add(v["engine"])
        tts_usage[v.get("category", v["engine"])] = tts_usage.get(v.get("category", v["engine"]), 0) + v.get("chars", 0)
    durs = [v["seconds"] + GAP_AFTER + (CTA_HOLD if s["kind"] == "cta" else 0) for v, s in zip(voices, scenes)]
    total = sum(durs)
    tempo = 1.0
    if total > settings["max_seconds"]:
        tempo = min(1.2, total / (settings["max_seconds"] - 0.5))
        durs = [d / tempo for d in durs]
        total = sum(durs)
        log(f"길이 {total * tempo:.1f}초 → 음성 속도 {tempo:.2f}배로 맞춤")
    warnings = []
    if tempo > 1.0:
        warnings.append(f"선택한 배속({settings.get('tts_speed', 1.0)}배)으로는 {settings['max_seconds']}초를 넘어서, "
                        f"영상 길이를 맞추려고 음성을 {tempo:.2f}배 더 빠르게 했습니다. 대본을 줄이면 선택한 배속 그대로 나옵니다.")
    if total < settings["min_seconds"]:
        warnings.append(f"영상 길이 {total:.1f}초로 목표({settings['min_seconds']}초)보다 짧습니다. 본문이 짧거나 배속이 빠릅니다(배속을 낮추거나 대본을 늘려 보세요).")
    if total > settings["max_seconds"] + 0.5:
        warnings.append(f"영상 길이 {total:.1f}초로 {settings['max_seconds']}초를 넘습니다. 대본을 줄여 주세요.")

    starts, t = [], 0.0
    for d in durs:
        starts.append(t)
        t += d
    narration = work / "narration.wav"
    if tempo == 1.0:
        _concat_wavs([(v["file"], st) for v, st in zip(voices, starts)], narration, total + 0.5)
    else:
        _tempo_mix(voices, starts, tempo, narration, total)

    # 2) 자막 조각 + 타이밍
    cues = []
    for i, (s, v, st) in enumerate(zip(scenes, voices, starts)):
        chunks = subtitle_chunks(s["narration"])
        lens = [max(1, speech_len(" ".join(c))) for c in chunks]
        speak = v["seconds"] / tempo
        acc = st
        for c, n in zip(chunks, lens):
            d = speak * n / sum(lens)
            cues.append({"start": acc, "end": acc + d, "lines": c, "hl": s.get("highlight", [])})
            acc += d
        cues[-1]["end"] = st + durs[i]  # 다음 장면 전까지 자막 유지
    (work / "subtitles.srt").write_text("".join(
        f"{i}\n{_srt_time(c['start'])} --> {_srt_time(c['end'])}\n" + "\n".join(c["lines"]) + "\n\n"
        for i, c in enumerate(cues, 1)), encoding="utf-8")

    # 3) 사진: 본문 사진 전부를 순서대로 한 장씩, 한 번씩만(2장 묶음·같은 사진 반복 없음)
    order, seen = [], set()
    for im in [im for s in scenes if s["kind"] != "cta" and not (s.get("clip") or {}).get("file")
               for im in s.get("images", [])] + list(script.get("all_images") or []):
        if im.get("file") and im["file"] not in seen:
            seen.add(im["file"])
            order.append(im)
    focus_map = {im["file"]: im.get("focus") for im in order}
    photo_iv = []  # 사진을 보여 줄 구간(엔딩·보조 영상 장면 제외)
    for i, s in enumerate(scenes):
        if s["kind"] != "cta" and not (s.get("clip") or {}).get("file"):
            photo_iv.append((i, starts[i], starts[i] + durs[i]))
    photo_total = sum(e - b for _, b, e in photo_iv)
    slot = photo_total / len(order) if order else 0
    cum, acc = {}, 0.0
    for i, b, e in photo_iv:
        cum[i] = acc
        acc += e - b
    if not order:
        warnings.append("영상에 넣을 사진이 없습니다.")
    elif slot < 0.8:
        warnings.append(f"사진 {len(order)}장이 영상 길이에 비해 많아 한 장당 {slot:.1f}초씩만 보입니다.")
    shown = {im["file"] for im in order}
    all_imgs = shown

    # 4) 배경과 엔딩(로고) 화면. 글자(상단 문구·자막·엔딩 문구)는 편집 가능한 문구로 따로 만든다.
    bg = Image.new("RGB", (1, 256))
    for y in range(256):
        a = y / 255
        bg.putpixel((0, y), tuple(int(th["bg_top"][i] * (1 - a) + th["bg_bottom"][i] * a) for i in range(3)))
    bg = bg.resize((W, H), Image.BILINEAR).convert("RGBA")
    scene_bases = []
    for i, s in enumerate(scenes):
        base = bg.copy()
        if s["kind"] == "cta":
            ImageDraw.Draw(base).rectangle((0, 560, W, 1360), fill=(0, 0, 0, 255))
            logo_p = Path(settings["logo"])
            if logo_p.exists():
                logo = Image.open(logo_p).convert("RGBA")
                logo.thumbnail((620, 620), Image.LANCZOS)
                _paste_center(base, logo, W / 2, 960)
            else:
                _paste_center(base, _shadowed_text_layer([settings["brand_name"]], 110, lambda w: (255, 255, 255)), W / 2, 960)
        scene_bases.append(base.convert("RGB"))

    def hexc(c):
        return "#%02x%02x%02x" % tuple(c)

    def fit(text, size, max_w=1000):
        while size > 30 and font(size).getlength(text) > max_w:
            size -= 2
        return size

    auto = []
    for i, (s, st, d) in enumerate(zip(scenes, starts, durs)):
        if s["kind"] == "cta":
            txt, size, col, y = script.get("end_header") or settings["brand_name"], 64, "#ffffff", 400
        elif s.get("label"):
            txt, size, col, y = s["label"], 84 if i == 0 else 60, hexc(title_col) if i == 0 else "#ffffff", LABEL_Y
        else:
            continue
        size = fit(txt, size)
        auto.append({"id": f"auto_lab{i}", "auto": "label", "text": txt, "start": round(st, 2), "end": round(st + d, 2),
                     "size": size, "base_size": size, "color": col, "bg": "", "shadow": True, "stroke": True,
                     "x": 0.5, "y": round(y / H, 4), "base_y": round(y / H, 4), "hl": [], "hl_color": ""})
    sub_y = round((SUB_Y[0] + SUB_Y[1]) / 2 / H, 4)
    for j, c in enumerate(cues):
        auto.append({"id": f"auto_sub{j}", "auto": "sub", "text": "\n".join(c["lines"]),
                     "start": round(c["start"], 2), "end": round(c["end"], 2), "size": 92, "base_size": 92,
                     "color": "#ffffff", "bg": "", "shadow": True, "stroke": True, "x": 0.5, "y": sub_y, "base_y": sub_y,
                     "hl": [h for h in c["hl"] if h], "hl_color": hexc(hl)})

    # 5) 프레임 → ffmpeg
    out_mp4 = work / "reel_base.mp4"  # 추가 문구 없는 기본 영상(문구는 editing.compose로 따로 합성)
    frames_dir = work / "frames"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir()
    n_frames = int(round(total * FPS))
    bgm = Path(settings.get("bgm", ""))
    cmd = [ffmpeg_exe(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-i", str(narration)]
    norm = "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[n]"  # 쇼츠 권장 음량, 클리핑 방지
    if bgm.is_file():
        cmd += ["-stream_loop", "-1", "-i", str(bgm), "-filter_complex",
                norm + f";[2:a]volume={settings['bgm_volume']}[b];[n][b]amix=inputs=2:duration=first:normalize=0[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-filter_complex", norm.replace("[n]", "[a]"), "-map", "0:v", "-map", "[a]"]
    cmd += ["-c:v", "libx264", "-preset", encoder_preset, "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{n_frames / FPS:.3f}",
            "-movflags", "+faststart", str(out_mp4)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    panel_cache = {}
    try:
        for fi in range(n_frames):
            t = fi / FPS
            si = max(i for i, st in enumerate(starts) if st <= t + 1e-6)
            frame = scene_bases[si].copy()
            s = scenes[si]
            key, make, prog = None, None, 0.0
            if (s.get("clip") or {}).get("file"):
                key, make = ("clip", si), (lambda: ClipPanel(s["clip"]["file"]))
            elif si in cum and order:
                el = cum[si] + (t - starts[si])
                k = min(len(order) - 1, int(el / slot))
                prog = min(1.0, (el - k * slot) / slot)
                key, make = ("photo", k), (lambda k=k: Panel([order[k]["file"]], k, False, focus_map))
            if key is not None:
                if key not in panel_cache:
                    for old in panel_cache.values():
                        getattr(old, "close", lambda: None)()
                    panel_cache.clear()
                    panel_cache[key] = make()
                panel_cache[key].draw(frame, prog)
            if fi % FPS == 0:  # 썸네일·타임라인용 프레임(글자 없음)을 1초마다 저장
                frame.save(frames_dir / f"clean_{fi // FPS:03d}.jpg", quality=88)
            proc.stdin.write(frame.tobytes())
            if fi % 15 == 0:
                progress(30 + 68 * fi / n_frames, f"영상 합성 {fi}/{n_frames} 프레임")
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg 인코딩 실패")
    except BrokenPipeError:
        proc.wait()
        raise RuntimeError("ffmpeg가 중간에 종료되었습니다")
    finally:
        for old in panel_cache.values():
            getattr(old, "close", lambda: None)()

    progress(100, "완료")
    timeline = [{"start": round(st, 2), "end": round(st + d, 2), "role": s.get("role", ""), "kind": s["kind"],
                 "label": s.get("label", "")} for s, st, d in zip(scenes, starts, durs)]
    return {"video": str(out_mp4), "base_video": str(out_mp4), "timeline": timeline, "auto_overlays": auto,
            "photo_seconds": round(slot, 2),
            "srt": str(work / "subtitles.srt"), "seconds": round(total, 2),
            "tts_engines": sorted(engines), "tts_usage": tts_usage,
            "clips_used": [s["clip"] for s in scenes if (s.get("clip") or {}).get("file")], "images_used": len(shown), "images_total": len(all_imgs),
            "warnings": warnings}


def _tempo_mix(voices, starts, tempo, out_path, total):
    tmp = []
    for v in voices:
        p = Path(v["file"]).with_suffix(f".t{tempo:.2f}.wav")
        if not p.exists():
            subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", v["file"], "-filter:a", f"atempo={tempo:.4f}",
                            "-ac", "1", "-ar", "48000", str(p)], check=True)
        tmp.append(str(p))
    _concat_wavs(list(zip(tmp, starts)), out_path, total + 0.5)
