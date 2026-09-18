import os
import math
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
SCALE = 2
W, H = WIDTH * SCALE, HEIGHT * SCALE

OUTPUT_PATH = '/home/freshjinyong/techcapitallab/src/assets/images/kiwoom-etf-pension-strategy-irp-isa-guide-og.png'
BG_PATH = '/home/freshjinyong/.gemini/antigravity-cli/brain/a4f7aa15-d8fa-4006-967f-52ae9c32fc54/pension_etf_bg_1789611036491.jpg'

FONT_BOLD = '/home/freshjinyong/.local/share/fonts/Pretendard-Bold.otf'
FONT_SEMI = '/home/freshjinyong/.local/share/fonts/Pretendard-SemiBold.otf'

# 1. Load and Crop Background
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

# 2. Slate Dark Editorial Gradient Overlay (#070B16 deep slate navy)
overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
draw_ol = ImageDraw.Draw(overlay)

# Left 0% to 55%: solid slate #070B16 (alpha 255) for crisp typography & card contrast
# 55% to 88%: smooth cosine fade to alpha 45
# 88% to 100%: gentle tail fade to highlight the glowing compounding charts on right
for x in range(W):
    nx = x / W
    if nx <= 0.53:
        alpha = 255
    elif nx <= 0.86:
        t = (nx - 0.53) / (0.86 - 0.53)
        curve = 0.5 * (1.0 + math.cos(math.pi * t))
        alpha = int(45 + (255 - 45) * curve)
    else:
        t = (nx - 0.86) / (1.0 - 0.86)
        alpha = int(45 - t * 25)
    
    draw_ol.line([(x, 0), (x, H)], fill=(7, 11, 22, alpha))

# Top & bottom cinematic vignettes
for y in range(H):
    top_v = max(0.0, 1.0 - (y / (130 * SCALE)))
    bot_v = max(0.0, (y - (H - 140 * SCALE)) / (140 * SCALE))
    if top_v > 0:
        a = int(top_v * 110)
        draw_ol.line([(0, y), (W, y)], fill=(7, 11, 22, a))
    if bot_v > 0:
        a = int(bot_v * 130)
        draw_ol.line([(0, y), (W, y)], fill=(7, 11, 22, a))

# Financial grid texture (left 65%)
grid_overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
draw_grid = ImageDraw.Draw(grid_overlay)
for gx in range(0, int(W * 0.65), 48 * SCALE):
    draw_grid.line([(gx, 0), (gx, H)], fill=(30, 41, 59, 28), width=1)
for gy in range(0, H, 48 * SCALE):
    draw_grid.line([(0, gy), (int(W * 0.65), gy)], fill=(30, 41, 59, 28), width=1)

canvas = Image.alpha_composite(bg_resized, overlay)
canvas = Image.alpha_composite(canvas, grid_overlay)

# 3. Typography and Cards
txt_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
draw = ImageDraw.Draw(txt_layer)

font_brand = ImageFont.truetype(FONT_BOLD, 20 * SCALE)
font_top_meta = ImageFont.truetype(FONT_SEMI, 16 * SCALE)
font_badge = ImageFont.truetype(FONT_BOLD, 13 * SCALE)

font_title1 = ImageFont.truetype(FONT_BOLD, 41 * SCALE)
font_title2 = ImageFont.truetype(FONT_BOLD, 39 * SCALE)
font_sub = ImageFont.truetype(FONT_SEMI, 20 * SCALE)

font_card_label = ImageFont.truetype(FONT_SEMI, 13 * SCALE)
font_card_val = ImageFont.truetype(FONT_BOLD, 22 * SCALE)
font_card_badge = ImageFont.truetype(FONT_BOLD, 11 * SCALE)
font_card_sub = ImageFont.truetype(FONT_SEMI, 12 * SCALE)

font_tag = ImageFont.truetype(FONT_SEMI, 13 * SCALE)

lx = 72 * SCALE

def draw_shadowed_text(draw_ctx, pos, text, font, fill_color, shadow_offsets=[(0, 3*SCALE, 160), (0, 6*SCALE, 90)]):
    x, y = pos
    for dx, dy, op in shadow_offsets:
        draw_ctx.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, op))
    draw_ctx.text((x, y), text, font=font, fill=fill_color)

# --- [Header: TECH CAPITAL LAB | 3. 증권사 리포트 읽기 • 키움증권 인뎁스] ---
top_y = 52 * SCALE

brand_text = "TECH CAPITAL LAB"
brand_w = font_brand.getbbox(brand_text)[2] - font_brand.getbbox(brand_text)[0]
draw_shadowed_text(draw, (lx, top_y), brand_text, font=font_brand, fill_color=(0, 229, 255, 255))

sep_text = "|"
draw.text((lx + brand_w + 14 * SCALE, top_y + 1 * SCALE), sep_text, font=font_top_meta, fill=(100, 116, 139, 200))
sep_w = font_top_meta.getbbox(sep_text)[2] - font_top_meta.getbbox(sep_text)[0]

meta_text = "3. 증권사 리포트 읽기  •  키움증권 인뎁스 분석"
meta_x = lx + brand_w + 14 * SCALE + sep_w + 14 * SCALE
draw_shadowed_text(draw, (meta_x, top_y + 1 * SCALE), meta_text, font=font_top_meta, fill_color=(148, 163, 184, 255))

# Underline accent
bar_y = top_y + 30 * SCALE
draw.line([(lx, bar_y), (lx + 60 * SCALE, bar_y)], fill=(0, 229, 255, 255), width=3 * SCALE)
draw.line([(lx + 60 * SCALE, bar_y), (lx + 340 * SCALE, bar_y)], fill=(0, 229, 255, 45), width=1 * SCALE)

# --- [Badge Line: 퇴직연금 500조 시대 • 상하위 40배 수익률 격차] ---
badg_y = 104 * SCALE
badg_pad_h = 24 * SCALE
badg_pad_w = 12 * SCALE

# Badge 1: 퇴직연금 500조 돌파 (Cyan / Navy)
b1_text = "퇴직연금 500조 돌파"
b1_w = font_badge.getbbox(b1_text)[2] - font_badge.getbbox(b1_text)[0]
draw.rounded_rectangle([lx, badg_y, lx + b1_w + badg_pad_w * 2, badg_y + badg_pad_h],
                       radius=4 * SCALE, fill=(8, 47, 73, 210), outline=(56, 189, 248, 150), width=1 * SCALE)
draw.text((lx + badg_pad_w, badg_y + 4 * SCALE), b1_text, font=font_badge, fill=(56, 189, 248, 255))

# Badge 2: 성과 양극화 40배 격차 (Emerald Green)
b2_text = "실적배당 vs 원리금 40배 격차"
b2_w = font_badge.getbbox(b2_text)[2] - font_badge.getbbox(b2_text)[0]
b2_x = lx + b1_w + badg_pad_w * 2 + 10 * SCALE
draw.rounded_rectangle([b2_x, badg_y, b2_x + b2_w + badg_pad_w * 2, badg_y + badg_pad_h],
                       radius=4 * SCALE, fill=(6, 78, 59, 210), outline=(52, 211, 153, 140), width=1 * SCALE)
draw.text((b2_x + badg_pad_w, badg_y + 4 * SCALE), b2_text, font=font_badge, fill=(52, 211, 153, 255))

# --- [Main Headline] ---
title1_y = 146 * SCALE
title1_text = "퇴직연금 500조, 왜 내 수익률은 0%일까?"
draw_shadowed_text(draw, (lx, title1_y), title1_text, font=font_title1, fill_color=(255, 255, 255, 255),
                   shadow_offsets=[(0, 2*SCALE, 190), (0, 6*SCALE, 140), (0, 12*SCALE, 80)])

title2_y = 200 * SCALE
# Highlight "ETF 평생 연금" in Electric Gold / Amber
title2_prefix = "키움증권 "
title2_highlight = "ETF 평생 연금 전략"
title2_suffix = "과 3대 절세계좌"

cur_tx = lx
draw_shadowed_text(draw, (cur_tx, title2_y), title2_prefix, font=font_title2, fill_color=(255, 255, 255, 255),
                   shadow_offsets=[(0, 2*SCALE, 190), (0, 6*SCALE, 130)])
cur_tx += font_title2.getbbox(title2_prefix)[2] - font_title2.getbbox(title2_prefix)[0]

draw_shadowed_text(draw, (cur_tx, title2_y), title2_highlight, font=font_title2, fill_color=(251, 191, 36, 255),
                   shadow_offsets=[(0, 2*SCALE, 190), (0, 6*SCALE, 130)])
cur_tx += font_title2.getbbox(title2_highlight)[2] - font_title2.getbbox(title2_highlight)[0]

draw_shadowed_text(draw, (cur_tx, title2_y), title2_suffix, font=font_title2, fill_color=(255, 255, 255, 255),
                   shadow_offsets=[(0, 2*SCALE, 190), (0, 6*SCALE, 130)])

# --- [Subtitle] ---
sub_y = 254 * SCALE
sub_text = "연금저축 · IRP · ISA 입체 결합 및 월배당 ETF 생애주기 포트폴리오 완벽 가이드"
draw_shadowed_text(draw, (lx, sub_y), sub_text, font=font_sub, fill_color=(203, 213, 225, 255),
                   shadow_offsets=[(0, 2*SCALE, 140)])

# Editorial Divider Line
sep_y = 296 * SCALE
draw.line([(lx, sep_y), (lx + 710 * SCALE, sep_y)], fill=(51, 65, 85, 180), width=1 * SCALE)
draw.line([(lx, sep_y), (lx + 130 * SCALE, sep_y)], fill=(251, 191, 36, 220), width=2 * SCALE)

# --- [Glassmorphic Data Overlay Cards: 3 Cards Side-by-Side] ---
card_y = 318 * SCALE
card_h = 176 * SCALE
card_w = 228 * SCALE
card_gap = 14 * SCALE

cards = [
    {
        "category": "수익률 양극화",
        "value": "상위 19.5% vs 0.5%",
        "val_color": (52, 211, 153),       # Emerald Green
        "accent": (16, 185, 129),
        "badge": "실적배당 40배 격차",
        "badge_color": (52, 211, 153),
        "badge_bg": (6, 78, 59, 220),
        "sub": "원리금 방치시 인플레 손실"
    },
    {
        "category": "3대 절세계좌",
        "value": "최대 1,200만 원",
        "val_color": (0, 229, 255),         # Cyan
        "accent": (0, 229, 255),
        "badge": "세액공제 · 과세이연",
        "badge_color": (56, 189, 248),
        "badge_bg": (8, 47, 73, 220),
        "sub": "연금저축+IRP+ISA 환급"
    },
    {
        "category": "월배당 인출 설계",
        "value": "월 2회 배당 캘린더",
        "val_color": (251, 191, 36),       # Amber Gold
        "accent": (245, 158, 11),
        "badge": "현금흐름 + 버퍼 구축",
        "badge_color": (251, 191, 36),
        "badge_bg": (30, 27, 75, 220),
        "sub": "원금 훼손 없는 현금 창출"
    }
]

for i, c in enumerate(cards):
    cx = lx + i * (card_w + card_gap)
    
    # Glassmorphic Box with dual-tone glowing border
    draw.rounded_rectangle([cx, card_y, cx + card_w, card_y + card_h],
                           radius=10 * SCALE, fill=(13, 19, 33, 235), outline=(51, 65, 85, 230), width=1 * SCALE)
    
    # Top subtle edge glow
    draw.line([(cx + 10 * SCALE, card_y), (cx + card_w - 10 * SCALE, card_y)],
              fill=(255, 255, 255, 35), width=1 * SCALE)
    
    # Left vertical accent bar
    draw.rounded_rectangle([cx + 10 * SCALE, card_y + 14 * SCALE, cx + 14 * SCALE, card_y + card_h - 14 * SCALE],
                           radius=2 * SCALE, fill=(*c["accent"], 255))
    
    text_indent = cx + 24 * SCALE
    
    # Category Label
    draw.text((text_indent, card_y + 16 * SCALE), c["category"], font=font_card_label, fill=(148, 163, 184, 255))
    
    # Main Value
    draw_shadowed_text(draw, (text_indent, card_y + 40 * SCALE), c["value"], font=font_card_val, fill_color=(*c["val_color"], 255),
                       shadow_offsets=[(0, 2*SCALE, 140)])
    
    # Badge Box
    b_text = c["badge"]
    b_bbox = font_card_badge.getbbox(b_text)
    bw = b_bbox[2] - b_bbox[0] + 14 * SCALE
    bh = 22 * SCALE
    bx = text_indent
    by = card_y + 88 * SCALE
    draw.rounded_rectangle([bx, by, bx + bw, by + bh],
                           radius=4 * SCALE, fill=c["badge_bg"], outline=(*c["badge_color"], 140), width=1 * SCALE)
    draw.text((bx + 7 * SCALE, by + 3 * SCALE), b_text, font=font_card_badge, fill=(*c["badge_color"], 255))
    
    # Subtext
    draw.text((text_indent, card_y + 128 * SCALE), c["sub"], font=font_card_sub, fill=(203, 213, 225, 210))

# --- [Tags at bottom] ---
tags_y = 538 * SCALE
tags = ["#퇴직연금", "#IRP", "#연금저축", "#중개형ISA", "#월배당ETF", "#키움증권", "#TechCapitalLab"]
cur_x = lx

for t in tags:
    tw = font_tag.getbbox(t)[2] - font_tag.getbbox(t)[0]
    draw.rounded_rectangle([cur_x, tags_y, cur_x + tw + 16 * SCALE, tags_y + 24 * SCALE],
                           radius=4 * SCALE, fill=(15, 23, 42, 220), outline=(51, 65, 85, 210), width=1 * SCALE)
    draw.text((cur_x + 8 * SCALE, tags_y + 3 * SCALE), t, font=font_tag, fill=(148, 163, 184, 255))
    cur_x += tw + 12 * SCALE

# 4. Composite and Lanczos Downsample to exactly 1200x630
final_canvas = Image.alpha_composite(canvas, txt_layer)
os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
final_img = final_canvas.convert("RGB").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
final_img.save(OUTPUT_PATH, format="PNG", optimize=True)
print(f"OG Image successfully generated and saved at: {OUTPUT_PATH}")
