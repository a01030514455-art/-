"""작업(job) 단위 진행 관리: 추출 → 대본 → (선택) AI 대본 → 영상. 상태는 output/<id>/job.json에 저장."""
import json
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import ai_script, extractor, render, script_rules, tts
from .config import OUTPUT_DIR, ROOT, load_settings

JOBS: dict = {}
_lock = threading.Lock()


def _web(path: str) -> str:
    try:
        return "/files/" + Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return ""


class Job:
    def __init__(self, url, jid=None):
        self.id = jid or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.dir = OUTPUT_DIR / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state = {"id": self.id, "url": url, "status": "created", "progress": 0, "message": "", "logs": [],
                      "report": None, "script": None, "script_edited": False, "result": None, "error": None,
                      "usage": {"ai_calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0, "krw": 0.0,
                                "tts": "", "note": "AI 미사용(무료 규칙 기반)"}}
        self.post = None
        self.busy = False

    # ---- 상태 ----
    def log(self, msg):
        self.state["logs"].append(time.strftime("%H:%M:%S ") + str(msg))
        self.state["logs"] = self.state["logs"][-200:]
        self.save()

    def set(self, status=None, progress=None, message=None):
        if status:
            self.state["status"] = status
        if progress is not None:
            self.state["progress"] = round(progress, 1)
        if message is not None:
            self.state["message"] = message
        self.save()

    def save(self):
        (self.dir / "job.json").write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")

    def public(self):
        s = dict(self.state)
        if s["script"]:
            s["script"] = script_for_ui(s["script"])
        if self.post:
            s["images"] = [{"index": b["index"], "url": _web(b["file"]), "caption": b.get("caption", ""),
                            "w": b.get("w"), "h": b.get("h")} for b in self.post["images"]]
        if s["result"]:
            s["result"] = dict(s["result"], video_url=_web(s["result"]["video"]), srt_url=_web(s["result"]["srt"]))
        return s

    def _run(self, fn):
        if self.busy:
            raise RuntimeError("이미 진행 중인 작업이 있습니다.")
        self.busy = True

        def wrap():
            try:
                fn()
            except Exception as e:
                self.state["error"] = str(e)
                self.log("오류: " + str(e))
                self.log(traceback.format_exc().splitlines()[-1])
                self.set(status="error", message=str(e))
            finally:
                self.busy = False
        threading.Thread(target=wrap, daemon=True).start()

    # ---- 단계 ----
    def start_extract(self):
        self._run(self._extract)

    def _extract(self):
        self.set(status="extracting", progress=2, message="블로그 글 여는 중")
        post = extractor.extract(self.state["url"], log=self.log)
        rep = post["report"]
        self.state["report"] = rep
        if not rep["body_ok"] and not post["images"]:
            raise RuntimeError("추출 실패: " + ("; ".join(rep["errors"]) or "원인 불명"))
        self.post = post
        (self.dir / "post.json").write_text(json.dumps(post, ensure_ascii=False, indent=1), encoding="utf-8")
        self.set(progress=60, message="무료 대본 작성 중")
        self.state["script"] = script_rules.build(post, load_settings())
        self.set(status="ready", progress=100, message="추출·대본 완료 — 확인 후 영상 만들기를 누르세요")

    def ai_estimate(self):
        return ai_script.estimate(self.post, load_settings())

    def start_ai(self):
        self._run(self._ai)

    def _ai(self):
        self.set(status="ai", progress=10, message="AI 대본 작성 중")
        script, usage = ai_script.generate(self.post, load_settings())
        u = self.state["usage"]
        u["ai_calls"] += 1
        for k in ("input_tokens", "output_tokens", "usd", "krw"):
            u[k] = round(u[k] + usage[k], 5)
        u["note"] = f"AI 사용: {usage['model']}"
        self.state["script"] = script
        self.state["script_edited"] = False
        self.log(f"AI 사용량: 입력 {usage['input_tokens']} / 출력 {usage['output_tokens']} 토큰, ${usage['usd']}")
        self.set(status="ready", progress=100, message="AI 대본 완료")

    def update_script(self, ui_script):
        by_index = {b["index"]: b for b in self.post["images"]}
        scenes = []
        for s in ui_script["scenes"]:
            hl = [h.strip() for h in str(s.get("highlight", "")).split(",") if h.strip()]
            scenes.append({"narration": s["narration"].strip(), "label": s.get("label", "").strip(),
                           "kind": s.get("kind", "body"), "highlight": hl,
                           "images": [by_index[i] for i in s.get("images", []) if i in by_index]})
        self.state["script"] = dict(self.state["script"], scenes=[s for s in scenes if s["narration"]],
                                    end_header=ui_script.get("end_header", self.state["script"]["end_header"]))
        self.state["script_edited"] = True
        self.save()

    def start_render(self, tts_engine=None):
        self._run(lambda: self._render(tts_engine))

    def _render(self, tts_engine):
        settings = load_settings()
        if tts_engine:
            settings["tts_engine"] = tts_engine
        self.set(status="rendering", progress=1, message="음성 길이 확인 중")
        script = self.state["script"]
        # 무료 대본이고 사용자가 고치지 않았다면, 실제 음성 속도로 한 번 보정해 40~60초에 맞춘다(TTS 결과는 캐시됨).
        if script["mode"] == "rules" and not self.state["script_edited"]:
            secs = sum(tts.synth(s["narration"], self.dir / "tts", settings, self.log)["seconds"] + render.GAP_AFTER
                       for s in script["scenes"]) + render.CTA_HOLD
            if not (settings["min_seconds"] + 2 <= secs <= settings["max_seconds"] - 2):
                chars = sum(script_rules.speech_len(s["narration"]) for s in script["scenes"])
                cps = chars / max(1.0, secs - render.CTA_HOLD - render.GAP_AFTER * len(script["scenes"]))
                self.log(f"예상 {secs:.1f}초 → 실제 말하기 속도({cps:.1f}자/초)로 대본 분량 재조정")
                script = script_rules.build(self.post, settings, chars_per_sec=cps * 0.97)
                self.state["script"] = script
        res = render.render(script, self.dir, settings, log=self.log,
                            progress=lambda p, m="": self.set(progress=p, message=m))
        eng = {"edge": "Edge 무료 음성(온라인)", "local": "로컬 무료 음성(오프라인)"}
        self.state["usage"]["tts"] = ", ".join(eng.get(e, e) for e in res["tts_engines"]) + " — 0원"
        self.state["result"] = res
        for w in res["warnings"]:
            self.log("주의: " + w)
        self.set(status="done", progress=100, message=f"완료: {res['seconds']}초 영상")


def script_for_ui(script):
    return {"mode": script["mode"], "topic": script["topic"], "end_header": script["end_header"],
            "scenes": [{"narration": s["narration"], "label": s.get("label", ""), "kind": s.get("kind", "body"),
                        "highlight": ", ".join(s.get("highlight", [])),
                        "images": [im["index"] for im in s.get("images", []) if "index" in im]}
                       for s in script["scenes"]]}


def create(url) -> Job:
    j = Job(url)
    with _lock:
        JOBS[j.id] = j
    j.save()
    return j


def get(jid) -> Job:
    if jid in JOBS:
        return JOBS[jid]
    d = OUTPUT_DIR / jid
    if (d / "job.json").exists():  # 앱 재시작 후 이전 작업 불러오기
        j = Job("", jid)
        j.state = json.loads((d / "job.json").read_text(encoding="utf-8"))
        if (d / "post.json").exists():
            j.post = json.loads((d / "post.json").read_text(encoding="utf-8"))
        if j.state["status"] in ("extracting", "rendering", "ai"):
            j.state["status"] = "error"
            j.state["error"] = "앱이 종료되어 작업이 중단되었습니다. 다시 실행해 주세요."
        JOBS[jid] = j
        return j
    raise KeyError(jid)


def list_jobs():
    out = []
    for d in sorted(OUTPUT_DIR.glob("*/job.json"), reverse=True)[:30]:
        try:
            s = json.loads(d.read_text(encoding="utf-8"))
            out.append({"id": s["id"], "url": s["url"], "status": s["status"],
                        "title": (s.get("report") or {}).get("title", ""), "usd": s["usage"]["usd"]})
        except Exception:
            pass
    return out
