"""작업(job) 단위 진행 관리: 추출 → 대본 → (선택) AI 대본 → 영상. 상태는 output/<id>/job.json에 저장."""
import json
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import ai_script, editing, extractor, images, render, script_rules, stock, tts, voices
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
            s["images"] = [self._image_public(b) for b in self.post["images"]]
        s["stock_clips"] = [dict(c, url=_web(c["file"])) for c in self.state.get("stock_clips", [])]
        if s.get("thumbnail", {}).get("file"):
            s["thumbnail"] = dict(s["thumbnail"], url=_web(s["thumbnail"]["file"]))
        if s["result"]:
            s["result"] = dict(s["result"], video_url=_web(s["result"]["video"]), srt_url=_web(s["result"]["srt"]),
                               base_url=_web(s["result"].get("base_video") or s["result"]["video"]),
                               credits_url=_web(s["result"]["credits"]) if s["result"].get("credits") else "")
        return s

    # ---- 사진 편집 ----
    def _image_public(self, b):
        f = b.get("edited_file") or b["file"]
        try:
            ver = int(Path(f).stat().st_mtime)
        except OSError:
            ver = 0
        return {"index": b["index"], "url": f"{_web(f)}?v={ver}", "original_url": _web(b["file"]),
                "caption": b.get("caption", ""), "w": b.get("w"), "h": b.get("h"),
                "edits": b.get("edits") or images.DEFAULT_EDITS, "uploaded": bool(b.get("uploaded"))}

    def _save_post(self):
        (self.dir / "post.json").write_text(json.dumps(self.post, ensure_ascii=False, indent=1), encoding="utf-8")

    def _find_image(self, index):
        if not self.post:
            raise RuntimeError("먼저 블로그 글을 가져와야 합니다.")
        for b in self.post["images"]:
            if b["index"] == int(index):
                return b
        raise RuntimeError(f"사진 {index}번을 찾을 수 없습니다.")

    def edit_image(self, index, edits):
        b = self._find_image(index)
        e = images.clean_edits(edits)
        edited = Path(self.post["image_dir"]) / "edited" / f"{b['index']:03d}.jpg"
        if images.is_default(e):
            edited.unlink(missing_ok=True)
            b.pop("edited_file", None)
        else:
            images.apply_edits(b["file"], edited, e)
            b["edited_file"] = str(edited)
        b["edits"] = e
        self._save_post()
        self.log(f"사진 {b['index']} 편집 저장")
        return self._image_public(b)

    def add_upload(self, data: bytes):
        if not self.post:
            raise RuntimeError("먼저 블로그 글을 가져와야 합니다.")
        index = max([b["index"] for b in self.post["images"]] or [0]) + 1
        b = images.save_upload(data, Path(self.post["image_dir"]), index)
        self.post["images"].append(b)
        self._save_post()
        self.log(f"내 사진 추가: {index}번")
        return self._image_public(b)

    # ---- 수동 편집: 추가 문구·썸네일 (AI 재호출 없음) ----
    def _duration(self):
        return float((self.state.get("result") or {}).get("seconds") or 60)

    def save_overlays(self, overlays, auto_style=None):
        self.state["overlays"] = [editing.clean_overlay(o, self._duration()) for o in overlays][:300]
        if auto_style is not None:
            self.state["auto_style"] = {g: {k: v for k, v in (auto_style.get(g) or {}).items()
                                            if k in ("dy", "dx", "scale", "color", "hl_color", "bg", "bg_opacity", "shadow", "stroke")}
                                        for g in editing.AUTO_GROUPS}
        self.save()
        return self.state["overlays"]

    def start_apply_overlays(self):
        if not self.state.get("result"):
            raise RuntimeError("먼저 영상을 만들어야 합니다.")
        self._run(self._apply_overlays)

    def _apply_overlays(self):
        self.set(status="compositing", progress=10, message="추가 문구를 영상에 합성 중(무료)")
        base = self.dir / "reel_base.mp4"
        if not base.exists():  # 이 기능 전에 만든 영상: 현재 영상을 기본본으로 보관
            shutil.copyfile(self.dir / "reel.mp4", base)
            self.state["result"]["base_video"] = str(base)
        n_frames = max(1, int(self._duration() * render.FPS))
        out = editing.compose(base, self.state.get("overlays", []), self.dir / "reel.mp4", self.log,
                              lambda n: self.set(progress=10 + 89 * n / n_frames))
        self.state["result"]["video"] = str(out)
        self.state["result"]["edited_at"] = time.strftime("%H:%M:%S")
        self.set(status="done", progress=100, message=f"문구 {len(self.state.get('overlays', []))}개 반영 완료")

    def frames(self):
        if not self.state.get("result"):
            raise RuntimeError("먼저 영상을 만들어야 합니다.")
        fdir = self.dir / "frames"
        if not any(fdir.glob("clean_*.jpg")):  # 예전 영상: 1초마다 프레임만 뽑아 둔다(자막 포함)
            fdir.mkdir(exist_ok=True)
            src = self.dir / ("reel_base.mp4" if (self.dir / "reel_base.mp4").exists() else "reel.mp4")
            subprocess.run([render.ffmpeg_exe(), "-v", "error", "-y", "-i", str(src), "-vf", "fps=1",
                            "-q:v", "3", "-start_number", "0", str(fdir / "clean_%03d.jpg")], check=True)
        if "thumb_recos" not in self.state:
            self.state["thumb_recos"] = editing.recommend(self.dir, self.state["result"].get("timeline", []))
            self.save()
        files = sorted(fdir.glob("clean_*.jpg"))
        return {"frames": [{"time": int(f.stem.split("_")[1]), "url": _web(str(f))} for f in files],
                "recommended": [dict(r, url=_web(str(fdir / r["url_name"]))) for r in self.state["thumb_recos"]],
                "duration": self._duration(), "safe_zones": editing.SAFE_ZONES}

    def thumbnail(self, spec):
        if not self.state.get("result"):
            raise RuntimeError("먼저 영상을 만들어야 합니다.")
        out = editing.make_thumbnail(self.dir, spec)
        self.state["thumbnail"] = dict(spec, file=str(out))
        self.save()
        return {"url": f"{_web(str(out))}?v={int(time.time())}", "file": str(out)}

    def _attach_clips(self, script, auto_stock):
        """사용자가 고른 보조 영상을 장면에 붙인다. auto_stock이면 사진이 없어 같은 사진을 반복하는
        설명 장면(왜/소개/이번 현장/포인트)에만 남은 보조 영상을 하나씩 채운다. 원본 사진이 항상 우선."""
        lib = {c["clip_id"]: c for c in self.stock_library() if Path(c["file"]).exists()}
        unused = [c for cid, c in lib.items() if cid not in {s.get("clip_id") for s in script["scenes"]}]
        scenes = []
        for s in script["scenes"]:
            s = dict(s)
            if s.get("clip_id") in lib:
                s["clip"] = lib[s["clip_id"]]
            elif (auto_stock and unused and s.get("kind") == "body" and s.get("role") in ("why", "intro", "lead", "point")
                  and (s.get("reused") or not s.get("images"))):
                s["clip"] = unused.pop(0)
            scenes.append(s)
        return dict(script, scenes=scenes)

    def rebuild_script(self, topic_id="auto", hook_style="question"):
        """주제·훅 스타일을 바꿔 무료 대본을 다시 만든다(AI 미사용)."""
        if not self.post:
            raise RuntimeError("먼저 블로그 글을 가져와야 합니다.")
        live = {b["index"]: b for b in self.post["images"]}
        post = dict(self.post, blocks=[b for b in self.post["blocks"]
                                       if b["type"] != "image" or b.get("index") in live])
        self.state["script"] = script_rules.build(post, load_settings(), topic_id=topic_id, hook_style=hook_style)
        self.state["script_edited"] = False
        self.log(f"대본 다시 만들기: 주제 {self.state['script']['topic_name']}, 훅 {hook_style}")
        self.save()
        return script_for_ui(self.state["script"])

    def delete_image(self, index):
        """사진을 이 작업에서 뺀다(파일은 지우지 않음). 장면에 들어 있던 것도 함께 빠진다."""
        b = self._find_image(index)
        self.post["images"] = [x for x in self.post["images"] if x["index"] != b["index"]]
        self.post.setdefault("deleted", []).append(b["index"])
        self._save_post()
        if self.state.get("script"):
            for s in self.state["script"]["scenes"]:
                s["images"] = [im for im in s.get("images", []) if im.get("index") != b["index"]]
            self.save()
        self.log(f"사진 {b['index']} 삭제")
        return {"ok": True, "index": b["index"]}

    def _resolve_images(self, script, auto_stock=False):
        """대본의 사진을 현재 편집 상태(편집본 파일·중심점)로 바꾸고, 보조 영상을 붙여 렌더링에 넘긴다."""
        if not self.post:
            return script
        script = self._attach_clips(script, auto_stock)
        by_index = {b["index"]: b for b in self.post["images"]}
        scenes = []
        for s in script["scenes"]:
            ims = []
            for im in s.get("images", []):
                if im.get("index") not in by_index:
                    continue  # 삭제된 사진
                b = by_index[im["index"]]
                ims.append(dict(b, file=b.get("edited_file") or b["file"],
                                focus=(b.get("edits") or {}).get("focus", [0.5, 0.5])))
            scenes.append(dict(s, images=ims))
        all_images = [dict(b, file=b.get("edited_file") or b["file"], focus=(b.get("edits") or {}).get("focus", [0.5, 0.5]))
                      for b in sorted(self.post["images"], key=lambda b: b["index"])]
        return dict(script, scenes=scenes, all_images=all_images)

    def _run(self, fn):
        if self.busy:
            raise RuntimeError("이미 진행 중인 작업이 있습니다.")
        self.busy = True
        self.state["error"] = None

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
        if not self.post or not self.state.get("script"):
            raise RuntimeError("먼저 블로그 글을 가져와야 합니다(추출이 끝나지 않았거나 실패한 작업입니다).")
        by_index = {b["index"]: b for b in self.post["images"]}
        scenes = []
        for s in ui_script["scenes"]:
            hl = [h.strip() for h in str(s.get("highlight", "")).split(",") if h.strip()]
            scenes.append({"narration": s["narration"].strip(), "label": s.get("label", "").strip(),
                           "kind": s.get("kind", "body"), "role": s.get("role", ""), "highlight": hl,
                           "clip_id": s.get("clip_id") or "",
                           "images": [by_index[i] for i in s.get("images", []) if i in by_index]})
        self.state["script"] = dict(self.state["script"], scenes=[s for s in scenes if s["narration"]],
                                    end_header=ui_script.get("end_header", self.state["script"]["end_header"]))
        self.state["script_edited"] = True
        self.save()

    def start_render(self, tts_engine=None, tts_speed=None, voice_opts=None, auto_stock=False, approved=False):
        if not self.state.get("script"):
            raise RuntimeError("대본이 없습니다. 먼저 블로그 글을 가져오세요.")
        settings = _voice_settings(tts_engine, tts_speed, voice_opts)
        if settings["tts_engine"] == "azure":  # 비용·한도 확인은 작업 시작 전에(승인 대화를 위해)
            chk = azure_needed(self.state["script"], self.dir / "tts", settings)
            if chk["level"] == "block" or (chk["level"] in ("warn", "billable") and not approved):
                raise ApprovalNeeded(chk)
        self._run(lambda: self._render(tts_engine, tts_speed, voice_opts, auto_stock, approved=True))

    def preview_voice(self, text, voice_opts=None):
        """선택한 음성·배속·음량으로 한 문장만 미리 만들어 들려준다(결과는 캐시되어 영상 만들 때 재사용)."""
        return preview(text, voice_opts, self.dir / "tts", self.log)

    # ---- 무료 스톡 영상(보조 장면) ----
    def stock_library(self):
        return self.state.setdefault("stock_clips", [])

    def stock_add(self, item, query):
        clip = stock.download(item, self.dir, query)
        lib = [c for c in self.stock_library() if c["clip_id"] != clip["clip_id"]] + [clip]
        self.state["stock_clips"] = lib
        self.log(f"보조 영상 추가: {clip['clip_id']} ({clip['license']})")
        self.save()
        return clip

    def stock_import(self, data, filename, source_url, author, query=""):
        clip = stock.import_manual(data, filename, self.dir, source_url, author, query)
        self.state["stock_clips"] = [c for c in self.stock_library() if c["clip_id"] != clip["clip_id"]] + [clip]
        self.log(f"보조 영상 등록(직접 받은 파일): {clip['clip_id']}")
        self.save()
        return clip

    def stock_remove(self, clip_id):
        self.state["stock_clips"] = [c for c in self.stock_library() if c["clip_id"] != clip_id]
        for s in (self.state.get("script") or {}).get("scenes", []):
            if s.get("clip_id") == clip_id:
                s["clip_id"] = ""
        self.save()
        return {"ok": True}

    def _render(self, tts_engine, tts_speed=None, voice_opts=None, auto_stock=False, approved=False):
        settings = _voice_settings(tts_engine, tts_speed, voice_opts)
        self.state["voice"] = {"engine": settings["tts_engine"], "speed": settings["tts_speed"],
                               "voice": settings.get("tts_voice"), "volume": settings.get("tts_volume", 1.0)}
        if settings["tts_engine"] == "azure":
            chk = azure_needed(self.state["script"], self.dir / "tts", settings)
            if chk["level"] == "block" or (chk["level"] in ("warn", "billable") and not approved):
                raise ApprovalNeeded(chk)
        self.set(status="rendering", progress=1, message="음성 길이 확인 중")
        script = self.state["script"]
        pre_usage = {}  # 길이 보정 단계에서 먼저 만든 음성의 사용량
        # 무료 대본이고 사용자가 고치지 않았다면, 실제 음성 속도로 한 번 보정해 40~60초에 맞춘다(TTS 결과는 캐시됨).
        if script["mode"] == "rules" and not self.state["script_edited"] and self.post:
            secs = render.CTA_HOLD
            for s in script["scenes"]:
                v = tts.synth(s["narration"], self.dir / "tts", settings, self.log)
                secs += v["seconds"] + render.GAP_AFTER
                pre_usage[v["category"]] = pre_usage.get(v["category"], 0) + v.get("chars", 0)
            if not (settings["min_seconds"] + 2 <= secs <= settings["max_seconds"] - 2):
                chars = sum(script_rules.speech_len(s["narration"]) for s in script["scenes"])
                cps = chars / max(1.0, secs - render.CTA_HOLD - render.GAP_AFTER * len(script["scenes"]))
                self.log(f"예상 {secs:.1f}초 → 실제 말하기 속도({cps:.1f}자/초)로 대본 분량 재조정")
                live = {b["index"] for b in self.post["images"]}
                post = dict(self.post, blocks=[b for b in self.post["blocks"]
                                               if b["type"] != "image" or b.get("index") in live])
                script = script_rules.build(post, settings, chars_per_sec=cps * 0.97,
                                            topic_id=script.get("topic_choice", "auto"),
                                            hook_style=script.get("hook_style", "question"))
                self.state["script"] = script
        res = render.render(self._resolve_images(script, auto_stock), self.dir, settings, log=self.log,
                            progress=lambda p, m="": self.set(progress=p, message=m))
        eng = {"edge": "Edge 무료 음성", "local": "PC 내장 음성", "azure": "Azure 음성"}
        tu = dict(res["tts_usage"])
        for k, n in pre_usage.items():
            tu[k] = tu.get(k, 0) + n
        tu.pop("cache", None)
        billable = tu.get("azure_billable", 0)
        self.state["usage"]["tts"] = ", ".join(eng.get(e, e) for e in res["tts_engines"])
        self.state["usage"]["tts_breakdown"] = tu
        self.state["usage"]["tts_billable_chars"] = billable
        self.state["usage"]["tts_free"] = billable == 0
        # 추가 문구 합성(AI·음성 재호출 없음). 문구가 없으면 기본 영상을 그대로 최종본으로.
        self.set(progress=99, message="추가 문구 합성 중")
        # 자동 문구(상단 문구·자막·엔딩 문구)는 새 대본으로 다시 만들고, 사용자가 정한 그룹 설정(위치·크기·색)을 적용.
        # 직접 추가한 문구는 그대로 유지.
        style = self.state.get("auto_style", {})
        autos = [editing.apply_auto_style(editing.clean_overlay(o, res["seconds"]), style) for o in res.pop("auto_overlays")]
        mine = [editing.clean_overlay(o, res["seconds"]) for o in self.state.get("overlays", []) if not o.get("auto")]
        self.state["overlays"] = autos + mine
        n_frames = max(1, int(res["seconds"] * render.FPS))
        res["video"] = str(editing.compose(Path(res["base_video"]), self.state["overlays"], self.dir / "reel.mp4", self.log,
                                           lambda n: self.set(progress=min(99.5, 98 + 1.5 * n / n_frames))))
        self.state.pop("thumb_recos", None)
        res["render_id"] = time.strftime("%Y%m%d%H%M%S")
        if res["clips_used"]:
            (self.dir / "credits.txt").write_text(stock.credits_text(res["clips_used"]), encoding="utf-8")
            res["credits"] = str(self.dir / "credits.txt")
        self.state["result"] = res
        for w in res["warnings"]:
            self.log("주의: " + w)
        self.set(status="done", progress=100, message=f"완료: {res['seconds']}초 영상")


class ApprovalNeeded(RuntimeError):
    def __init__(self, info):
        super().__init__(info["message"] or "승인이 필요합니다")
        self.info = info


def _voice_settings(tts_engine=None, tts_speed=None, voice_opts=None):
    settings = load_settings()
    if tts_engine:
        settings["tts_engine"] = tts_engine
    if tts_speed:
        settings["tts_speed"] = round(min(1.6, max(0.7, float(tts_speed))), 2)
    vo = voice_opts or {}
    if vo.get("voice"):
        settings["tts_voice"] = vo["voice"]
    if vo.get("volume"):
        settings["tts_volume"] = round(min(1.5, max(0.5, float(vo["volume"]))), 2)
    return settings


def azure_needed(script, tts_dir, settings):
    """캐시에 없는 문장만 세어 Azure 사용 글자 수를 추정(자동 분량 보정 여유 20% 포함)."""
    speed = float(settings.get("tts_speed", 1.0))
    vol = float(settings.get("tts_volume", 1.0))
    need = 0
    for s in script["scenes"]:
        need += voices.billable_chars(voices.azure_ssml(s["narration"], settings.get("tts_voice", ""), speed, vol))
    return voices.azure_precheck(int(need * 1.2), settings)


def preview(text, voice_opts, tts_dir, log=print):
    if not (text or "").strip():
        raise RuntimeError("미리 들을 문장이 없습니다.")
    vo = voice_opts or {}
    settings = _voice_settings(vo.get("engine"), vo.get("speed"), vo)
    if settings["tts_engine"] == "azure":
        chk = voices.azure_precheck(voices.billable_chars(voices.azure_ssml(text[:200], settings["tts_voice"], 1, 1)), settings)
        if chk["level"] in ("block", "billable") and not vo.get("approved"):
            raise ApprovalNeeded(chk)
    v = tts.synth(text.strip()[:200], Path(tts_dir), settings, log)
    return {"url": _web(v["file"]), "seconds": round(v["seconds"], 2), "engine": v["engine"],
            "voice": settings.get("tts_voice"), "chars": v.get("chars", 0), "category": v.get("category")}


def script_for_ui(script):
    return {"mode": script["mode"], "topic": script["topic"], "end_header": script["end_header"],
            "topic_id": script.get("topic_id", "generic"), "topic_name": script.get("topic_name", ""),
            "topic_choice": script.get("topic_choice", "auto"), "hook_style": script.get("hook_style", "question"),
            "scenes": [{"narration": s["narration"], "label": s.get("label", ""), "kind": s.get("kind", "body"),
                        "highlight": ", ".join(s.get("highlight", [])), "role": s.get("role", ""),
                        "clip_id": s.get("clip_id", ""),
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
        if j.state["status"] in ("extracting", "rendering", "ai", "compositing"):
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
