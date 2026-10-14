"""무료(규칙 기반) 대본 생성. AI 호출 없이 본문 문장을 골라 레퍼런스 말투로 다듬는다.

레퍼런스 구조: 훅 질문 → 문제/이유 → "그래서 오늘은" → 작업 과정 → 결과 → 상담 CTA.
"""
import re
from collections import Counter

JOSA = ("으로는", "에서는", "이라는", "에서", "으로", "에게", "까지", "부터", "처럼", "보다", "이랑", "라는",
        "은", "는", "이", "가", "을", "를", "에", "의", "도", "와", "과", "로", "만", "랑")
STOP = {"그리고", "그래서", "하지만", "그런데", "오늘", "정말", "너무", "진짜", "이번", "이렇게", "저희", "우리",
        "있는", "없는", "하는", "했던", "합니다", "있습니다", "입니다", "때문에", "그냥", "바로", "같은", "이런", "그런",
        "많이", "다시", "모두", "포스팅", "블로그", "사진", "안녕하세요"}
NOISE = re.compile(r"(안녕하세요|공감|댓글|이웃추가|구독|좋아요|문의|연락|카톡|오픈채팅|http|www\.|#\S+|☎|📞)")
TOPIC_TAIL = re.compile(r"\s*(후기|리뷰|현장|시공기|작업기|내돈내산|솔직|정보|방법|추천|공유|기록|이야기|일지)$")
END_FIX = [
    (r"(했|였|았|었|났|됐|왔|갔|졌)다$", r"\1습니다"), (r"이다$", "입니다"), (r"있다$", "있습니다"),
    (r"없다$", "없습니다"), (r"된다$", "됩니다"), (r"한다$", "합니다"), (r"했음$", "했어요"), (r"됨$", "됐어요"),
    (r"함$", "해요"), (r"좋다$", "좋습니다"), (r"같다$", "같습니다"),
]


def _stem(word: str) -> str:
    w = re.sub(r"[^\w가-힣]", "", word)
    for j in JOSA:
        if len(w) > len(j) + 1 and w.endswith(j):
            return w[: -len(j)]
    return w


def keywords(post: dict, n=15) -> list:
    words = []
    for b in post["blocks"]:
        if b["type"] in ("text", "heading"):
            words += [_stem(w) for w in b["text"].split()]
        if b.get("caption"):
            words += [_stem(w) for w in b["caption"].split()] * 2
    words += [_stem(w) for w in post.get("title", "").split()] * 3
    c = Counter(w for w in words if len(w) >= 2 and re.search(r"[가-힣]", w) and w not in STOP)
    return [w for w, _ in c.most_common(n)]


def topic_from_title(title: str) -> str:
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】", " ", title)
    t = re.split(r"[|/·~\-–]", t)[0]
    t = re.sub(r"[^\w가-힣\s,!?]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" ,")
    words = t.split()
    out = ""
    for w in words:
        if len(out) + len(w) > 18:
            break
        out = (out + " " + w).strip()
    out = out or title[:18]
    while TOPIC_TAIL.search(out) and len(TOPIC_TAIL.sub("", out)) >= 4:
        out = TOPIC_TAIL.sub("", out)
    return out


def split_sentences(text: str) -> list:
    out = []
    for line in text.split("\n"):
        for s in re.split(r"(?<=[.!?。])\s+", line):
            s = s.strip(" ~^*-•·")
            s = re.sub(r"[ㅋㅎㅠㅜ]{2,}|[^\w가-힣\s.,!?%()~'\"’‘“”:/+-]", "", s).strip()
            if not s:
                continue
            if len(s) > 70:  # 너무 긴 문장은 쉼표에서 나눈다
                parts = [p.strip() for p in re.split(r",\s*", s) if p.strip()]
                out += parts
            else:
                out.append(s)
    return out


def polish(s: str) -> str:
    s = s.rstrip(".!? ")
    for pat, rep in END_FIX:
        if re.search(pat, s):
            s = re.sub(pat, rep, s)
            break
    if not re.search(r"[.!?]$", s):
        s += "?" if re.search(r"(까|죠|나요|가요)$", s) else "."
    return s


def speech_len(s: str) -> int:
    """TTS 길이 추정용 음절 수(한글/영숫자)."""
    return len(re.findall(r"[가-힣A-Za-z0-9]", s))


def build(post: dict, settings: dict, chars_per_sec: float = 6.2) -> dict:
    title = post.get("title", "")
    topic = topic_from_title(title)
    kws = keywords(post)

    # 1) 후보 문장 수집(원래 위치 기억) + 섹션 제목
    cands, pos, heading = [], 0, ""
    for bi, b in enumerate(post["blocks"]):
        if b["type"] == "heading":
            heading = b["text"][:24]
        if b["type"] != "text":
            continue
        for s in split_sentences(b["text"]):
            n = speech_len(s)
            if n < 8 or NOISE.search(s):
                continue
            score = sum(2 for k in kws[:8] if k in s) + sum(1 for k in kws[8:] if k in s)
            score += 1.5 if 12 <= n <= 45 else 0
            score += 1 if re.search(r"(했|합니다|해요|됩니다|있습니다|드렸|시공|작업|교체|보수|완료|마무리)", s) else 0
            cands.append({"text": polish(s), "block": bi, "pos": pos, "score": score, "heading": heading})
            pos += 1

    hook = f"{topic}, 이런 고민 해보신 적 있으시죠?"
    intro = f"그래서 오늘은 {topic} 과정을 보여드릴게요."
    cta = settings["cta_text"]
    budget = settings["target_seconds"] * chars_per_sec - speech_len(hook) - speech_len(cta) - speech_len(intro)

    # 2) 점수 높은 문장부터 예산 안에서 고르고, 원래 순서로 정렬
    picked, used = [], 0
    for c in sorted(cands, key=lambda c: (-c["score"], c["pos"])):
        n = speech_len(c["text"])
        if used + n > budget:
            continue
        if any(c["text"][:10] == p["text"][:10] for p in picked):
            continue
        picked.append(c)
        used += n
    picked.sort(key=lambda c: c["pos"])
    if not picked and cands:
        picked = cands[:1]

    first_block = picked[0]["block"] if picked else 0
    scenes = [{"narration": hook, "label": topic, "kind": "hook", "block": -1, "images": []}]
    has_today = False
    for c in picked:
        if c["text"].startswith("오늘은") and not has_today:  # 레퍼런스의 "그래서 오늘은 ~" 전환
            c["text"], has_today = "그래서 " + c["text"], True
    if len(picked) >= 2 and not has_today:
        scenes.append({"narration": intro, "label": "", "kind": "body", "block": first_block, "images": []})
    for c in picked:
        scenes.append({"narration": c["text"], "label": "", "heading": c["heading"], "kind": "body",
                       "block": c["block"], "images": []})
    scenes.append({"narration": cta, "label": "", "kind": "cta", "block": 10**9, "images": []})

    assign_images(post, scenes)
    prev_label = topic
    for s in scenes:
        if not s["label"] and s["kind"] == "body":
            caps = [im.get("caption") for im in s["images"] if im.get("caption")] if not s.get("reused") else []
            if caps and caps[0][:24] != prev_label:
                s["label"] = caps[0][:24]
            elif s.get("heading") and s["heading"] != prev_label:
                s["label"] = s["heading"]
        prev_label = s["label"] or prev_label
    # 강조 단어는 장면마다 달라지도록, 직전 장면에서 쓴 단어는 뒤로 미룬다
    prev_hl = set()
    for s in scenes:
        found = [k for k in kws if k in s["narration"]]
        s["highlight"] = ([k for k in found if k not in prev_hl] or found)[:1]
        prev_hl = set(s["highlight"])
    scenes[-1]["highlight"] = [settings["cta_highlight"]] if settings.get("cta_highlight") in cta else scenes[-1]["highlight"]
    end_header = settings.get("end_header") or f"{topic} 전문 상담"
    return {"mode": "rules", "topic": topic, "end_header": end_header, "keywords": kws, "scenes": scenes}


def assign_images(post: dict, scenes: list):
    """사진을 원래 순서대로, 가장 가까운 앞쪽 문단의 장면에 붙인다. 사진 없는 장면은 이웃 사진을 이어 쓴다."""
    imgs = [(bi, b) for bi, b in enumerate(post["blocks"]) if b["type"] == "image" and b.get("file")]
    body = [s for s in scenes if s["kind"] != "cta"]
    for bi, b in imgs:
        target = body[0]
        for s in body[1:]:
            if s["block"] <= bi:
                target = s
        target["images"].append(b)
    # 훅 장면에는 대표 사진이 필요
    if not body[0]["images"] and imgs:
        body[0]["images"].append(imgs[0][1])
    prev = body[0]["images"]
    for s in body[1:]:
        if not s["images"]:
            s["images"] = prev[-1:]
            s["reused"] = True
        prev = s["images"]
