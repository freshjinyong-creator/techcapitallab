import os
from PIL import Image, ImageDraw, ImageFont

OUT = "/home/freshjinyong/techcapitallab/src/assets/images/lg-electronics-ai-datacenter-chiller-hvac-k-vertiv-re-rating-20261006-og.png"
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

    d.text((lx, 60 * scale), "TECH CAPITAL LAB", fill=(56, 189, 248),
           font=ImageFont.truetype(FONT_BOLD, 22 * scale))
    d.text((lx, 105 * scale), "3. 증권사 리포트 읽기  |  2026.10.06", fill=(148, 163, 184),
           font=ImageFont.truetype(FONT_SEMI, 16 * scale))

    # Main title
    d.text((lx, 190 * scale), "LG전자, '세탁기 회사' 탈피하나", fill=(255, 255, 255),
           font=ImageFont.truetype(FONT_BOLD, 46 * scale))
    d.text((lx, 265 * scale), "AI 데이터센터 쿨링과 K-Vertiv 도약", fill=(56, 189, 248),
           font=ImageFont.truetype(FONT_BOLD, 42 * scale))
    
    # Clean Card
    cy, ch = 380 * scale, 170 * scale
    cx = lx
    d.rounded_rectangle([cx, cy, cx + 1060 * scale, cy + ch], radius=16 * scale,
                        fill=(15, 23, 42, 240), outline=(51, 65, 85, 220), width=scale)
    d.line([(cx + 20 * scale, cy), (cx + 200 * scale, cy)], fill=(56, 189, 248), width=5 * scale)
    
    d.text((cx + 40 * scale, cy + 30 * scale), "LG ELECTRONICS (066570)", fill=(148, 163, 184),
           font=ImageFont.truetype(FONT_BOLD, 18 * scale))
    d.text((cx + 40 * scale, cy + 70 * scale), "HVAC · 칠러 · 액체냉각 B2B 체질 개선과 밸류에이션 리레이팅", fill=(241, 245, 249),
           font=ImageFont.truetype(FONT_BOLD, 24 * scale))

    final_img = canvas.resize((1200, 630), Image.Resampling.LANCZOS)
    final_img.convert("RGB").save(OUT, "PNG", optimize=True)
    print("OG Image successfully generated:", OUT)

if __name__ == "__main__":
    main()
