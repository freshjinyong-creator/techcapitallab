import os
from PIL import Image, ImageDraw, ImageFont

OUT = "/home/freshjinyong/techcapitallab/src/assets/images/broker-report-market-closing-roundup-2026-09-10-og.png"
os.makedirs(os.path.dirname(OUT), exist_ok=True)

FONT_BOLD = os.path.expanduser("~/.local/share/fonts/Pretendard-Bold.otf")
FONT_SEMI = os.path.expanduser("~/.local/share/fonts/Pretendard-SemiBold.otf")

def main():
    scale = 2
    w, h = 1200 * scale, 630 * scale
    
    # Create base dark slate canvas
    canvas = Image.new("RGBA", (w, h), (7, 10, 19, 255))
    d = ImageDraw.Draw(canvas)
    
    # Draw subtle background grid/glow
    for x in range(0, w, 60 * scale):
        d.line([(x, 0), (x, h)], fill=(30, 41, 59, 100), width=1)
    for y in range(0, h, 60 * scale):
        d.line([(0, y), (w, y)], fill=(30, 41, 59, 100), width=1)
        
    lx = 70 * scale

    # Brand + Meta
    d.text((lx, 50 * scale), "TECH CAPITAL LAB", fill=(56, 189, 248),
           font=ImageFont.truetype(FONT_BOLD, 22 * scale))
    d.text((lx, 90 * scale), "3. 증권사 리포트 읽기  •  2026.09.10 MARKET CLOSING", fill=(148, 163, 184),
           font=ImageFont.truetype(FONT_SEMI, 15 * scale))

    # Main Titles
    d.text((lx, 150 * scale), "KOSPI 7,000pt 안착 & 2차전지 급등", fill=(255, 255, 255),
           font=ImageFont.truetype(FONT_BOLD, 42 * scale))
    d.text((lx, 215 * scale), "9월 10일 증권사 장 마감 시황 총정리", fill=(56, 189, 248),
           font=ImageFont.truetype(FONT_BOLD, 38 * scale))
    d.text((lx, 285 * scale), "외국인 5일 연속 순매수 / LG엔솔 +6.5% / SK하이닉스 +3.5%", fill=(226, 232, 240),
           font=ImageFont.truetype(FONT_SEMI, 20 * scale))

    # Glass Data Cards (3개)
    cy, ch = 380 * scale, 170 * scale
    cards = [
        ("KOSPI / KOSDAQ", "7,000pt 안착", "코스피 +1.4% / 코스닥 +2.3%", (56, 189, 248)),
        ("2차전지 셀 반사수혜", "LGES +6.5%", "미 Ford-중국 배터리 규제", (16, 185, 129)),
        ("반도체/AI 훈풍", "하이닉스 +3.5%", "GPT-6 Astra 모멘텀", (245, 158, 11)),
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

    # Downsample to 1200x630
    canvas.resize((1200, 630), Image.Resampling.LANCZOS).save(OUT, optimize=True)
    print("OG Image successfully generated:", OUT)

if __name__ == "__main__":
    main()
