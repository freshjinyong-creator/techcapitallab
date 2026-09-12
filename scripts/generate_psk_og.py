import os
import math
from PIL import Image, ImageDraw, ImageFont

# 1200x630 with 2x Super-Sampling for ultra-sharp typography & glass edges
WIDTH, HEIGHT = 1200, 630
SCALE = 2
W, H = WIDTH * SCALE, HEIGHT * SCALE

OUTPUT_PATH = '/home/freshjinyong/techcapitallab/src/assets/images/psk-holdings-3q26-preview-cowos-hbm-hana-report-20260911-og.png'
BG_PATH = '/home/freshjinyong/.gemini/antigravity-cli/brain/bf464f82-c19b-4249-979f-8e317efa6e7b/psk_cowos_reflow_1789192711490.jpg'

FONT_BOLD = '/home/freshjinyong/.local/share/fonts/Pretendard-Bold.otf'
FONT_SEMI = '/home/freshjinyong/.local/share/fonts/Pretendard-SemiBold.otf'

# 1. Load & Crop Photographic Background
bg = Image.open(BG_PATH).convert('RGBA')
bg_w, bg_h = bg.size
target_ratio = W / H
curr_ratio = bg_w / bg_h

if curr_ratio > target_ratio:
    new_w = int(bg_h * target_ratio)
    left = (bg_w - new_w) // 2
    bg_cropped = bg.crop((left, 0, left + new_w, bg_h))
else:
    new_h = int(bg_w / target_ratio)
    top = (bg_h - new_h) // 2
    bg_cropped = bg.crop((0, top, bg_w, top + new_h))

bg_resized = bg_cropped.resize((W, H), Image.Resampling.LANCZOS)

# 2. Dark Knight Editorial Gradient Overlay (#070A13 slate dark tone)
# Left 0% to 58%: 100% solid slate #070A13 (alpha 255) for crisp text contrast
# 58% to 88%: smooth cosine fade to alpha 45
# 88% to 100%: gentle tail fade to alpha 15
overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
draw_ol = ImageDraw.Draw(overlay)

for x in range(W):
    nx = x / W
    if nx <= 0.58:
        alpha = 255
    elif nx <= 0.88:
        t = (nx - 0.58) / (0.88 - 0.58)
        curve = 0.5 * (1.0 + math.cos(math.pi * t))
        alpha = int(45 + (255 - 45) * curve)
    else:
        t = (nx - 0.88) / (1.0 - 0.88)
        alpha = int(45 - t * 30)
    
    draw_ol.line([(x, 0), (x, H)], fill=(7, 10, 19, alpha))

# Top and bottom cinematic vignette
for y in range(H):
    top_v = max(0.0, 1.0 - (y / (140 * SCALE)))
    bot_v = max(0.0, (y - (H - 160 * SCALE)) / (160 * SCALE))
    if top_v > 0:
        a = int(top_v * 90)
        draw_ol.line([(0, y), (W, y)], fill=(7, 10, 19, a))
    if bot_v > 0:
        a = int(bot_v * 120)
        draw_ol.line([(0, y), (W, y)], fill=(7, 10, 19, a))

# Financial tech grid texture (left 65% area)
grid_overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
draw_grid = ImageDraw.Draw(grid_overlay)
for gx in range(0, int(W * 0.68), 48 * SCALE):
    draw_grid.line([(gx, 0), (gx, H)], fill=(30, 41, 59, 32), width=1)
for gy in range(0, H, 48 * SCALE):
    draw_grid.line([(0, gy), (int(W * 0.68), gy)], fill=(30, 41, 59, 32), width=1)

canvas = Image.alpha_composite(bg_resized, overlay)
canvas = Image.alpha_composite(canvas, grid_overlay)

# 3. Typography & UI Elements Layer
txt_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
draw = ImageDraw.Draw(txt_layer)

font_brand = ImageFont.truetype(FONT_BOLD, 22 * SCALE)
font_meta = ImageFont.truetype(FONT_SEMI, 16 * SCALE)
font_badge = ImageFont.truetype(FONT_BOLD, 14 * SCALE)
font_title1 = ImageFont.truetype(FONT_BOLD, 46 * SCALE)
font_title2 = ImageFont.truetype(FONT_BOLD, 37 * SCALE)
font_sub = ImageFont.truetype(FONT_SEMI, 19 * SCALE)
font_pill = ImageFont.truetype(FONT_SEMI, 15 * SCALE)
font_card_label = ImageFont.truetype(FONT_SEMI, 14 * SCALE)
font_card_val = ImageFont.truetype(FONT_BOLD, 31 * SCALE)
font_card_sub = ImageFont.truetype(FONT_SEMI, 14 * SCALE)
font_tag = ImageFont.truetype(FONT_SEMI, 14 * SCALE)

lx = 75 * SCALE

# --- [Header] ---
header_y = 46 * SCALE
brand_text = "TECHCAPITAL LAB"
meta_text = "•  하나증권 기업분석 리포트  •  2026.09.11"

draw.text((lx, header_y), brand_text, font=font_brand, fill=(0, 229, 255, 255))
brand_w = font_brand.getbbox(brand_text)[2] - font_brand.getbbox(brand_text)[0]
draw.text((lx + brand_w + 18 * SCALE, header_y + 4 * SCALE), meta_text, font=font_meta, fill=(148, 163, 184, 240))

# Header accent lines
bar_y = header_y + 32 * SCALE
draw.line([(lx, bar_y), (lx + 68 * SCALE, bar_y)], fill=(0, 229, 255, 255), width=3 * SCALE)
draw.line([(lx + 68 * SCALE, bar_y), (lx + 320 * SCALE, bar_y)], fill=(0, 229, 255, 50), width=1 * SCALE)

# --- [Badges above Title] ---
badg_y = 96 * SCALE
badg_pad_h = 24 * SCALE
badg_pad_w = 14 * SCALE

# Badge 1: Category
b1_text = "TechCapitalLab | Report Lab"
b1_w = font_badge.getbbox(b1_text)[2] - font_badge.getbbox(b1_text)[0]
draw.rounded_rectangle([lx, badg_y, lx + b1_w + badg_pad_w * 2, badg_y + badg_pad_h],
                       radius=4 * SCALE, fill=(15, 23, 42, 230), outline=(56, 189, 248, 130), width=1 * SCALE)
draw.text((lx + badg_pad_w, badg_y + 4 * SCALE), b1_text, font=font_badge, fill=(56, 189, 248, 255))

# Badge 2: Target Stock
b2_text = "피에스케이홀딩스 (031980)"
b2_w = font_badge.getbbox(b2_text)[2] - font_badge.getbbox(b2_text)[0]
b2_x = lx + b1_w + badg_pad_w * 2 + 12 * SCALE
draw.rounded_rectangle([b2_x, badg_y, b2_x + b2_w + badg_pad_w * 2, badg_y + badg_pad_h],
                       radius=4 * SCALE, fill=(6, 78, 59, 210), outline=(52, 211, 153, 150), width=1 * SCALE)
draw.text((b2_x + badg_pad_w, badg_y + 4 * SCALE), b2_text, font=font_badge, fill=(52, 211, 153, 255))

# Badge 3: Report Title Badge
b3_text = "하나증권 3Q26 Preview 《거침없는 실적》"
b3_w = font_badge.getbbox(b3_text)[2] - font_badge.getbbox(b3_text)[0]
b3_x = b2_x + b2_w + badg_pad_w * 2 + 12 * SCALE
draw.rounded_rectangle([b3_x, badg_y, b3_x + b3_w + badg_pad_w * 2, badg_y + badg_pad_h],
                       radius=4 * SCALE, fill=(30, 27, 75, 210), outline=(245, 158, 11, 140), width=1 * SCALE)
draw.text((b3_x + badg_pad_w, badg_y + 4 * SCALE), b3_text, font=font_badge, fill=(251, 191, 36, 255))

# --- [Main Titles] ---
title1_text = "피에스케이홀딩스, 거침없는 실적"
title2_text = "3Q26 영업익 +154% & CoWoS·HBM 슈퍼사이클"
title1_y = 138 * SCALE
title2_y = 196 * SCALE

# Drop shadow for title 1
for dx, dy, op in [(0, 3 * SCALE, 160), (0, 6 * SCALE, 100)]:
    draw.text((lx + dx, title1_y + dy), title1_text, font=font_title1, fill=(0, 0, 0, op))
draw.text((lx, title1_y), title1_text, font=font_title1, fill=(255, 255, 255, 255))

# Drop shadow for title 2 (Cyan point color)
for dx, dy, op in [(0, 3 * SCALE, 160), (0, 6 * SCALE, 100)]:
    draw.text((lx + dx, title2_y + dy), title2_text, font=font_title2, fill=(0, 0, 0, op))
draw.text((lx, title2_y), title2_text, font=font_title2, fill=(0, 229, 255, 255))

# --- [Subtitle] ---
sub_y = 258 * SCALE
sub_text = "하나증권 목표가 220,000원 상향 • 리플로우 & 디스컴 독점력과 2027 실적 퀀텀점프"
for dx, dy, op in [(0, 2 * SCALE, 120)]:
    draw.text((lx + dx, sub_y + dy), sub_text, font=font_sub, fill=(0, 0, 0, op))
draw.text((lx, sub_y), sub_text, font=font_sub, fill=(203, 213, 225, 255))

# Divider Line
sep_y = 300 * SCALE
draw.line([(lx, sep_y), (lx + 690 * SCALE, sep_y)], fill=(51, 65, 85, 180), width=1 * SCALE)

# --- [Focus Highlight Pills] ---
pills = [
    ("하나증권 목표주가 220,000원", (52, 211, 153)),   # Emerald Green
    ("3Q26 OPM 42.4% 사상 최대", (56, 189, 248)),     # Cyan Blue
    ("TSMC · ASE CoWoS CAPA 2배", (251, 191, 36))     # Amber Gold
]

pill_x = lx
pill_y = 318 * SCALE

for text, color in pills:
    p_bbox = font_pill.getbbox(text)
    pw = (p_bbox[2] - p_bbox[0]) + 26 * SCALE
    ph = 30 * SCALE
    
    draw.rounded_rectangle([pill_x, pill_y, pill_x + pw, pill_y + ph],
                           radius=6 * SCALE, fill=(15, 23, 42, 230), outline=(*color, 130), width=1 * SCALE)
    draw.ellipse([pill_x + 9 * SCALE, pill_y + 11 * SCALE, pill_x + 15 * SCALE, pill_y + 17 * SCALE], fill=(*color, 255))
    draw.text((pill_x + 21 * SCALE, pill_y + 5 * SCALE), text, font=font_pill, fill=(241, 245, 249, 250))
    pill_x += pw + 12 * SCALE

# --- [Glassmorphism Data Cards (2 Cards Side by Side)] ---
card_y = 368 * SCALE
card_h = 175 * SCALE
card_w = 345 * SCALE
card_gap = 20 * SCALE

cards = [
    {
        "label": "3Q26 영업이익 퀀텀점프",
        "value": "350억 원",
        "badge": "QoQ +153.6% (OPM 42.4%)",
        "badge_color": (56, 189, 248),   # Cyan Blue
        "sub": "3Q 매출 825억 (+74%) · 4Q 1,144억 전망",
        "accent": (0, 229, 255),         # Electric Cyan
        "val_color": (56, 189, 248)
    },
    {
        "label": "하나증권 목표주가 상향",
        "value": "220,000원",
        "badge": "상승여력 +41.9% (BUY 유지)",
        "badge_color": (52, 211, 153),  # Emerald Green
        "sub": "2027F 영업익 1,504억 (+47.3%) 독점력",
        "accent": (16, 185, 129),        # Emerald Green
        "val_color": (52, 211, 153)
    }
]

for i, card in enumerate(cards):
    cx = lx + i * (card_w + card_gap)
    
    # Glassmorphism Card Box with glowing edge & semi-transparent backdrop
    draw.rounded_rectangle([cx, card_y, cx + card_w, card_y + card_h],
                           radius=12 * SCALE, fill=(13, 19, 33, 240), outline=(51, 65, 85, 240), width=2 * SCALE)
    
    # Top edge subtle light glow
    draw.line([(cx + 12 * SCALE, card_y), (cx + card_w - 12 * SCALE, card_y)],
              fill=(255, 255, 255, 40), width=1 * SCALE)
    
    # Left vertical accent bar
    draw.rounded_rectangle([cx + 12 * SCALE, card_y + 16 * SCALE, cx + 17 * SCALE, card_y + card_h - 16 * SCALE],
                           radius=3 * SCALE, fill=(*card["accent"], 255))
    
    # Card Header Label
    draw.text((cx + 28 * SCALE, card_y + 16 * SCALE), card["label"], font=font_card_label, fill=(148, 163, 184, 255))
    
    # Card Big Value
    draw.text((cx + 28 * SCALE, card_y + 44 * SCALE), card["value"], font=font_card_val, fill=(*card["val_color"], 255))
    
    # Badge Box
    b_text = card["badge"]
    b_bbox = font_badge.getbbox(b_text)
    bw = b_bbox[2] - b_bbox[0] + 16 * SCALE
    bh = 22 * SCALE
    bx = cx + 28 * SCALE
    by = card_y + 92 * SCALE
    draw.rounded_rectangle([bx, by, bx + bw, by + bh],
                           radius=4 * SCALE, fill=(30, 41, 59, 220), outline=(*card["badge_color"], 140), width=1 * SCALE)
    draw.text((bx + 8 * SCALE, by + 2 * SCALE), b_text, font=font_badge, fill=(*card["badge_color"], 255))
    
    # Card Subtext
    draw.text((cx + 28 * SCALE, card_y + 130 * SCALE), card["sub"], font=font_card_sub, fill=(203, 213, 225, 230))

# --- [Tags at bottom] ---
tags_y = 562 * SCALE
tags = ["#피에스케이홀딩스", "#하나증권", "#HBM4", "#CoWoS증설", "#리플로우", "#디스컴"]
cur_x = lx

for t in tags:
    tw = font_tag.getbbox(t)[2] - font_tag.getbbox(t)[0]
    draw.rounded_rectangle([cur_x, tags_y, cur_x + tw + 18 * SCALE, tags_y + 26 * SCALE],
                           radius=5 * SCALE, fill=(30, 41, 59, 210), outline=(51, 65, 85, 220), width=1 * SCALE)
    draw.text((cur_x + 9 * SCALE, tags_y + 4 * SCALE), t, font=font_tag, fill=(148, 163, 184, 255))
    cur_x += tw + 28 * SCALE

# 4. Composite and Lanczos Downsample to 1200x630
final_canvas = Image.alpha_composite(canvas, txt_layer)
os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
final_img = final_canvas.convert("RGB").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
final_img.save(OUTPUT_PATH, format="PNG", optimize=True)
print(f"OG Image successfully created and saved at: {OUTPUT_PATH}")
