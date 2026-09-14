import os
from PIL import Image, ImageDraw, ImageFont

OUT = "/home/freshjinyong/techcapitallab/src/assets/images/lg-exaone-hair-loss-ai-discovery-og.png"
os.makedirs(os.path.dirname(OUT), exist_ok=True)

FONT_BOLD = os.path.expanduser("~/.local/share/fonts/Pretendard-Bold.otf")
FONT_SEMI = os.path.expanduser("~/.local/share/fonts/Pretendard-SemiBold.otf")

def main():
    scale = 2
    w, h = 1200 * scale, 630 * scale
    
    canvas = Image.new("RGBA", (w, h), (7, 10, 19, 255))
    d = ImageDraw.Draw(canvas)
    
    # Grid lines
    for x in range(0, w, 60 * scale):
        d.line([(x, 0), (x, h)], fill=(30, 41, 59, 100), width=1)
    for y in range(0, h, 60 * scale):
        d.line([(0, y), (w, y)], fill=(30, 41, 59, 100), width=1)
        
    lx = 70 * scale

    d.text((lx, 50 * scale), "TECH CAPITAL LAB", fill=(56, 189, 248),
           font=ImageFont.truetype(FONT_BOLD, 22 * scale))
    d.text((lx, 90 * scale), "2. 뉴스 속 경제  •  2026.09.14 AI & BIOTECH", fill=(148, 163, 184),
           font=ImageFont.truetype(FONT_SEMI, 15 * scale))

    d.text((lx, 150 * scale), "22개월 걸릴 탈모 신소재 하루 만에 탐색", fill=(255, 255, 255),
           font=ImageFont.truetype(FONT_BOLD, 40 * scale))
    d.text((lx, 215 * scale), "LG 엑사원의 AI 소재 발굴과 람시딜의 메커니즘", fill=(56, 189, 248),
           font=ImageFont.truetype(FONT_BOLD, 36 * scale))
    d.text((lx, 285 * scale), "42만 개 후보 물질 시뮬레이션 / DKK1 억제 / ERα 수용체 활성화", fill=(226, 232, 240),
           font=ImageFont.truetype(FONT_SEMI, 20 * scale))

    cy, ch = 380 * scale, 170 * scale
    cards = [
        ("탐색 기간 단축", "22개월 → 1일", "42만 개 후보 스크리닝", (56, 189, 248)),
        ("핵심 신소재", "람시딜", "DKK1 탈모 신호 차단", (16, 185, 129)),
        ("여성형 탈모 해법", "ERα 활성화", "모낭 줄기세포 자극", (245, 158, 11)),
    ]
    
    for i, (label, value, sub, accent) in enumerate(cards):
        cx = lx + i * 360 * scale
        d.rounded_rectangle([cx, cy, cx + 330 * scale, cy + ch], radius=14 * scale,
                            fill=(15, 23, 42, 235), outline=(51, 65, 85, 220), width=scale)
        d.line([(cx + 16 * scale, cy), (cx + 100 * scale, cy)], fill=accent, width=4 * scale)
        d.text((cx + 20 * scale, cy + 20 * scale), label, fill=(148, 163, 184),
               font=ImageFont.truetype(FONT_BOLD, 16 * scale))
        d.text((cx + 20 * scale, cy + 55 * scale), value, fill=accent,
               font=ImageFont.truetype(FONT_BOLD, 28 * scale))
        d.text((cx + 20 * scale, cy + 115 * scale), sub, fill=(203, 213, 225),
               font=ImageFont.truetype(FONT_SEMI, 15 * scale))

    canvas.resize((1200, 630), Image.Resampling.LANCZOS).save(OUT, optimize=True)
    print("OG Image successfully generated:", OUT)

if __name__ == "__main__":
    main()
