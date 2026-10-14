#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
python -m pip install -q -r requirements.txt
# Chrome/Edge가 설치돼 있으면 그대로 쓰고, 없을 때만 Playwright 전용 Chromium(무료, 약 150MB)을 받습니다.
python - <<'P' || python -m playwright install chromium
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    for ch in (None, "chrome", "msedge"):
        try: p.chromium.launch(channel=ch).close(); raise SystemExit(0)
        except SystemExit: raise
        except Exception: pass
raise SystemExit(1)
P
python -m reels_app
