"""네이버 블로그 공개 게시글에서 제목·본문·본문 사진(순서/설명 포함)을 추출한다.

원칙: 공개 글만 연다. 로그인·비공개·접근 제한 페이지를 만나면 우회하지 않고 오류로 보고한다.
데스크톱 블로그는 본문이 iframe(mainFrame) 안에 있어 단순 HTTP 요청으로는 본문이 보이지 않는다.
그래서 같은 글의 모바일 주소(m.blog.naver.com)를 실제 브라우저로 열고, 지연 로딩이 끝날 때까지 스크롤한 뒤 수집한다.
"""
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import IMAGES_DIR, load_settings

# 본문 사진이 아닌 이미지(프로필, 스티커, 링크 미리보기, UI 아이콘, 광고)
EXCLUDE_URL_PATTERNS = [
    "blogpfthumb", "storep-phinf", "dthumb-phinf", "ssl.pstatic.net/static", "simg.pstatic.net",
    "/static/", "profile", "sticker", "emoticon", "adcr.naver", "veta.naver", "data:image",
]
SKIPPED_COMPONENTS = {
    "se-sticker": "스티커", "se-oglink": "링크 미리보기", "se-map": "지도", "se-placesMap": "지도",
    "se-video": "동영상", "se-oembed": "외부 임베드", "se-file": "첨부파일", "se-material": "자료 카드",
}

ACCESS_DENIED_HINTS = [
    ("비공개", "비공개 글입니다"),
    ("서로이웃", "서로이웃 공개 글입니다"),
    ("존재하지 않는", "삭제되었거나 존재하지 않는 글입니다"),
    ("삭제되었거나", "삭제되었거나 존재하지 않는 글입니다"),
    ("접근이 제한", "접근이 제한된 글입니다"),
]


class ExtractError(Exception):
    pass


def normalize_url(url: str):
    """여러 형태의 네이버 블로그 주소를 (모바일 글 주소, blogId, logNo)로 바꾼다. naver.me 단축주소는 (url, None, None)."""
    url = url.strip()
    if not re.match(r"^https?://", url):
        url = "https://" + url
    u = urlparse(url)
    host = u.netloc.lower()
    if host.endswith("naver.me"):
        return url, None, None
    if not host.endswith("blog.naver.com"):
        raise ExtractError(f"네이버 블로그 주소가 아닙니다: {host}")
    qs = parse_qs(u.query)
    blog_id = (qs.get("blogId") or [None])[0]
    log_no = (qs.get("logNo") or [None])[0]
    if not (blog_id and log_no):
        parts = [p for p in u.path.split("/") if p]
        if len(parts) >= 2 and parts[1].isdigit():
            blog_id, log_no = parts[0], parts[1]
    if not (blog_id and log_no):
        raise ExtractError("게시글 번호가 없는 주소입니다. 블로그 홈이 아니라 글 하나의 주소를 넣어 주세요.")
    return f"https://m.blog.naver.com/{blog_id}/{log_no}", blog_id, log_no


# 페이지 안에서 실행: 스마트에디터 ONE(se-*)과 구 에디터를 문서 순서대로 훑는다.
_COLLECT_JS = r"""
() => {
  const SKIP = __SKIP__;
  const clean = s => (s || '').replace(/​/g, '').replace(/ /g, ' ').replace(/[ \t]+/g, ' ').trim();
  const q = s => document.querySelector(s);
  const meta = p => { const m = document.querySelector(`meta[property="${p}"]`); return m ? m.content : ''; };
  const title = clean((q('.se-title-text') || q('.se_title .se_textarea') || q('h3.se_textarea') || q('.tit_h3') || {}).innerText)
                || clean(meta('og:title')) || clean(document.title);
  const imgInfo = (img) => {
    let src = img.getAttribute('data-lazy-src') || img.getAttribute('data-src') || img.currentSrc || img.src || '';
    let ow = 0, oh = 0, origin = '';
    const link = img.closest('a.se-module-image-link, a.__se_image_link');
    if (link && link.getAttribute('data-linkdata')) {
      try { const d = JSON.parse(link.getAttribute('data-linkdata')); origin = d.src || ''; ow = +d.originalWidth || 0; oh = +d.originalHeight || 0; } catch (e) {}
    }
    const abs = u => { try { return u ? new URL(u, location.href).href : ''; } catch (e) { return u; } };
    src = abs(src); origin = abs(origin);
    return { src, origin, w: ow || img.naturalWidth || +img.getAttribute('width') || 0, h: oh || img.naturalHeight || +img.getAttribute('height') || 0,
             alt: clean(img.getAttribute('alt')) };
  };
  const out = { title, editor: null, blocks: [], skipped: [], bodyFound: false };
  const se = q('.se-main-container');
  if (se) {
    out.editor = 'smarteditor-one'; out.bodyFound = true;
    const comps = [...se.querySelectorAll('.se-component')].filter(c => !c.parentElement.closest('.se-component'));
    for (const c of comps) {
      const cls = [...c.classList];
      const skip = cls.find(k => k in SKIP);
      if (skip) { out.skipped.push(SKIP[skip]); continue; }
      if (cls.includes('se-sectionTitle')) { out.blocks.push({ type: 'heading', text: clean(c.innerText) }); continue; }
      const imgs = [...c.querySelectorAll('img')].filter(i => !i.closest('.se-sticker, .se-oglink'));
      if (cls.some(k => /^se-image/.test(k)) || (imgs.length && !c.querySelector('.se-text-paragraph'))) {
        const cap = clean((c.querySelector('.se-caption') || {}).innerText);
        imgs.forEach((img, i) => out.blocks.push({ type: 'image', caption: cap, groupIndex: i, groupSize: imgs.length, ...imgInfo(img) }));
        continue;
      }
      const paras = [...c.querySelectorAll('.se-text-paragraph')].map(p => clean(p.innerText)).filter(Boolean);
      const text = paras.length ? paras.join('\n') : clean(c.innerText);
      if (text) out.blocks.push({ type: 'text', text });
      imgs.forEach(img => out.blocks.push({ type: 'image', caption: '', groupIndex: 0, groupSize: 1, ...imgInfo(img) }));
    }
    return out;
  }
  const old = q('#postViewArea') || q('.post_ct') || q('#viewTypeSelector') || q('.se_component_wrap');
  if (old) {
    out.editor = 'legacy'; out.bodyFound = true;
    for (const el of old.querySelectorAll('img, p, div.se_textarea, h2, h3, h4')) {
      if (el.tagName === 'IMG') { out.blocks.push({ type: 'image', caption: '', groupIndex: 0, groupSize: 1, ...imgInfo(el) }); continue; }
      if (el.querySelector('p, div.se_textarea')) continue;
      const t = clean(el.innerText);
      if (t) out.blocks.push({ type: /^H\d/.test(el.tagName) ? 'heading' : 'text', text: t });
    }
  }
  return out;
}
""".replace("__SKIP__", json.dumps(SKIPPED_COMPONENTS, ensure_ascii=False))


def _is_content_image(b: dict) -> bool:
    url = (b.get("origin") or b.get("src") or "").lower()
    if not url or any(p in url for p in EXCLUDE_URL_PATTERNS):
        return False
    w, h = b.get("w") or 0, b.get("h") or 0
    if w and h and (w < 120 or h < 120):  # 아이콘/구분선 크기
        return False
    return True


def _candidate_urls(b: dict):
    """원본 품질을 우선 시도하고, 실패하면 페이지에 표시된 주소로 내려간다."""
    seen, out = set(), []
    for u in (b.get("origin"), b.get("src")):
        if not u:
            continue
        if u.startswith("//"):
            u = "https:" + u
        base = re.sub(r"([?&])type=[^&]*&?", r"\1", u).rstrip("?&")
        for c in (base, base + ("&" if "?" in base else "?") + "type=w3840",
                  base + ("&" if "?" in base else "?") + "type=w966", u):
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


def _launch(p, channel: str, executable: str = ""):
    tried = []
    if executable:
        try:
            return p.chromium.launch(headless=True, executable_path=executable)
        except Exception as e:
            tried.append(f"{executable}: {str(e).splitlines()[0]}")
    for ch in ([channel] if channel else ["", "msedge", "chrome"]):
        try:
            return p.chromium.launch(headless=True, channel=ch or None)
        except Exception as e:  # 브라우저 미설치
            tried.append(f"{ch or 'chromium'}: {str(e).splitlines()[0]}")
    raise ExtractError("브라우저를 실행하지 못했습니다. `python -m playwright install chromium`을 실행하거나 "
                       "Edge/Chrome을 설치하세요. (" + " / ".join(tried) + ")")


def extract(url: str, log=print, out_root: Path = IMAGES_DIR, _allow_any_host=False) -> dict:
    """글을 열어 post 딕셔너리를 돌려주고, 사진은 out_root/<blogId>_<logNo>/에 저장한다.

    실패해도 예외 대신 report.errors에 원인을 담아 돌려준다(성공처럼 보고하지 않기 위해).
    """
    from playwright.sync_api import Error as PwError
    from playwright.sync_api import sync_playwright

    settings = load_settings()
    report = {"url": url, "title": "", "body_ok": False, "body_chars": 0, "images_found": 0,
              "images_downloaded": 0, "excluded_images": 0, "skipped_components": [], "errors": []}
    post = {"title": "", "blocks": [], "images": [], "report": report, "image_dir": ""}

    try:
        if _allow_any_host:
            page_url, blog_id, log_no = url, "local", "test"
        else:
            page_url, blog_id, log_no = normalize_url(url)
    except ExtractError as e:
        report["errors"].append(str(e))
        return post

    with sync_playwright() as p:
        try:
            browser = _launch(p, settings.get("browser_channel", ""),
                              settings.get("browser_executable") or os.environ.get("REELS_BROWSER", ""))
        except ExtractError as e:
            report["errors"].append(str(e))
            return post
        ctx = browser.new_context(
            locale="ko-KR", viewport={"width": 430, "height": 932}, device_scale_factor=2, is_mobile=True,
            user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"))
        page = ctx.new_page()
        try:
            log(f"페이지 여는 중: {page_url}")
            resp = page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
            if blog_id is None:  # naver.me 단축주소 → 실제 주소로 다시 정규화
                page_url, blog_id, log_no = normalize_url(page.url)
                resp = page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
            status = resp.status if resp else 0
            if status >= 400:
                raise ExtractError(f"블로그 서버 응답 오류 HTTP {status}")
            if "nid.naver.com" in page.url:
                raise ExtractError("로그인이 필요한 글입니다(로그인 우회는 하지 않습니다).")
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PwError:
                log("네트워크 대기 시간 초과 — 현재까지 로드된 내용으로 진행")
            # 지연 로딩 사진을 모두 불러오도록 끝까지 천천히 스크롤
            last = -1
            for _ in range(60):
                h = page.evaluate("document.body.scrollHeight")
                page.mouse.wheel(0, 900)
                page.wait_for_timeout(250)
                y = page.evaluate("window.scrollY + window.innerHeight")
                if y >= h and h == last:
                    break
                last = h
            page.wait_for_timeout(800)
            data = page.evaluate(_COLLECT_JS)
        except ExtractError as e:
            report["errors"].append(str(e))
            browser.close()
            return post
        except PwError as e:
            report["errors"].append("페이지 접속 실패: " + str(e).splitlines()[0])
            browser.close()
            return post

        if not data["bodyFound"]:
            body_text = page.evaluate("document.body ? document.body.innerText : ''")
            reason = next((msg for key, msg in ACCESS_DENIED_HINTS if key in body_text), None)
            report["errors"].append(reason or "본문 영역(se-main-container/postViewArea)을 찾지 못했습니다. 페이지 구조가 바뀌었을 수 있습니다.")
            browser.close()
            return post

        post["title"] = report["title"] = data["title"]
        report["skipped_components"] = sorted(set(data["skipped"]))
        blocks = []
        for b in data["blocks"]:
            if b["type"] == "image":
                if not _is_content_image(b):
                    report["excluded_images"] += 1
                    continue
            blocks.append(b)
        text_chars = sum(len(b["text"]) for b in blocks if b["type"] in ("text", "heading"))
        report["body_chars"] = text_chars
        report["body_ok"] = text_chars > 0
        if not text_chars:
            report["errors"].append("본문 텍스트가 비어 있습니다(사진만 있는 글이거나 구조 인식 실패).")

        img_blocks = [b for b in blocks if b["type"] == "image"]
        report["images_found"] = len(img_blocks)
        img_dir = out_root / f"{blog_id}_{log_no}"
        img_dir.mkdir(parents=True, exist_ok=True)
        post["image_dir"] = str(img_dir)
        for n, b in enumerate(img_blocks, 1):
            b["index"] = n
            saved = None
            last_err = ""
            for cand in _candidate_urls(b):
                try:
                    r = ctx.request.get(cand, headers={"Referer": "https://m.blog.naver.com/"}, timeout=30000)
                    ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
                    body = r.body() if r.ok else b""
                    if r.ok and ctype.startswith("image/") and len(body) > 2048:
                        path = img_dir / f"{n:03d}{_EXT.get(ctype, '.jpg')}"
                        path.write_bytes(body)
                        saved = {"file": str(path), "url": cand, "bytes": len(body)}
                        break
                    last_err = f"HTTP {r.status} {ctype}"
                except PwError as e:
                    last_err = str(e).splitlines()[0]
            if saved:
                b.update(saved)
                report["images_downloaded"] += 1
            else:
                b["error"] = last_err
                report["errors"].append(f"사진 {n} 다운로드 실패: {last_err}")
            log(f"사진 {n}/{len(img_blocks)} {'저장' if saved else '실패'}")
        browser.close()

    _fill_sizes(img_blocks)
    post["blocks"] = blocks
    post["images"] = [b for b in img_blocks if b.get("file")]
    (Path(post["image_dir"]) / "post.json").write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")
    return post


def _fill_sizes(img_blocks):
    from PIL import Image
    for b in img_blocks:
        if b.get("file"):
            try:
                with Image.open(b["file"]) as im:
                    b["w"], b["h"] = im.size
            except Exception as e:
                b["error"] = f"이미지 파일 손상: {e}"
                b.pop("file", None)
