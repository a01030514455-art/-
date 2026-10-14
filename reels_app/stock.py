"""무료 스톡 영상(보조 장면용). 블로그 원본 사진이 기본이고, 스톡 영상은 사용자가 고른 것만 보조로 쓴다.

출처별 처리(2026-10 확인한 조건 기준. 실제 약관은 각 사이트에서 다시 확인할 것)
- Pexels  : 공식 API(무료 키). 기본 한도 시간당 200회·월 2만 회. 결과 화면에 Pexels 링크와 작가 표기.
            Pexels 자체를 대신하는 스톡 사이트처럼 쓰는 것은 금지.
- Pixabay : 공식 API(무료 키). 기본 한도 60초당 100회. 검색 결과는 24시간 캐시, 결과 화면에 출처 표시,
            대량·자동 다운로드 금지.
- Mixkit  : 공식 API가 확인되지 않아 자동 검색·다운로드를 하지 않는다. 사이트를 열어 주고,
            사용자가 직접 받은 파일을 출처 URL과 함께 등록만 한다.

공통: 사용자가 '이 영상 사용'을 누른 영상 1개만 내려받는다(일괄 다운로드 없음). 원본 파일을 따로 배포하는
기능은 없고, 영상 안에 편집된 형태로만 들어간다. 모든 다운로드는 stock_log.jsonl에 출처·라이선스와 함께 기록한다.
"""
import hashlib
import json
import os
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import OUTPUT_DIR

CACHE_DIR = OUTPUT_DIR / "stock_cache"
LOG_FILE = OUTPUT_DIR / "stock_log.jsonl"
CACHE_SEC = 24 * 3600
UA = {"User-Agent": "blog-reels-maker/1.0"}
PEXELS_API = "https://api.pexels.com/v1/videos/search"
PIXABAY_API = "https://pixabay.com/api/videos/"
DOWNLOAD_HOSTS = ("pexels.com", "pixabay.com")  # API가 알려 준 공식 파일 주소에서만 받는다

LICENSES = {
    "pexels": {"name": "Pexels License", "url": "https://www.pexels.com/license/",
               "note": "무료·상업 이용 가능, 출처 표기 권장(API 이용 시 Pexels·작가 표기 필요). 변형 없이 재판매·재배포 금지."},
    "pixabay": {"name": "Pixabay Content License", "url": "https://pixabay.com/service/license-summary/",
                "note": "무료·상업 이용 가능, 출처 표기 불필요. 변형 없이 단독 재판매·재배포 금지."},
    "mixkit": {"name": "Mixkit Stock Video Free License", "url": "https://mixkit.co/license/#videoFree",
               "note": "무료·상업 이용 가능(Free License 항목만), 출처 표기 불필요. 원본 그대로 재판매·재배포 금지. "
                       "'Restricted License' 영상은 상업 이용 불가이니 받기 전에 확인하세요."},
}

# 한국어 키워드 → 영어 검색어(영어로 찾을 때 결과가 훨씬 많음)
KEYWORDS = {
    "집수리": "home repair", "인테리어": "interior renovation", "욕실": "bathroom renovation", "화장실": "bathroom",
    "누수": "water leak", "배관": "plumbing pipe", "타일": "tile installation", "줄눈": "tile grout",
    "방수": "waterproofing roof", "옥상": "rooftop", "곰팡이": "mold wall", "결로": "condensation window",
    "도배": "wallpaper installation", "페인트": "painting wall", "마루": "wooden floor installation",
    "바닥": "floor installation", "창호": "window installation", "샷시": "window frame", "공사": "construction work",
    "공구": "tools work", "드릴": "drill", "볼라드": "bollard street", "골목": "alley street", "안전": "safety",
    "주방": "kitchen renovation", "전기": "electrician", "조명": "lighting installation", "실리콘": "caulking",
}


def keys(settings: dict) -> dict:
    return {"pexels": os.environ.get("PEXELS_API_KEY") or settings.get("pexels_key", ""),
            "pixabay": os.environ.get("PIXABAY_API_KEY") or settings.get("pixabay_key", "")}


def translate(q: str) -> str:
    q = q.strip()
    parts = [KEYWORDS.get(w, w) for w in q.split()]
    return " ".join(parts) if any(w in KEYWORDS for w in q.split()) else KEYWORDS.get(q, q)


def _get_json(url, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        info = {k.lower(): v for k, v in r.headers.items()}
        return json.loads(r.read().decode()), info


def _cached(provider, url, headers=None):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / (provider + "_" + hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")
    if f.exists() and time.time() - f.stat().st_mtime < CACHE_SEC:
        return json.loads(f.read_text(encoding="utf-8")), {"cache": "hit"}
    data, info = _get_json(url, headers)
    f.write_text(json.dumps(data), encoding="utf-8")
    return data, info


def _pick_file(files, want_h=1920):
    """세로 1080×1920에 쓰기 좋은 파일: FHD 이하 중 가장 큰 것(4K는 용량만 커서 제외)."""
    ok = [f for f in files if f.get("link") and (f.get("height") or 0) and max(f["width"], f["height"]) <= 2000]
    ok = ok or [f for f in files if f.get("link")]
    return max(ok, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0)) if ok else None


def search(provider: str, query: str, settings: dict, orientation="portrait", page=1) -> dict:
    k = keys(settings)
    q_en = translate(query)
    if provider == "pexels":
        if not k["pexels"]:
            raise RuntimeError("Pexels API 키가 없습니다. pexels.com/api 에서 무료로 발급받아 설정에 넣으세요.")
        url = PEXELS_API + "?" + urllib.parse.urlencode(
            {"query": q_en, "orientation": orientation, "per_page": 15, "page": page, "size": "medium"})
        data, info = _cached("pexels", url, {"Authorization": k["pexels"]})
        items = []
        for v in data.get("videos", []):
            f = _pick_file(v.get("video_files", []))
            if not f:
                continue
            items.append({"provider": "pexels", "id": str(v["id"]), "thumb": v.get("image"), "duration": v.get("duration"),
                          "width": f.get("width"), "height": f.get("height"), "download": f["link"],
                          "page_url": v.get("url"), "author": (v.get("user") or {}).get("name", ""),
                          "author_url": (v.get("user") or {}).get("url", "")})
        quota = info.get("x-ratelimit-remaining")
    elif provider == "pixabay":
        if not k["pixabay"]:
            raise RuntimeError("Pixabay API 키가 없습니다. pixabay.com/api/docs 에서 무료로 발급받아 설정에 넣으세요.")
        url = PIXABAY_API + "?" + urllib.parse.urlencode(
            {"key": k["pixabay"], "q": q_en, "per_page": 15, "page": page, "safesearch": "true"})
        data, info = _cached("pixabay", url)
        items = []
        for v in data.get("hits", []):
            vids = v.get("videos", {})
            f = _pick_file([{"link": x.get("url"), "width": x.get("width"), "height": x.get("height")}
                            for x in vids.values() if x.get("url")])
            if not f:
                continue
            thumb = (vids.get("tiny") or vids.get("small") or {}).get("thumbnail") or v.get("picture_id")
            items.append({"provider": "pixabay", "id": str(v["id"]), "thumb": thumb, "duration": v.get("duration"),
                          "width": f.get("width"), "height": f.get("height"), "download": f["link"],
                          "page_url": v.get("pageURL"), "author": v.get("user", ""),
                          "author_url": f"https://pixabay.com/users/{v.get('user', '')}-{v.get('user_id', '')}/",
                          "tags": v.get("tags", "")})
        quota = info.get("x-ratelimit-remaining")
    elif provider == "mixkit":
        return {"provider": "mixkit", "items": [], "query_en": q_en, "manual": True,
                "open_url": "https://mixkit.co/free-stock-video/" + urllib.parse.quote(q_en.replace(" ", "-")) + "/",
                "license": LICENSES["mixkit"]}
    else:
        raise RuntimeError("알 수 없는 출처입니다.")
    return {"provider": provider, "items": items, "query_en": q_en, "quota_remaining": quota,
            "cache": info.get("cache", "miss"), "license": LICENSES[provider]}


def _log(entry):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def download(item: dict, job_dir: Path, query: str) -> dict:
    """사용자가 고른 영상 1개만 받는다. 출처·라이선스를 함께 기록한다."""
    prov = item.get("provider")
    if prov not in ("pexels", "pixabay"):
        raise RuntimeError("API로 받을 수 있는 출처가 아닙니다.")
    url = item["download"]
    host = urllib.parse.urlparse(url).netloc
    if not any(host == h or host.endswith("." + h) or host.split(":")[0] == h for h in DOWNLOAD_HOSTS):
        raise RuntimeError("허용되지 않은 다운로드 주소입니다.")
    d = job_dir / "stock"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{prov}_{item['id']}.mp4"
    if not path.exists():
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
            shutil.copyfileobj(r, f)
    clip = _clip_record(item, path, query, "api")
    _log(clip)
    return clip


def import_manual(data: bytes, filename: str, job_dir: Path, source_url: str, author: str, query: str) -> dict:
    """Mixkit 등에서 사용자가 직접 받은 파일을 출처와 함께 등록."""
    if not source_url.startswith("https://mixkit.co/"):
        raise RuntimeError("Mixkit 영상 페이지 주소(https://mixkit.co/...)를 함께 입력해 주세요.")
    if len(data) > 300 * 1024 * 1024:
        raise RuntimeError("파일이 너무 큽니다(300MB 이하).")
    d = job_dir / "stock"
    d.mkdir(parents=True, exist_ok=True)
    cid = hashlib.sha1(source_url.encode()).hexdigest()[:10]
    path = d / f"mixkit_{cid}.mp4"
    path.write_bytes(data)
    item = {"provider": "mixkit", "id": cid, "page_url": source_url, "author": author, "author_url": "",
            "download": "", "thumb": "", "filename": filename}
    clip = _clip_record(item, path, query, "manual")
    _log(clip)
    return clip


def _clip_record(item, path, query, how):
    lic = LICENSES[item["provider"]]
    return {"clip_id": f"{item['provider']}_{item['id']}", "provider": item["provider"], "source_id": item["id"],
            "file": str(path), "page_url": item.get("page_url"), "author": item.get("author", ""),
            "author_url": item.get("author_url", ""), "license": lic["name"], "license_url": lic["url"],
            "download_url": item.get("download", ""), "query": query, "how": how,
            "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"), "cost": "무료"}


def credits_text(clips) -> str:
    """영상 설명란에 붙일 출처 문구."""
    lines = []
    for c in clips:
        who = f" by {c['author']}" if c.get("author") else ""
        name = {"pexels": "Pexels", "pixabay": "Pixabay", "mixkit": "Mixkit"}[c["provider"]]
        lines.append(f"- Video{who} on {name}: {c.get('page_url') or ''} ({c['license']})")
    return "보조 영상 출처\n" + "\n".join(lines) if lines else ""
