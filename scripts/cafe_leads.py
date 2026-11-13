"""가온집수리 네이버 카페 손님 글 수집기.

지역 × 시공 항목으로 검색어를 만들어 네이버 카페글 검색 API를 부르고,
이미 본 글(data/seen.json)을 뺀 새 글만 data/candidates_YYYY-MM-DD.json 으로 저장한다.
손님 글인지 판단하고 답글을 쓰는 일은 이 파일이 하지 않는다(사람 또는 Claude가 한다).
여기서는 기계적으로 분명한 것만 표시한다: 광고 단어, '광고 사절', 다른 지역 이름, 질문 신호, 날짜.

키는 .env 에서 읽는다. 키 값은 어디에도 출력하지 않는다.
    NAVER_CLIENT_ID=...
    NAVER_CLIENT_SECRET=...
    (선택) NAVER_CAFE_API_URL=https://openapi.naver.com/v1/search/cafearticle.json

사용:
    python scripts/cafe_leads.py                # 검색 + 새 글 저장 + seen.json 갱신
    python scripts/cafe_leads.py --no-mark      # seen.json 은 건드리지 않음(시험용)
    python scripts/cafe_leads.py --pages 2      # 검색어마다 최대 200건(기본 1페이지=100건)
    python scripts/cafe_leads.py --check        # 키·접속만 확인(검색어 1개)
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SEEN = DATA / "seen.json"
ENV = ROOT / ".env"
DEFAULT_URL = "https://openapi.naver.com/v1/search/cafearticle.json"

# ---- 검색 조건 (바꾸고 싶으면 여기만 고치면 된다) ----
REGIONS = ["서울", "경기"]
ITEMS = ["집수리교육", "집수리창업", "데크", "욕실 실리콘", "곰팡이", "문 후넨스", "집수리 추천", "견적"]
# '견적'만 단독으로 붙이면 너무 넓어서 집수리 단어를 같이 붙인다.
ITEM_QUERY = {"견적": "집수리 견적"}

# 서울·경기 안의 시·구 이름(지역 맞는지 판단할 때 씀)
SEOUL_GYEONGGI = (
    "서울 경기 강남 강동 강북 강서 관악 광진 구로 금천 노원 도봉 동대문 동작 마포 서대문 서초 성동 성북 송파 "
    "양천 영등포 용산 은평 종로 중랑 수원 성남 분당 판교 용인 수지 기흥 고양 일산 화성 동탄 부천 남양주 안산 "
    "안양 평촌 평택 시흥 파주 운정 김포 의정부 광주 하남 광명 군포 오산 이천 양주 안성 구리 포천 의왕 여주 "
    "동두천 과천 가평 양평 연천 위례 미사 다산 별내 광교 송도"
).split()
# 서울·경기가 아닌 지역(이것만 나오고 서울·경기 단어가 없으면 '지역 안 맞음' 후보)
OTHER_REGIONS = (
    "부산 대구 대전 광주광역시 울산 세종 제주 강원 춘천 원주 강릉 충북 청주 충주 충남 천안 아산 전북 전주 "
    "군산 익산 전남 목포 여수 순천 경북 포항 구미 경주 경남 창원 김해 양산 진주 거제 인천"
).split()

AD_WORDS = [
    "시공문의", "상담문의", "문의주세요", "문의 주세요", "연락주세요", "연락 주세요", "카톡", "오픈채팅", "010-", "010 ",
    "전화상담", "무료견적", "무료 견적", "무료상담", "할인", "이벤트", "업체입니다", "전문업체", "시공사례", "시공 사례",
    "시공후기", "작업후기", "체험단", "협찬", "원고료", "제공받아", "홍보", "광고", "수강생 모집", "모집합니다",
]
NO_AD_WORDS = ["광고사절", "광고 사절", "업체사절", "업체 사절", "업자사절", "업자 사절", "홍보사절", "홍보 사절", "업체 연락 사절"]
QUESTION_WORDS = [
    "추천", "견적", "얼마", "비용", "가격", "어디", "맡겨", "업체", "고칠", "수리", "어떻게", "방법", "가능할까",
    "가능한가", "되나요", "할까요", "있을까요", "있나요", "궁금", "질문", "도와", "알려", "?",
]
FIX_WORDS = ["고장", "하자", "새요", "누수", "곰팡이", "깨졌", "부서", "안닫", "안 닫", "삐걱", "처짐", "들뜸", "갈라", "썩"]
QUOTE_WORDS = ["추천", "견적", "얼마", "비용", "가격", "어디", "맡겨", "업체"]


def load_env() -> dict:
    env = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "NAVER_CAFE_API_URL"):
        if os.environ.get(k):
            env.setdefault(k, os.environ[k])
    return env


def build_queries() -> list[dict]:
    out = []
    for r in REGIONS:
        for it in ITEMS:
            out.append({"query": f"{r} {ITEM_QUERY.get(it, it)}", "region": r, "item": it})
    return out


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def call_api(url: str, cid: str, secret: str, query: str, start: int) -> dict:
    qs = urllib.parse.urlencode({"query": query, "display": 100, "start": start, "sort": "date"})
    req = urllib.request.Request(f"{url}?{qs}", headers={
        "X-Naver-Client-Id": cid,
        "X-Naver-Client-Secret": secret,
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def article_key(link: str) -> str:
    """같은 글이 다른 주소 모양으로 와도 하나로 본다: 카페주소/글번호."""
    m = re.search(r"cafe\.naver\.com/([^/?#]+)/(\d+)", link)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = re.search(r"articleid=(\d+)", link, re.I)
    c = re.search(r"cafe\.naver\.com/([^/?#]+)", link)
    if m and c:
        return f"{c.group(1)}/{m.group(1)}"
    return link


DATE_RE = re.compile(r"(20\d{2})[.\-/]\s?(\d{1,2})[.\-/]\s?(\d{1,2})")


def guess_date(text: str) -> str | None:
    """본문 요약에 날짜가 적혀 있으면 쓴다. 카페 검색 API는 작성일을 주지 않는다."""
    m = DATE_RE.search(text)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def flags_for(title: str, desc: str) -> dict:
    t = f"{title} {desc}"
    has_local = any(w in t for w in SEOUL_GYEONGGI)
    other = [w for w in OTHER_REGIONS if w in t]
    return {
        "ad_words": [w for w in AD_WORDS if w in t],
        "no_ad": any(w in t for w in NO_AD_WORDS),
        "other_region": other if (other and not has_local) else [],
        "question": any(w in t for w in QUESTION_WORDS),
        "quote_or_recommend": any(w in t for w in QUOTE_WORDS),
        "fix_or_defect": any(w in t for w in FIX_WORDS),
    }


def load_seen() -> dict:
    if SEEN.exists():
        try:
            return json.loads(SEEN.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("경고: data/seen.json 을 읽지 못해 빈 목록으로 시작합니다.", file=sys.stderr)
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=1, help="검색어당 페이지 수(1페이지=100건, 최대 10)")
    ap.add_argument("--no-mark", action="store_true", help="seen.json 을 갱신하지 않음")
    ap.add_argument("--check", action="store_true", help="키·접속만 확인")
    args = ap.parse_args()

    env = load_env()
    cid, secret = env.get("NAVER_CLIENT_ID"), env.get("NAVER_CLIENT_SECRET")
    url = env.get("NAVER_CAFE_API_URL") or DEFAULT_URL
    if not cid or not secret:
        print("오류: .env 에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 이 없습니다. (값은 출력하지 않음)", file=sys.stderr)
        return 2

    queries = build_queries()
    if args.check:
        queries = queries[:1]
        args.pages = 1

    today = dt.date.today().isoformat()
    seen = load_seen()
    found: dict[str, dict] = {}
    total_hits = 0
    errors = []

    for q in queries:
        for page in range(max(1, min(args.pages, 10))):
            try:
                res = call_api(url, cid, secret, q["query"], 1 + page * 100)
            except urllib.error.HTTPError as e:
                errors.append(f"{q['query']}: HTTP {e.code}")
                break
            except Exception as e:  # 네트워크 차단 등
                errors.append(f"{q['query']}: {type(e).__name__}: {e}")
                break
            items = res.get("items", [])
            total_hits += len(items)
            for it in items:
                link = it.get("link", "")
                key = article_key(link)
                title, desc = clean(it.get("title")), clean(it.get("description"))
                if key in found:
                    found[key]["queries"].append(q["query"])
                    continue
                found[key] = {
                    "key": key,
                    "title": title,
                    "description": desc,
                    "cafe_name": clean(it.get("cafename")),
                    "cafe_url": it.get("cafeurl", ""),
                    "link": link,
                    "queries": [q["query"]],
                    "region": q["region"],
                    "item": q["item"],
                    "date": guess_date(desc),
                    "flags": flags_for(title, desc),
                }
            if len(items) < 100:
                break
            time.sleep(0.15)
        time.sleep(0.1)

    if args.check:
        ok = not errors
        print("접속 확인:", "성공" if ok else "실패", f"(결과 {total_hits}건)")
        for e in errors:
            print(" -", e)
        return 0 if ok else 1

    new = [v for k, v in found.items() if k not in seen]
    DATA.mkdir(exist_ok=True)
    out = DATA / f"candidates_{today}.json"
    out.write_text(json.dumps({
        "date": today,
        "queries": [q["query"] for q in queries],
        "total_hits": total_hits,
        "unique": len(found),
        "already_seen": len(found) - len(new),
        "errors": errors,
        "items": new,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.no_mark:
        for v in new:
            seen[v["key"]] = {"first_seen": today, "title": v["title"]}
        SEEN.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"검색어 {len(queries)}개 · 검색 결과 {total_hits}건 · 중복 제거 {len(found)}건 · "
          f"이미 본 글 {len(found) - len(new)}건 · 새 글 {len(new)}건")
    print(f"저장: {out.relative_to(ROOT)}")
    if errors:
        print(f"실패한 검색 {len(errors)}개:")
        for e in errors[:10]:
            print(" -", e)
    return 0 if not errors or new else 1


if __name__ == "__main__":
    sys.exit(main())
