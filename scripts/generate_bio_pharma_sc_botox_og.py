import os
import math
from PIL import Image, ImageDraw, ImageFont

# 1200x630 with 2x Super-Sampling (2400x1260) for ultra-sharp typography & glass edges
WIDTH, HEIGHT = 1200, 630
SCALE = 2
W, H = WIDTH * SCALE, HEIGHT * SCALE

OUTPUT_PATH = '/home/freshjinyong/techcapitallab/src/assets/images/bigpharma-sc-transition-botox-us-direct-sales-alteogen-stpharm-hugel-2026-og.png'
BG_PATH = '/home/freshjinyong/.gemini/antigravity-cli/brain/cf4f07ba-1f57-4d73-87c8-15d234651475/bio_pharma_cover_1789992149544.jpg'

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

# 2. Dark Editorial Gradient Overlay (#060B14 deep dark obsidian navy)
# Left 0% to 54%: solid slate #060B14 (alpha 255) for pristine text legibility
# 54% to 86%: smooth cosine fade to alpha 40
# 86% to 100%: gentle reveal of the high-tech biomanufacturing facility
overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
draw_ol = ImageDraw.Draw(overlay)

for x in range(W):
    nx = x / W
    if nx <= 0.52:
        alpha = 255
    elif nx <= 0.85:
        t = (nx - 0.52) / (0.85 - 0.52)
        curve = 0.5 * (1.0 + math.cos(math.pi * t))
        alpha = int(40 + (255 - 40) * curve)
    else:
        t = (nx - 0.85) / (1.0 - 0.85)
        alpha = int(40 - t * 20)
    
    draw_ol.line([(x, 0), (x, H)], fill=(6, 11, 20, alpha))

# Top and bottom cinematic vignette
for y in range(H):
    top_v = max(0.0, 1.0 - (y / (130 * SCALE)))
    bot_v = max(0.0, (y - (H - 140 * SCALE)) / (140 * SCALE))
    if top_v > 0:
        a = int(top_v * 110)
        draw_ol.line([(0, y), (W, y)], fill=(6, 11, 20, a))
    if bot_v > 0:
        a = int(bot_v * 140)
        draw_ol.line([(0, y), (W, y)], fill=(6, 11, 20, a))

# Financial tech grid texture (left 65% area)
grid_overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
draw_grid = ImageDraw.Draw(grid_overlay)
for gx in range(0, int(W * 0.65), 48 * SCALE):
    draw_grid.line([(gx, 0), (gx, H)], fill=(30, 41, 59, 32), width=1)
for gy in range(0, H, 48 * SCALE):
    draw_grid.line([(0, gy), (int(W * 0.65), gy)], fill=(30, 41, 59, 32), width=1)

canvas = Image.alpha_composite(bg_resized, overlay)
canvas = Image.alpha_composite(canvas, grid_overlay)

# 3. Typography & UI Elements Layer
txt_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
draw = ImageDraw.Draw(txt_layer)

font_brand = ImageFont.truetype(FONT_BOLD, 22 * SCALE)
font_meta = ImageFont.truetype(FONT_SEMI, 16 * SCALE)
font_badge = ImageFont.truetype(FONT_BOLD, 13 * SCALE)
font_title1 = ImageFont.truetype(FONT_BOLD, 44 * SCALE)
font_title2 = ImageFont.truetype(FONT_BOLD, 38 * SCALE)
font_sub = ImageFont.truetype(FONT_SEMI, 18 * SCALE)
font_card_label = ImageFont.truetype(FONT_SEMI, 14 * SCALE)
font_card_val = ImageFont.truetype(FONT_BOLD, 26 * SCALE)
font_card_badge = ImageFont.truetype(FONT_BOLD, 12 * SCALE)
font_card_sub = ImageFont.truetype(FONT_SEMI, 13 * SCALE)
font_tag = ImageFont.truetype(FONT_SEMI, 13 * SCALE)

lx = 75 * SCALE

def draw_shadowed_text(draw_ctx, pos, text, font, fill_color, shadow_offsets=[(0, 2*SCALE, 160), (0, 5*SCALE, 90)]):
    x, y = pos
    for dx, dy, op in shadow_offsets:
        draw_ctx.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, op))
    draw_ctx.text((x, y), text, font=font, fill=fill_color)

# --- [Header: TECH CAPITAL LAB | 바이오 제약 글로벌 전략 분석] ---
top_y = 48 * SCALE
brand_text = "Tech Capital Lab"
draw_shadowed_text(draw, (lx, top_y), brand_text, font=font_brand, fill_color=(0, 229, 255, 255))
brand_w = font_brand.getbbox(brand_text)[2] - font_brand.getbbox(brand_text)[0]

sep_text = "|"
draw.text((lx + brand_w + 14 * SCALE, top_y + 2 * SCALE), sep_text, font=font_meta, fill=(100, 116, 139, 200))
sep_w = font_meta.getbbox(sep_text)[2] - font_meta.getbbox(sep_text)[0]

meta_text = "글로벌 바이오 전략 & 2027 실적 전망 심층 분석"
meta_x = lx + brand_w + 14 * SCALE + sep_w + 14 * SCALE
draw_shadowed_text(draw, (meta_x, top_y + 3 * SCALE), meta_text, font=font_meta, fill_color=(148, 163, 184, 255))

# Header underline accent
bar_y = top_y + 34 * SCALE
draw.line([(lx, bar_y), (lx + 68 * SCALE, bar_y)], fill=(0, 229, 255, 255), width=3 * SCALE)
draw.line([(lx + 68 * SCALE, bar_y), (lx + 340 * SCALE, bar_y)], fill=(0, 229, 255, 45), width=1 * SCALE)

# --- [Badges above Title] ---
badg_y = 100 * SCALE
badg_pad_h = 24 * SCALE
badg_pad_w = 12 * SCALE

# Badge 1: SC 제형 패러다임 시프트 (Cyan)
b1_text = "빅파마 SC 제형 전환 가속화"
b1_w = font_badge.getbbox(b1_text)[2] - font_badge.getbbox(b1_text)[0]
draw.rounded_rectangle([lx, badg_y, lx + b1_w + badg_pad_w * 2, badg_y + badg_pad_h],
                       radius=4 * SCALE, fill=(15, 23, 42, 220), outline=(56, 189, 248, 150), width=1 * SCALE)
draw.text((lx + badg_pad_w, badg_y + 4 * SCALE), b1_text, font=font_badge, fill=(56, 189, 248, 255))

# Badge 2: 미국 직판 글로벌 침투 (Amber Gold)
b2_text = "미국 보톡스 직판 및 CDMO 확장"
b2_w = font_badge.getbbox(b2_text)[2] - font_badge.getbbox(b2_text)[0]
b2_x = lx + b1_w + badg_pad_w * 2 + 12 * SCALE
draw.rounded_rectangle([b2_x, badg_y, b2_x + b2_w + badg_pad_w * 2, badg_y + badg_pad_h],
                       radius=4 * SCALE, fill=(30, 27, 75, 220), outline=(251, 191, 36, 150), width=1 * SCALE)
draw.text((b2_x + badg_pad_w, badg_y + 4 * SCALE), b2_text, font=font_badge, fill=(251, 191, 36, 255))

# --- [Main Titles: Editorial Headline] ---
title1_y = 142 * SCALE
title2_y = 198 * SCALE

# Line 1: SC Transition & Botox US Direct Sales
title1_text = "SC Transition & Botox US Direct Sales"
draw_shadowed_text(draw, (lx, title1_y), title1_text, font=font_title1, fill_color=(255, 255, 255, 255),
                   shadow_offsets=[(0, 2*SCALE, 190), (0, 6*SCALE, 130)])

# Line 2: 빅파마 SC 제형 전환과 보톡스 미국 직판 (한국어 및 주요 키워드 강조)
t2_main = "빅파마 SC 변환과 미국 직판: "
t2_sub = "2027 실적 가시성"

draw_shadowed_text(draw, (lx, title2_y), t2_main, font=font_title2, fill_color=(241, 245, 249, 255),
                   shadow_offsets=[(0, 2*SCALE, 190), (0, 6*SCALE, 130)])
main_w = font_title2.getbbox(t2_main)[2] - font_title2.getbbox(t2_main)[0]

draw_shadowed_text(draw, (lx + main_w, title2_y), t2_sub, font=font_title2, fill_color=(0, 229, 255, 255),
                   shadow_offsets=[(0, 2*SCALE, 190), (0, 6*SCALE, 130)])

# --- [Subtitle: Alteogen, ST Pharm, Hugel Analysis] ---
sub_y = 258 * SCALE
sub_text = "Alteogen, ST Pharm, Hugel Analysis  |  알테오젠 · 에스티팜 · 휴젤 파이프라인 분석"
draw_shadowed_text(draw, (lx, sub_y), sub_text, font=font_sub, fill_color=(203, 213, 225, 255),
                   shadow_offsets=[(0, 2*SCALE, 140)])

# Editorial Divider Line
sep_y = 300 * SCALE
draw.line([(lx, sep_y), (lx + 690 * SCALE, sep_y)], fill=(51, 65, 85, 180), width=1 * SCALE)
draw.line([(lx, sep_y), (lx + 130 * SCALE, sep_y)], fill=(0, 229, 255, 220), width=2 * SCALE)

# --- [Glassmorphic Data Cards: 2 High-Impact Cards] ---
card_y = 322 * SCALE
card_h = 180 * SCALE
card_w = 338 * SCALE
card_gap = 18 * SCALE

cards = [
    {
        "category": "빅파마 SC 제형 전환 (ALT-B4)",
        "value": "2027 매출 1.2조원+",
        "val_color": (0, 229, 255),         # Electric Cyan
        "accent": (0, 229, 255),
        "badge": "키트루다 SC 로열티 본격화",
        "badge_color": (56, 189, 248),
        "badge_bg": (15, 23, 42, 220),
        "sub": "알테오젠 인간 히알루로니다제 독점 수혜"
    },
    {
        "category": "보톡스 미국 직판 및 CDMO 체결",
        "value": "영업이익률 45% 돌파",
        "val_color": (251, 191, 36),       # Amber Gold
        "accent": (245, 158, 11),
        "badge": "휴젤 레티보 미국 직판 & 올리고 가동",
        "badge_color": (251, 191, 36),
        "badge_bg": (30, 27, 75, 220),
        "sub": "유통 마진 내재화 및 에스티팜 2동 상업화"
    }
]

for i, c in enumerate(cards):
    cx = lx + i * (card_w + card_gap)
    
    # Glassmorphic Box with dual-tone glowing border
    draw.rounded_rectangle([cx, card_y, cx + card_w, card_y + card_h],
                           radius=10 * SCALE, fill=(13, 19, 33, 235), outline=(51, 65, 85, 230), width=1 * SCALE)
    
    # Top edge subtle light glow line
    draw.line([(cx + 10 * SCALE, card_y), (cx + card_w - 10 * SCALE, card_y)],
              fill=(255, 255, 255, 35), width=1 * SCALE)
    
    # Left vertical accent bar
    draw.rounded_rectangle([cx + 10 * SCALE, card_y + 14 * SCALE, cx + 14 * SCALE, card_y + card_h - 14 * SCALE],
                           radius=2 * SCALE, fill=(*c["accent"], 255))
    
    text_indent = cx + 24 * SCALE
    
    # Category Label
    draw.text((text_indent, card_y + 16 * SCALE), c["category"], font=font_card_label, fill=(148, 163, 184, 255))
    
    # Main Value (High impact)
    draw_shadowed_text(draw, (text_indent, card_y + 44 * SCALE), c["value"], font=font_card_val, fill_color=(*c["val_color"], 255),
                       shadow_offsets=[(0, 2*SCALE, 140)])
    
    # Badge Box
    b_text = c["badge"]
    b_bbox = font_card_badge.getbbox(b_text)
    bw = b_bbox[2] - b_bbox[0] + 14 * SCALE
    bh = 22 * SCALE
    bx = text_indent
    by = card_y + 94 * SCALE
    draw.rounded_rectangle([bx, by, bx + bw, by + bh],
                           radius=4 * SCALE, fill=c["badge_bg"], outline=(*c["badge_color"], 140), width=1 * SCALE)
    draw.text((bx + 7 * SCALE, by + 3 * SCALE), b_text, font=font_card_badge, fill=(*c["badge_color"], 255))
    
    # Subtext
    draw.text((text_indent, card_y + 134 * SCALE), c["sub"], font=font_card_sub, fill=(203, 213, 225, 210))

# --- [Tags at bottom] ---
tags_y = 542 * SCALE
tags = ["#알테오젠", "#에스티팜", "#휴젤", "#SC제형변환", "#보톡스미국직판", "#2027실적전망", "#TechCapitalLab"]
cur_x = lx

for t in tags:
    tw = font_tag.getbbox(t)[2] - font_tag.getbbox(t)[0]
    draw.rounded_rectangle([cur_x, tags_y, cur_x + tw + 16 * SCALE, tags_y + 24 * SCALE],
                           radius=4 * SCALE, fill=(15, 23, 42, 220), outline=(51, 65, 85, 210), width=1 * SCALE)
    draw.text((cur_x + 8 * SCALE, tags_y + 3 * SCALE), t, font=font_tag, fill=(148, 163, 184, 255))
    cur_x += tw + 14 * SCALE

# 4. Composite and Lanczos Downsample to exactly 1200x630
final_canvas = Image.alpha_composite(canvas, txt_layer)
os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
final_img = final_canvas.convert("RGB").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
final_img.save(OUTPUT_PATH, format="PNG", optimize=True)
print(f"OG Image successfully generated and saved at: {OUTPUT_PATH}")
