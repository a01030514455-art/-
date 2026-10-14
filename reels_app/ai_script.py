"""선택 기능: Claude로 대본 작성. 기본 꺼짐이며, 실행 전 예상 비용을 계산해 승인을 받아야 한다.

비용을 줄이려고 사진 자체는 보내지 않고(이미지 토큰 비용 큼) 본문 텍스트와 사진 위치·설명만 한 번 보낸다.
"""
import json
import os
import re

from .script_rules import assign_images, keywords, topic_from_title

SYSTEM = """너는 집수리·시공 업체의 인스타 릴스/유튜브 쇼츠 대본 작가다.
블로그 글을 40~60초 분량의 한국어 내레이션 대본으로 바꾼다.
말투와 구조(레퍼런스 영상 기준):
- 친근한 존댓말(~죠?, ~했는데요, ~입니다, ~거예요). 짧은 문장, 과장/허위 금지, 글에 없는 사실 추가 금지.
- 1장면: 시청자 공감 훅 질문(예: "멀쩡해 보이던 우리 집 바닥, 파손된 거 보신 적 있으시죠?")
- 2장면: 왜 문제인지(예: "단순히 미관상 문제만 있는 게 아니라 ~")
- "그래서 오늘은 ~" 로 해결 소개 → 작업 과정 2~5장면 → 결과 1장면
- 마지막 장면은 반드시 CTA 문장 그대로.
- 각 장면 label은 화면 상단에 뜨는 12자 안팎 요약 문구(예: "전용 접착제 균일 도포").
- highlight는 자막에서 색으로 강조할 핵심 단어 1개(내레이션에 그대로 등장해야 함).
- images는 그 장면에 보여줄 사진 번호 목록. 모든 사진 번호를 원래 순서대로 빠짐없이 한 번 이상 배치.
- 전체 내레이션 한글 글자 수 합계는 TARGET_CHARS자 안팎.
JSON만 출력: {"topic": "...", "end_header": "OO 전문 상담", "scenes": [{"narration": "...", "label": "...", "highlight": ["..."], "images": [1,2]}]}"""


def build_prompt(post: dict, settings: dict) -> tuple[str, str]:
    lines = [f"제목: {post.get('title', '')}", "본문(사진 위치 포함):"]
    for b in post["blocks"]:
        if b["type"] in ("text", "heading"):
            lines.append(("## " if b["type"] == "heading" else "") + b["text"])
        elif b.get("file"):
            cap = b.get("caption") or b.get("alt") or ""
            lines.append(f"[사진 {b['index']}{': ' + cap if cap else ''}]")
    target_chars = int(settings["target_seconds"] * 6.2)
    system = SYSTEM.replace("TARGET_CHARS", str(target_chars))
    user = "\n".join(lines) + f"\n\nCTA 문장: {settings['cta_text']}"
    return system, user


def estimate(post: dict, settings: dict) -> dict:
    """대략적 추정(한국어는 글자당 약 1토큰으로 보수적으로 계산). 실제 사용량은 실행 후 응답의 usage로 기록한다."""
    system, user = build_prompt(post, settings)
    in_tok = int(len(system) * 1.0 + len(user) * 1.0) + 50
    out_tok = 2500  # 대본 JSON + 내부 추론 여유분(상한 추정)
    usd = in_tok / 1e6 * settings["ai_price_in_per_mtok"] + out_tok / 1e6 * settings["ai_price_out_per_mtok"]
    return {"model": settings["ai_model"], "input_tokens_est": in_tok, "output_tokens_est": out_tok,
            "usd_est": round(usd, 5), "krw_est": round(usd * settings["usd_to_krw"], 1),
            "api_key_found": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))}


def generate(post: dict, settings: dict) -> tuple[dict, dict]:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("AI 대본을 쓰려면 `pip install anthropic`이 필요합니다.")
    system, user = build_prompt(post, settings)
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=settings["ai_model"], max_tokens=8000, system=system,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": user}],
    )
    u = msg.usage
    usd = u.input_tokens / 1e6 * settings["ai_price_in_per_mtok"] + u.output_tokens / 1e6 * settings["ai_price_out_per_mtok"]
    usage = {"model": settings["ai_model"], "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
             "usd": round(usd, 5), "krw": round(usd * settings["usd_to_krw"], 1)}
    if msg.stop_reason not in ("end_turn", "stop_sequence"):
        raise RuntimeError(f"AI 응답이 완료되지 않았습니다(stop_reason={msg.stop_reason}).")
    text = "".join(b.text for b in msg.content if b.type == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError("AI 응답에서 JSON을 찾지 못했습니다.")
    data = json.loads(m.group(0))

    by_index = {b["index"]: b for b in post["blocks"] if b["type"] == "image" and b.get("file")}
    scenes = []
    for i, s in enumerate(data["scenes"]):
        kind = "hook" if i == 0 else ("cta" if i == len(data["scenes"]) - 1 else "body")
        scenes.append({"narration": s["narration"].strip(), "label": s.get("label", "")[:24], "kind": kind,
                       "highlight": [h for h in s.get("highlight", []) if h in s["narration"]][:2],
                       "images": [by_index[n] for n in s.get("images", []) if n in by_index]})
    if scenes[-1]["kind"] == "cta":
        scenes[-1]["images"] = []
    if not any(s["images"] for s in scenes) and by_index:  # 사진 배치를 안 했으면 규칙 기반으로 배치
        for s in scenes:
            s["block"] = 0
        assign_images(post, scenes)
    topic = data.get("topic") or topic_from_title(post.get("title", ""))
    script = {"mode": "ai", "topic": topic, "end_header": settings.get("end_header") or data.get("end_header") or f"{topic} 전문 상담",
              "keywords": keywords(post), "scenes": scenes}
    return script, usage
