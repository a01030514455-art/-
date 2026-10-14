"""명령줄 실행: python -m reels_app <블로그주소> [--tts edge|local|auto]
웹앱과 같은 파이프라인을 쓰며, 무료 규칙 기반 대본만 사용한다(AI 호출 없음)."""
import argparse
import json

from . import pipeline


def main():
    ap = argparse.ArgumentParser(description="네이버 블로그 → 릴스 MP4")
    ap.add_argument("url")
    ap.add_argument("--tts", choices=["auto", "edge", "local"])
    a = ap.parse_args()
    job = pipeline.create(a.url)
    job.log = lambda m: (print(m), pipeline.Job.log(job, m))
    try:
        job._extract()
    except Exception as e:
        print("추출 실패:", e)
        print(json.dumps(job.state["report"], ensure_ascii=False, indent=1))
        raise SystemExit(1)
    print(json.dumps(job.state["report"], ensure_ascii=False, indent=1))
    job._render(a.tts)
    r = job.state["result"]
    print(f"\n완료: {r['video']} ({r['seconds']}초, 사진 {r['images_used']}/{r['images_total']}장, 음성 {r['tts_engines']})")
    for w in r["warnings"]:
        print("주의:", w)
