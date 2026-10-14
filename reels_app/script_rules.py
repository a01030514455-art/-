"""무료(규칙 기반) 대본 생성. AI 호출 없이 본문 문장을 골라 레퍼런스 말투로 다듬는다.

레퍼런스 구조: 훅 질문 → 문제/이유 → "그래서 오늘은" → 작업 과정 → 결과 → 상담 CTA.
"""
import re
from collections import Counter

from . import hooks

JOSA = ("으로는", "에서는", "이라는", "에서", "으로", "에게", "까지", "부터", "처럼", "보다", "이랑", "라는",
        "은", "는", "이", "가", "을", "를", "에", "의", "도", "와", "과", "로", "만", "랑")
STOP = {"그리고", "그래서", "하지만", "그런데", "오늘", "정말", "너무", "진짜", "이번", "이렇게", "저희", "우리",
        "있는", "없는", "하는", "했던", "합니다", "있습니다", "입니다", "때문에", "그냥", "바로", "같은", "이런", "그런",
        "많이", "다시", "모두", "포스팅", "블로그", "사진", "안녕하세요"}
NOISE = re.compile(r"(안녕하세요|공감|댓글|이웃추가|구독|좋아요|문의|연락|카톡|오픈채팅|http|www\.|#\S+|☎|📞|"
                   r"\d{2,4}-\d{3,4}-\d{4}|주소|영업시간|위치는|오시는 길|주차|협찬|광고|제공받|원고료|소정의|"
                   r"네이버 예약|플레이스|포스팅|블로그|읽어주셔서|감사합니다|다음에 또|다음 포스팅)")
SENT_END = re.compile(r"(다|요|죠|까|네|군|지|음|함|됨|[.!?])$")


def _is_subheading(line: str) -> bool:
    """소제목처럼 보이는 줄(짧고 문장으로 끝나지 않음, 번호·기호로 시작)은 본문에서 뺀다."""
    t = line.strip(" ~^*-•·▶▷►✔✅☑■□◆◇●○★☆♥♡❤|[]【】<>《》「」『』")
    if not t:
        return True
    if re.match(r"^(\d+[.)]|[①-⑳]|STEP|Step|step|PART|Part|#)", t) and len(t) <= 30:
        return True
    return len(t) <= 25 and not SENT_END.search(t)
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
        if _is_subheading(line):
            continue
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


PROCESS = re.compile(r"(먼저|우선|그다음|그 다음|이후|다음으로|마지막으로|떼어|떼고|제거|철거|도포|발라|바르|바른|고정|설치|심었|심어|"
                     r"박아|타공|깨고|깨서|파고|파낸|채우|채워|마감|교체했|시공했|작업했|맞춘|맞춰|잘라|재단|붙였|붙이|깔았|깔고|칠했|"
                     r"칠하|긁어|정리한|정리했|부착|연결|교체해|시공해|작업해|진행했)")
LEADING_CONN = re.compile(r"^(그리고|그래서|그다음에?|그 다음에?|다음으로|이후에?|먼저|우선|마지막으로|마지막에|또한|또|이어서)\s*,?\s*")
RESULT = re.compile(r"(완료|깔끔|튼튼|든든|안심|달라졌|새것|새 것|마무리|끝났|해결)")
BLOG_INTRO = re.compile(r"^(그래서 )?(오늘은|이번 포스팅|이번에는|이번 시간).{0,40}(소개|보여|알려|포스팅)")


def _overlap(a: str, b: str) -> float:
    wa, wb = {_stem(w) for w in a.split()}, {_stem(w) for w in b.split()}
    wa, wb = {w for w in wa if len(w) >= 2}, {w for w in wb if len(w) >= 2}
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


def _flow(scenes):
    """레퍼런스처럼 한 사람이 이어서 말하듯 연결어를 붙인다."""
    procs = [s for s in scenes if s["role"] == "process"]
    has_intro = any(s["role"] == "intro" for s in scenes)
    first_situation = True
    for s in scenes:
        t = s["narration"]
        if s["role"] == "situation" and first_situation:
            first_situation = False
            if any(x["role"] == "why" for x in scenes) and not re.match(r"^(실제로|이번|저희|고객)", t):
                t = "실제로 " + LEADING_CONN.sub("", t)
        elif s["role"] == "lead":
            t = ("이번 현장에서는 이렇게 진행했습니다." if has_intro else "그래서 이번 현장에서는 이렇게 진행했습니다.")
        elif s["role"] == "process" and len(procs) >= 2:
            i = procs.index(s)
            body = LEADING_CONN.sub("", t)
            if i == 0:
                t = "먼저 " + body
            elif i == len(procs) - 1:
                # "~했습니다" → "~해 주면 끝입니다" (레퍼런스 말투)
                body2 = re.sub(r"했(습니다|어요)\.?$", "해 주면 끝입니다.", body)
                t = ("마지막으로 " + body) if body2 == body else ("이제 " + body2)
            else:
                t = ("그다음 " if i % 2 == 1 else "이어서 ") + body
        elif s["role"] == "result" and not re.match(r"^(이렇게|덕분에|그 결과|이제|결과적으로)", t):
            t = "이렇게 " + LEADING_CONN.sub("", t)
        s["narration"] = t


def build(post: dict, settings: dict, chars_per_sec: float = 6.2, topic_id: str = "auto",
          hook_style: str = "question") -> dict:
    """주제별 후킹 구조로 대본을 만든다: 훅 → 왜 필요한지 → 이런 시공 → 이번 현장 진행 → 결과 → 상담."""
    title = post.get("title", "")
    topic = topic_from_title(title)
    full = title + "\n" + "\n".join((b.get("text") or "") + " " + (b.get("caption") or "") for b in post["blocks"])
    if not topic_id or topic_id == "auto":
        T, _ = hooks.detect(full)
    else:
        T = hooks.BY_ID.get(topic_id, hooks.GENERIC)
    hook_style = hook_style if hook_style in hooks.HOOK_STYLES else "question"
    kws = [k for k in T["keywords"] if k in full] + keywords(post)
    kws = list(dict.fromkeys(kws))

    # 1) 블로그 문장 후보(현장 사실은 블로그 문장만 사용)
    cands, pos, heading = [], 0, ""
    for bi, b in enumerate(post["blocks"]):
        if b["type"] == "heading":
            heading = re.sub(r"^(\d+[.)]\s*|[①-⑳]\s*|STEP\s*\d+\.?\s*|[^\w가-힣]+)", "", b["text"]).strip()[:24]
        if b["type"] != "text":
            continue
        for s in split_sentences(b["text"]):
            n = speech_len(s)
            if n < 8 or NOISE.search(s) or BLOG_INTRO.search(s):
                continue
            score = sum(2 for k in kws[:8] if k in s) + sum(1 for k in kws[8:] if k in s)
            score += 1.5 if 12 <= n <= 45 else 0
            score += 4 if PROCESS.search(s) else 0  # 실제 현장 작업 문장을 가장 우선
            score += 2 if RESULT.search(s) else 0
            cands.append({"text": polish(s), "block": bi, "pos": pos, "score": score, "heading": heading,
                          "result": bool(RESULT.search(s))})
            pos += 1

    hook = T["hooks"][hook_style].format(topic=topic)
    cta = settings["cta_text"]
    budget = settings["target_seconds"] * chars_per_sec - speech_len(hook) - speech_len(cta)

    # 2) 중요도 순서대로 예산 안에서 채우기
    blog = sorted(cands, key=lambda c: (-c["score"], c["pos"]))
    pts = [(keys, lab, txt) for keys, (lab, txt) in T["points"].items() if any(k in full for k in keys)]
    tpl = {"why0": T["why"][0] if T["why"] else "", "why1": T["why"][1] if len(T["why"]) > 1 else "",
           "intro": T["intro"], "lead": "이번 현장에서는 이렇게 진행했습니다." if cands else "",
           "result": T["result"]}
    order = (["why0", "lead"] + [("b", c) for c in blog[:4]] + [("p", p) for p in pts[:1]] +
             [("b", c) for c in blog[4:6]] + ["why1"] + [("p", p) for p in pts[1:2]] + ["intro"] +
             [("b", c) for c in blog[6:]] + ["result"])
    chosen, picked, picked_pts, used = set(), [], [], 0
    for item in order:
        if isinstance(item, str):
            txt = tpl[item]
        else:
            txt = item[1]["text"] if item[0] == "b" else item[1][2]
        if not txt:
            continue
        if not isinstance(item, str) and item[0] == "b" and any(txt[:10] == c["text"][:10] for c in picked):
            continue
        n = speech_len(txt)
        if used + n > budget:
            continue
        used += n
        if isinstance(item, str):
            chosen.add(item)
        elif item[0] == "b":
            picked.append(item[1])
        else:
            picked_pts.append(item[1])
    picked.sort(key=lambda c: c["pos"])
    if not picked and cands:
        picked = [cands[0]]
    # 블로그에 같은 취지의 문장이 있으면 실제 글 문장을 쓰고 템플릿 "왜" 문장은 뺀다
    for w in ("why0", "why1"):
        if w in chosen and any(_overlap(tpl[w], c["text"]) >= 0.3 for c in picked):
            chosen.discard(w)
    has_blog_result = any(c["result"] for c in picked[-2:])

    # 3) 장면 순서 구성: 훅 → 왜 → 실제 상황 → 이런 시공 → 그래서 이번 현장 → 과정 → 포인트 → 결과 → 상담
    proc_i = next((i for i, c in enumerate(picked) if PROCESS.search(c["text"]) and not c["result"]), None)
    situation = [c for i, c in enumerate(picked) if proc_i is None or i < proc_i]
    rest = [c for i, c in enumerate(picked) if proc_i is not None and i >= proc_i]
    if proc_i is None:  # 과정 문장이 없으면 결과 문장만 뒤로
        rest = [c for c in situation if c["result"]]
        situation = [c for c in situation if not c["result"]]
    first_block = picked[0]["block"] if picked else 0
    proc_block = rest[0]["block"] if rest else first_block
    last_block = picked[-1]["block"] if picked else 0

    def sc(txt, role, label="", block=0.0, heading="", result=False):
        return {"narration": txt, "label": label, "kind": role if role in ("hook", "cta") else "body", "role": role,
                "block": block, "heading": heading, "result": result, "images": []}

    scenes = [sc(hook, "hook", topic, -1)]
    whys = [w for w in ("why0", "why1") if w in chosen]
    for j, w in enumerate(whys):
        scenes.append(sc(tpl[w], "why", "왜 해야 할까요?" if j == 0 else "", first_block * (j + 1) / (len(whys) + 2) - 0.5))
    for j, c in enumerate(situation):
        scenes.append(sc(c["text"], "situation", "", c["block"], c["heading"], c["result"]))
    if "intro" in chosen:
        scenes.append(sc(tpl["intro"], "intro", T["name"] + " 시공" if T["id"] != "generic" else "", proc_block - 0.5))
    if "lead" in chosen and rest:
        scenes.append(sc(tpl["lead"], "lead", "이번 현장", proc_block - 0.25))
    placed = set()
    for c in rest:
        scenes.append(sc(c["text"], "result" if c["result"] else "process", "", c["block"], c["heading"], c["result"]))
        for p in picked_pts:  # 포인트 문장은 관련 키워드가 나온 블로그 문장 바로 뒤에
            if id(p) not in placed and any(k in c["text"] for k in p[0]):
                scenes.append(sc(p[2], "point", p[1], c["block"] + 0.1))
                placed.add(id(p))
    at = len(scenes) - 1 if scenes[-1]["role"] == "result" else len(scenes)
    for p in picked_pts:  # 남은 포인트는 결과 문장 앞에
        if id(p) not in placed:
            scenes.insert(at, sc(p[2], "point", p[1], scenes[at - 1]["block"] + 0.1))
            at += 1
    if "result" in chosen and not has_blog_result:
        scenes.append(sc(tpl["result"], "result", "시공 완료", last_block + 0.2))
    scenes.append(sc(cta, "cta", "", 10**9))
    _flow(scenes)

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
    end_header = settings.get("end_header") or T["cta_header"] or f"{topic} 전문 상담"
    return {"mode": "rules", "topic": topic, "topic_id": T["id"], "topic_name": T["name"], "hook_style": hook_style,
            "topic_choice": topic_id or "auto", "end_header": end_header, "keywords": kws, "scenes": scenes}


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
