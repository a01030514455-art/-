"""스마트에디터 ONE 모바일 구조를 흉내 낸 테스트용 게시글 페이지와 사진을 만든다(실제 네이버 접속 없이 검증용)."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PARAS = [
    ("text", "안녕하세요, 가온집수리입니다. 오늘은 욕실 타일 들뜸 보수 현장을 소개해 드릴게요."),
    ("img", "들뜬 욕실 바닥 타일", (1600, 1200)),
    ("text", "고객님 댁 욕실 바닥 타일이 들떠서 밟을 때마다 덜컥거리는 소리가 났다. 타일 사이 줄눈도 깨져 있었습니다."),
    ("sticker",),
    ("section", "들뜸을 방치하면 생기는 문제"),
    ("text", "타일 들뜸은 단순히 보기 싫은 문제가 아니라 틈으로 물이 스며들어 아래층 누수와 곰팡이로 이어질 수 있습니다."),
    ("img", "줄눈 사이 곰팡이", (1200, 1600)),
    ("img", "", (1200, 1600)),
    ("section", "부분 보수 작업 과정"),
    ("text", "먼저 들뜬 타일만 조심스럽게 떼어내고 바닥면의 오래된 접착제를 깨끗하게 긁어냈습니다."),
    ("img", "들뜬 타일 제거", (1600, 1200)),
    ("text", "그다음 방수 프라이머를 바르고 충분히 건조한 뒤에 전용 타일 접착제를 균일하게 도포했어요."),
    ("img", "전용 접착제 균일 도포", (1600, 1200)),
    ("img", "", (1600, 1200)),
    ("text", "수평계로 높이를 맞추면서 새 타일을 단단히 밀착시켜 고정했습니다. <img src=\"/storep-phinf/sticker.png\">"),
    ("img", "수평 맞춰 타일 고정", (1200, 1600)),
    ("oglink",),
    ("text", "마지막으로 줄눈을 새로 채우고 실리콘으로 마감해 물이 스며들지 않도록 했습니다."),
    ("img", "줄눈 시공과 실리콘 마감", (1600, 1200)),
    ("text", "전체 철거 없이 부분 보수만으로도 새 욕실처럼 깔끔해졌고 공사 시간도 반나절이면 충분했습니다."),
    ("img", "보수 완료", (1200, 1600)),
    ("text", "욕실 타일 들뜸이나 줄눈 문제로 고민이시라면 언제든 문의 주세요. 공감과 댓글은 큰 힘이 됩니다!"),
]

PALETTE = [(120, 140, 160), (160, 120, 100), (90, 130, 110), (140, 110, 150), (170, 150, 90), (100, 120, 170)]


def make(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    (out / "img").mkdir(exist_ok=True)
    (out / "blogpfthumb").mkdir(exist_ok=True)
    (out / "storep-phinf").mkdir(exist_ok=True)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 90)
    except OSError:
        f = ImageFont.load_default()
    Image.new("RGB", (160, 160), (200, 80, 80)).save(out / "blogpfthumb/profile.jpg")
    Image.new("RGB", (185, 160), (250, 200, 0)).save(out / "storep-phinf/sticker.png")

    body, n = [], 0
    for item in PARAS:
        kind = item[0]
        if kind == "text":
            body.append(f'<div class="se-component se-text"><div class="se-module se-module-text">'
                        f'<p class="se-text-paragraph"><span>{item[1]}</span></p></div></div>')
        elif kind == "section":
            body.append(f'<div class="se-component se-sectionTitle"><div class="se-module"><p class="se-text-paragraph">{item[1]}</p></div></div>')
        elif kind == "sticker":
            body.append('<div class="se-component se-sticker"><img src="/storep-phinf/sticker.png"></div>')
        elif kind == "oglink":
            body.append('<div class="se-component se-oglink"><a href="#"><img src="/blogpfthumb/profile.jpg"></a><span>관련 링크</span></div>')
        else:
            n += 1
            cap, (w, h) = item[1], item[2]
            im = Image.new("RGB", (w, h), PALETTE[n % len(PALETTE)])
            d = ImageDraw.Draw(im)
            for k in range(0, w + h, 120):
                d.line([(k, 0), (k - h, h)], fill=tuple(c + 25 for c in PALETTE[n % len(PALETTE)]), width=40)
            d.text((w // 2, h // 2), f"사진 {n}", font=f, fill=(255, 255, 255), anchor="mm", stroke_width=4, stroke_fill=(0, 0, 0))
            im.save(out / f"img/p{n}.jpg", quality=92)
            capdiv = f'<div class="se-caption"><p class="se-text-paragraph">{cap}</p></div>' if cap else ""
            linkdata = f'{{"src": "/img/p{n}.jpg", "originalWidth": "{w}", "originalHeight": "{h}"}}'
            # 실제 화면처럼 표시용 주소(?type=w80_blur)는 data-lazy-src와 다르게 둔다
            body.append(f'<div class="se-component se-image"><div class="se-module se-module-image">'
                        f"<a class=\"se-module-image-link\" data-linkdata='{linkdata}'>"
                        f'<img class="se-image-resource" src="/img/p{n}.jpg?type=w80_blur" data-lazy-src="/img/p{n}.jpg?type=w966" width="{w // 4}" height="{h // 4}"></a>'
                        f'</div>{capdiv}</div>')
    html = f"""<!doctype html><html><head><meta charset="utf-8"><meta property="og:title" content="[가온집수리] 욕실 타일 들뜸 부분 보수 후기 | 반나절 시공">
<title>테스트 블로그</title></head><body>
<div class="blog_profile"><img src="/blogpfthumb/profile.jpg" width="160" height="160"></div>
<div class="se-viewer"><div class="se-component se-documentTitle"><div class="se-title-text"><span>[가온집수리] 욕실 타일 들뜸 부분 보수 후기 | 반나절 시공</span></div></div>
<div class="se-main-container">{''.join(body)}</div></div></body></html>"""
    (out / "post.html").write_text(html, encoding="utf-8")
    return n


if __name__ == "__main__":
    print(make(Path(sys.argv[1])))
