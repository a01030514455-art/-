"""한국어 음성 목록·Azure Speech 호출·월간 사용량 기록.

엔진
- edge  : Edge 읽어주기 음성(무료, 키 불필요, 비공식 경로라 서비스 보장 없음)
- azure : Azure AI Speech 공식 REST API. F0(무료) 티어는 월 50만 자 무료이고 한도를 넘으면 요청이 거절될 뿐 과금되지 않음.
          S0(유료) 티어는 첫 글자부터 과금되므로 앱에서 승인 후에만 사용.
- local : PC 내장 음성(오프라인, 무료)

Azure 과금 글자 수는 보수적으로 계산한다: 한글·한자·가나는 2자, 그 외 1자, SSML 조절 태그 포함.
"""
import json
import os
import re
import time
import urllib.request
from pathlib import Path

from .config import OUTPUT_DIR, ROOT

USAGE_FILE = OUTPUT_DIR / "tts_usage.json"
AZURE_FREE_CHARS = 500_000
AZURE_WARN_RATIO = 0.8
_voice_cache: dict = {}

# 키를 못 쓸 때(오프라인 등) 보여 줄 기본 목록. 실제 목록은 API로 조회한다.
FALLBACK_KO_VOICES = [
    {"name": "ko-KR-SunHiNeural", "label": "선히", "gender": "Female"},
    {"name": "ko-KR-InJoonNeural", "label": "인준", "gender": "Male"},
]


# ---------------- 설정(키는 settings.local.json 또는 환경변수, 저장소에는 올라가지 않음) ----------------
def azure_conf(settings: dict) -> dict:
    region = os.environ.get("AZURE_SPEECH_REGION") or settings.get("azure_region", "")
    return {"key": os.environ.get("AZURE_SPEECH_KEY") or settings.get("azure_key", ""), "region": region,
            "tier": settings.get("azure_tier", "F0"),
            # 기본은 지역 엔드포인트. 사용자 지정 도메인(또는 테스트용 주소)이 있으면 그것을 쓴다.
            "base": (settings.get("azure_endpoint") or f"https://{region}.tts.speech.microsoft.com").rstrip("/")}


def save_local_settings(updates: dict):
    p = ROOT / "settings.local.json"
    cur = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    cur.update(updates)
    p.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------- 음성 목록 ----------------
def _gender_ko(g):
    return "여성" if str(g).lower().startswith("f") else "남성"


def list_voices(engine: str, settings: dict) -> dict:
    """한국어(ko-KR) 음성 목록. 반환: {voices:[{name,label,gender,gender_ko}], source, error}"""
    key = engine
    if key in _voice_cache and time.time() - _voice_cache[key][0] < 3600:
        return _voice_cache[key][1]
    out, err, source = [], "", "api"
    try:
        if engine == "edge":
            import asyncio

            import edge_tts
            vs = asyncio.run(edge_tts.list_voices())
            out = [{"name": v["ShortName"], "label": v.get("FriendlyName", v["ShortName"]).split(" - ")[0]
                    .replace("Microsoft ", "").replace(" Online (Natural)", ""), "gender": v.get("Gender", "")}
                   for v in vs if v.get("Locale") == "ko-KR"]
        elif engine == "azure":
            c = azure_conf(settings)
            if not (c["key"] and c["region"]):
                raise RuntimeError("Azure 키와 지역을 먼저 입력하세요.")
            req = urllib.request.Request(
                f"{c['base']}/cognitiveservices/voices/list",
                headers={"Ocp-Apim-Subscription-Key": c["key"]})
            with urllib.request.urlopen(req, timeout=20) as r:
                vs = json.loads(r.read().decode())
            out = [{"name": v["ShortName"], "label": v.get("LocalName") or v.get("DisplayName"),
                    "gender": v.get("Gender", ""), "type": v.get("VoiceType", "")}
                   for v in vs if v.get("Locale") == "ko-KR"]
        else:
            out = [{"name": "local", "label": "PC 내장 음성", "gender": "Female"}]
            source = "local"
    except Exception as e:
        err = f"음성 목록을 불러오지 못했습니다: {str(e).splitlines()[0] if str(e) else type(e).__name__}"
        out, source = list(FALLBACK_KO_VOICES), "fallback"
    for v in out:
        v["gender_ko"] = _gender_ko(v["gender"])
    out.sort(key=lambda v: (v["gender_ko"], v["label"]))
    res = {"engine": engine, "voices": out, "source": source, "error": err}
    if not err:
        _voice_cache[key] = (time.time(), res)
    return res


# ---------------- Azure 합성 ----------------
def _xml(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def azure_ssml(text: str, voice: str, speed: float, volume: float) -> str:
    rate = f"{round((speed - 1) * 100):+d}%"
    vol = f"{round((volume - 1) * 100):+d}%"
    return (f"<speak version='1.0' xml:lang='ko-KR'><voice name='{voice}'>"
            f"<prosody rate='{rate}' volume='{vol}'>{_xml(text)}</prosody></voice></speak>")


def billable_chars(ssml: str) -> int:
    """outer <speak>/<voice> 태그를 뺀 나머지를 센다. 한글·한자·가나는 2자로(보수적 추정)."""
    inner = re.sub(r"^<speak[^>]*><voice[^>]*>|</voice></speak>$", "", ssml)
    return sum(2 if re.match(r"[ᄀ-ᇿ㄰-㆏가-힣一-鿿぀-ヿ]", ch) else 1
               for ch in inner)


def azure_synth(text: str, voice: str, speed: float, volume: float, settings: dict, out_wav: Path) -> int:
    c = azure_conf(settings)
    if not (c["key"] and c["region"]):
        raise RuntimeError("Azure 키와 지역이 설정되지 않았습니다.")
    ssml = azure_ssml(text, voice, speed, volume)
    req = urllib.request.Request(
        f"{c['base']}/cognitiveservices/v1", data=ssml.encode("utf-8"),
        headers={"Ocp-Apim-Subscription-Key": c["key"], "Content-Type": "application/ssml+xml",
                 "X-Microsoft-OutputFormat": "riff-48khz-16bit-mono-pcm", "User-Agent": "blog-reels-maker"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out_wav.write_bytes(r.read())
    except urllib.error.HTTPError as e:
        msg = {401: "키가 올바르지 않습니다", 403: "권한이 없거나 무료 한도를 넘었습니다",
               429: "요청이 너무 많습니다(F0는 1분에 20회 제한). 잠시 후 다시 시도하세요"}.get(e.code, e.reason)
        raise RuntimeError(f"Azure 음성 오류 HTTP {e.code}: {msg}")
    return billable_chars(ssml)


# ---------------- 월간 사용량 ----------------
def _month():
    return time.strftime("%Y-%m")


def load_usage() -> dict:
    try:
        u = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        u = {}
    return u


def month_usage(month=None) -> dict:
    m = load_usage().get(month or _month(), {})
    return {"month": month or _month(), "azure_free": m.get("azure_free", 0), "azure_billable": m.get("azure_billable", 0),
            "edge": m.get("edge", 0), "local": m.get("local", 0)}


def record(engine: str, chars: int, settings: dict) -> str:
    """사용량을 기록하고 분류('azure_free' | 'azure_billable' | 'edge' | 'local')를 돌려준다."""
    u = load_usage()
    m = u.setdefault(_month(), {})
    if engine == "azure":
        cat = "azure_free" if azure_conf(settings)["tier"] == "F0" else "azure_billable"
    else:
        cat = engine
    m[cat] = m.get(cat, 0) + chars
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(json.dumps(u, ensure_ascii=False, indent=1), encoding="utf-8")
    return cat


def azure_precheck(needed_chars: int, settings: dict) -> dict:
    """이번 작업 전에 무료 한도·과금 여부를 판단한다."""
    c = azure_conf(settings)
    mu = month_usage()
    used = mu["azure_free"]
    price = float(settings.get("azure_price_per_mchar", 16.0))
    res = {"tier": c["tier"], "used": used, "needed": needed_chars, "limit": AZURE_FREE_CHARS,
           "after": used + needed_chars, "level": "ok", "message": "", "billable_usd": 0.0}
    if c["tier"] == "F0":
        if used + needed_chars > AZURE_FREE_CHARS:
            res.update(level="block", message=(
                f"이번 달 Azure 무료 한도(50만 자)를 넘습니다(사용 {used:,}자 + 이번 {needed_chars:,}자). "
                "F0는 한도를 넘으면 요청이 거절됩니다. Edge 무료 음성으로 바꾸거나 다음 달에 진행하세요."))
        elif used + needed_chars > AZURE_FREE_CHARS * AZURE_WARN_RATIO:
            res.update(level="warn", message=(
                f"이번 달 Azure 무료 한도의 {round((used + needed_chars) / AZURE_FREE_CHARS * 100)}%를 쓰게 됩니다 "
                f"({used + needed_chars:,} / {AZURE_FREE_CHARS:,}자)."))
    else:
        usd = needed_chars / 1e6 * price
        res.update(level="billable", billable_usd=round(usd, 4), message=(
            f"Azure S0(유료) 티어는 사용한 만큼 과금됩니다. 이번 작업 약 {needed_chars:,}자, 예상 ${usd:.3f} "
            f"(1백만 자당 ${price} 기준, 실제 요금은 Azure 가격표 확인)."))
    return res
