import os
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
OUTPUT_PATH = '/home/freshjinyong/techcapitallab/src/assets/images/broker-report-market-closing-roundup-2026-09-10-og.png'
BG_PATH = '/home/freshjinyong/.gemini/antigravity-cli/brain/63016a24-21f1-42ba-9582-dda3106e495f/market_closing_20260910_1789036052945.jpg'

# 1. Load & crop background
bg = Image.open(BG_PATH).convert('RGBA')
bg_w, bg_h = bg.size
target_ratio = WIDTH / HEIGHT
curr_ratio = bg_w / bg_h

if curr_ratio > target_ratio:
    new_w = int(bg_h * target_ratio)
    left = (bg_w - new_w) // 2
    bg_cropped = bg.crop((left, 0, left + new_w, bg_h))
else:
    new_h = int(bg_w / target_ratio)
    top = (bg_h - new_h) // 2
    bg_cropped = bg.crop((0, top, bg_w, top + new_h))

bg_resized = bg_cropped.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)

# 2. Dark Slate Editorial Gradient Overlay (#070A13: RGB 7, 10, 19)
overlay = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
draw_ol = ImageDraw.Draw(overlay)

for x in range(WIDTH):
    nx = x / WIDTH
    if nx <= 0.48:
        alpha = 248 - int(nx / 0.48 * 25) # 248 -> 223
    elif nx <= 0.80:
        t = (nx - 0.48) / 0.32
        alpha = int(223 - t * 135) # 223 -> 88
    else:
        t = (nx - 0.80) / 0.20
        alpha = int(88 - t * 28) # 88 -> 60
    
    draw_ol.line([(x, 0), (x, HEIGHT)], fill=(7, 10, 19, alpha))

for y in range(HEIGHT):
    top_v = max(0.0, 1.0 - (y / 130.0))
    bot_v = max(0.0, (y - (HEIGHT - 150)) / 150.0)
    
    if top_v > 0:
        a = int(top_v * 60)
        draw_ol.line([(0, y), (WIDTH, y)], fill=(7, 10, 19, a))
    if bot_v > 0:
        a = int(bot_v * 85)
        draw_ol.line([(0, y), (WIDTH, y)], fill=(7, 10, 19, a))

canvas = Image.alpha_composite(bg_resized, overlay)

# 3. Fonts
font_bold_path = '/home/freshjinyong/.local/share/fonts/Pretendard-Bold.otf'
font_semibold_path = '/home/freshjinyong/.local/share/fonts/Pretendard-SemiBold.otf'

font_brand = ImageFont.truetype(font_bold_path, 21)
font_meta = ImageFont.truetype(font_semibold_path, 19)
font_title = ImageFont.truetype(font_bold_path, 42)
font_subtitle = ImageFont.truetype(font_semibold_path, 24)
font_badge_bold = ImageFont.truetype(font_bold_path, 22)
font_badge_sub = ImageFont.truetype(font_semibold_path, 22)
font_tag = ImageFont.truetype(font_semibold_path, 16)

txt_layer = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
draw = ImageDraw.Draw(txt_layer)

left_margin = 85

# --- [Header] ---
brand_y = 78
brand_text = "TECHCAPITAL LAB"
meta_text = "•  3. 증권사 리포트 읽기  •  2026-09-10"

draw.text((left_margin, brand_y), brand_text, font=font_brand, fill=(0, 229, 255, 255))
brand_w = font_brand.getbbox(brand_text)[2] - font_brand.getbbox(brand_text)[0]
draw.text((left_margin + brand_w + 22, brand_y + 1), meta_text, font=font_meta, fill=(148, 163, 184, 230))

bar_y = brand_y + 34
draw.line([(left_margin, bar_y), (left_margin + 52, bar_y)], fill=(0, 229, 255, 255), width=3)
draw.line([(left_margin + 52, bar_y), (left_margin + 180, bar_y)], fill=(0, 229, 255, 60), width=1)

# --- [Main Title] ---
title_text = "코스피 7천선 안착과 수급 충돌: 신한투자증권 마감 분석"
title_y = 195

for dx, dy, op in [(-2, -2, 40), (2, -2, 40), (-2, 2, 40), (2, 2, 40), (0, 3, 160), (0, 5, 120), (0, 8, 70)]:
    draw.text((left_margin + dx, title_y + dy), title_text, font=font_title, fill=(0, 0, 0, op))

draw.text((left_margin, title_y), title_text, font=font_title, fill=(255, 255, 255, 255))

# --- [Subtitle] ---
sub_text = "GPT-6 Astra AI 훈풍 vs 10년물 국채금리 4.8%·유가 100달러 돌파"
sub_y = 265

for dx, dy, op in [(0, 2, 120), (0, 4, 60)]:
    draw.text((left_margin + dx, sub_y + dy), sub_text, font=font_subtitle, fill=(0, 0, 0, op))

draw.text((left_margin, sub_y), sub_text, font=font_subtitle, fill=(203, 213, 225, 255))

# --- [Glass Data Cards] ---
card_w = 480
card_h = 56
card1_y = 350
card2_y = 422

def draw_glass_card(c_y, title, num, num_color):
    c_x = left_margin
    # Glass background
    draw.rounded_rectangle([c_x, c_y, c_x + card_w, c_y + card_h], radius=8, fill=(15, 23, 42, 180), outline=(255, 255, 255, 30), width=1)
    
    # Left accent bar
    draw.rectangle([c_x, c_y + 8, c_x + 4, c_y + card_h - 8], fill=(0, 229, 255, 255))
    
    # Text
    draw.text((c_x + 22, c_y + 16), title, font=font_badge_sub, fill=(226, 232, 240, 255))
    
    # Number right aligned
    num_w = font_badge_bold.getbbox(num)[2] - font_badge_bold.getbbox(num)[0]
    draw.text((c_x + card_w - num_w - 22, c_y + 16), num, font=font_badge_bold, fill=num_color)

draw_glass_card(card1_y, "KOSPI 종가 (7,000선 안착)", "7,033.92 pt", (56, 189, 248, 255))
draw_glass_card(card2_y, "美 10년물 국채금리 / 유가", "4.84% / WTI $96.7", (244, 63, 94, 255))

# --- [Tags] ---
tags_y = 525
tags = ["#신한투자증권", "#마감시황", "#코스피7000", "#GPT6_Astra", "#외환금리"]
cur_x = left_margin

for t in tags:
    tw = font_tag.getbbox(t)[2] - font_tag.getbbox(t)[0]
    draw.rounded_rectangle([cur_x, tags_y, cur_x + tw + 20, tags_y + 32], radius=6, fill=(30, 41, 59, 200), outline=(51, 65, 85, 220), width=1)
    draw.text((cur_x + 10, tags_y + 7), t, font=font_tag, fill=(148, 163, 184, 255))
    cur_x += tw + 30

# Combine and save
final_img = Image.alpha_composite(canvas, txt_layer)
os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
final_img.convert('RGB').save(OUTPUT_PATH, 'PNG')
print("Saved OG image to:", OUTPUT_PATH)
