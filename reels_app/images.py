"""사진 편집(회전·반전·밝기·대비·채도·중심점)과 내 사진 추가. 원본은 그대로 두고 edited/ 폴더에 편집본을 만든다."""
import io
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

DEFAULT_EDITS = {"rotate": 0, "flip": False, "brightness": 1.0, "contrast": 1.0, "saturation": 1.0,
                 "focus": [0.5, 0.5]}
MAX_UPLOAD = 40 * 1024 * 1024


def clean_edits(e: dict) -> dict:
    out = dict(DEFAULT_EDITS)
    out["rotate"] = int(e.get("rotate", 0)) % 360 // 90 * 90
    out["flip"] = bool(e.get("flip", False))
    for k in ("brightness", "contrast", "saturation"):
        out[k] = round(min(2.0, max(0.3, float(e.get(k, 1.0)))), 2)
    fx, fy = (e.get("focus") or [0.5, 0.5])[:2]
    out["focus"] = [round(min(1.0, max(0.0, float(fx))), 3), round(min(1.0, max(0.0, float(fy))), 3)]
    return out


def is_default(e: dict) -> bool:
    return all(e[k] == DEFAULT_EDITS[k] for k in ("rotate", "flip", "brightness", "contrast", "saturation"))


def apply_edits(src: str, dst: Path, e: dict):
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
    if e["rotate"]:
        im = im.rotate(-e["rotate"], expand=True)  # 시계 방향
    if e["flip"]:
        im = ImageOps.mirror(im)
    if e["brightness"] != 1.0:
        im = ImageEnhance.Brightness(im).enhance(e["brightness"])
    if e["contrast"] != 1.0:
        im = ImageEnhance.Contrast(im).enhance(e["contrast"])
    if e["saturation"] != 1.0:
        im = ImageEnhance.Color(im).enhance(e["saturation"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, quality=95)


def save_upload(data: bytes, img_dir: Path, index: int) -> dict:
    if len(data) > MAX_UPLOAD:
        raise RuntimeError("사진 파일이 너무 큽니다(40MB 이하).")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
    except Exception:
        raise RuntimeError("사진 파일을 읽을 수 없습니다(JPG, PNG, WEBP 등 이미지 파일만 가능).")
    path = Path(img_dir) / f"upload_{index:03d}.jpg"
    im.save(path, quality=95)
    return {"type": "image", "index": index, "file": str(path), "caption": "", "uploaded": True,
            "w": im.width, "h": im.height}
