"""설정과 경로. 기본값은 여기, 사용자 변경값은 프로젝트 루트의 settings.json에 둔다."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
ASSETS = APP_DIR / "assets"
OUTPUT_DIR = ROOT / "output"
IMAGES_DIR = ROOT / "extracted_images"

# 레퍼런스 영상 2편(가온집수리 마루 보수 / 매립식 안전기둥)에서 측정한 값. docs/STYLE_GUIDE.md 참고.
DEFAULTS = {
    "brand_name": "가온집수리",
    "end_header": "",                       # 비우면 "{주제} 전문 상담"
    "cta_text": "전문가에게 상담받아 보세요.",
    "cta_highlight": "상담받아",
    "logo": str(ASSETS / "logo.png"),
    "bgm": str(ASSETS / "bgm.mp3"),          # 파일이 있을 때만 작게 깔린다
    "bgm_volume": 0.10,

    "theme": "dark_blue",                   # dark_blue(마루 보수 레퍼런스) | black_green(안전기둥 레퍼런스)
    "themes": {
        "dark_blue": {"bg_top": [30, 31, 33], "bg_bottom": [15, 16, 18], "highlight": [80, 122, 255],
                      "title": [246, 193, 10]},
        "black_green": {"bg_top": [4, 4, 4], "bg_bottom": [3, 3, 3], "highlight": [126, 241, 4],
                        "title": [246, 193, 10]},
    },

    "target_seconds": 48,                   # 40~60초 범위 안의 목표
    "min_seconds": 40,
    "max_seconds": 60,

    "tts_engine": "auto",                   # auto(edge→local) | edge | local
    "tts_voice": "ko-KR-SunHiNeural",       # ko-KR-InJoonNeural(남성)도 가능
    "tts_speed": 1.1,                       # 음성 배속(1.0=기본). 앱 화면에서 0.7~1.6배로 조절

    # AI 대본은 기본 꺼짐. 사용 시 앱에서 예상 비용을 보여주고 승인을 받는다.
    "ai_model": "claude-haiku-5-5",
    "ai_price_in_per_mtok": 0.10,           # USD, 2026-10 기준
    "ai_price_out_per_mtok": 0.50,
    "usd_to_krw": 1400,

    "browser_channel": "",                  # 비우면 자동: playwright chromium → msedge → chrome
    "browser_executable": "",               # 이미 설치된 크롬/엣지 실행파일 경로(선택)
    "host": "127.0.0.1",
    "port": 8765,
}


def load_settings() -> dict:
    s = json.loads(json.dumps(DEFAULTS))
    for name in ("settings.json", "settings.local.json"):
        p = ROOT / name
        if p.exists():
            s.update(json.loads(p.read_text(encoding="utf-8")))
    return s


def theme(settings: dict) -> dict:
    return settings["themes"].get(settings["theme"], DEFAULTS["themes"]["dark_blue"])


_FONT_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\malgunbd.ttf",
    # macOS
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/Library/Fonts/NanumGothicExtraBold.ttf",
    # Linux
    "/usr/share/fonts/truetype/nanum/NanumGothicExtraBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumSquareEB.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
]


def bold_font_path() -> str:
    env = os.environ.get("REELS_FONT")
    if env and Path(env).exists():
        return env
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p
    raise RuntimeError("한글 굵은 글꼴을 찾지 못했습니다. REELS_FONT 환경변수로 .ttf 경로를 지정하세요.")


IS_WINDOWS = sys.platform == "win32"

_ffmpeg = None


def ffmpeg_exe() -> str:
    """PC에 설치된 FFmpeg가 있으면 쓰고, 없으면 pip 패키지(imageio-ffmpeg)에 들어 있는 FFmpeg를 쓴다(별도 설치 불필요)."""
    global _ffmpeg
    if _ffmpeg is None:
        import shutil
        _ffmpeg = shutil.which("ffmpeg")
        if not _ffmpeg:
            try:
                import imageio_ffmpeg
                _ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception as e:
                raise RuntimeError("FFmpeg를 찾지 못했습니다. `pip install imageio-ffmpeg`를 실행하세요.") from e
    return _ffmpeg
