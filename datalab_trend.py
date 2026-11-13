"""네이버 데이터랩 검색어 트렌드: 최근 1개월 주간 검색 추이.

사용법:
    1) .env 파일에 키 입력 (.env.example 참고)
         NAVER_CLIENT_ID=...
         NAVER_CLIENT_SECRET=...
    2) python datalab_trend.py
       결과는 화면에 표로 나오고 output/datalab_trend_YYYYMMDD.csv 로 저장됩니다.

    키워드를 바꾸려면: python datalab_trend.py 집수리 욕실수리 누수탐지

참고: 값(ratio)은 검색량 자체가 아니라 상대값입니다.
      이번 조회 기간·키워드 전체에서 가장 많이 검색된 지점을 100으로 놓은 비율입니다.
"""

import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

API_URL = "https://openapi.naver.com/v1/datalab/search"
DEFAULT_KEYWORDS = ["집수리", "집수리창업", "집수리교육"]
HERE = Path(__file__).resolve().parent


def load_env(path: Path) -> None:
    """.env 파일의 KEY=VALUE 줄을 환경변수로 읽는다 (이미 있는 값은 덮어쓰지 않음)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def fetch_trend(client_id: str, client_secret: str, keywords: list[str],
                start: date, end: date) -> dict:
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "timeUnit": "week",
        # 키워드마다 그룹 하나 (한 번에 최대 5개 그룹)
        "keywordGroups": [{"groupName": k, "keywords": [k]} for k in keywords],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        sys.exit(f"API 오류 {e.code}: {detail}\n"
                 "→ 401이면 키 확인, 403이면 개발자센터 앱에 '데이터랩(검색어트렌드)' API가 추가돼 있는지 확인하세요.")
    except urllib.error.URLError as e:
        sys.exit(f"네트워크 오류: {e.reason}")


def main() -> None:
    load_env(HERE / ".env")
    client_id = os.environ.get("NAVER_CLIENT_ID")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit(".env 에 NAVER_CLIENT_ID 와 NAVER_CLIENT_SECRET 을 넣어주세요. (.env.example 참고)")

    keywords = sys.argv[1:] or DEFAULT_KEYWORDS
    if len(keywords) > 5:
        sys.exit("데이터랩은 한 번에 키워드 그룹을 5개까지만 받습니다.")

    end = date.today() - timedelta(days=1)   # 오늘 데이터는 아직 집계 전일 수 있음
    start = end - timedelta(days=30)
    result = fetch_trend(client_id, client_secret, keywords, start, end)

    # 기간(주 시작일)별로 키워드 값을 모은다. 검색이 너무 적은 주는 응답에서 빠지므로 0으로 채움.
    periods = sorted({d["period"] for g in result["results"] for d in g["data"]})
    table = {g["title"]: {d["period"]: d["ratio"] for d in g["data"]} for g in result["results"]}

    print(f"네이버 데이터랩 주간 검색 추이 ({start} ~ {end}, 최대값=100 기준 상대값)\n")
    header = ["주 시작일"] + keywords
    print("  ".join(f"{h:>10}" for h in header))
    rows = []
    for p in periods:
        row = [p] + [round(table.get(k, {}).get(p, 0.0), 2) for k in keywords]
        rows.append(row)
        print("  ".join(f"{str(v):>10}" for v in row))

    out_dir = HERE / "output"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"datalab_trend_{date.today():%Y%m%d}.csv"
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: 엑셀 한글 깨짐 방지
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"\nCSV 저장: {out_path}")
    print("※ 첫 주와 마지막 주는 기간이 잘려 7일이 다 안 들어갈 수 있습니다.")


if __name__ == "__main__":
    main()
