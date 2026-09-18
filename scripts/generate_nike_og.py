import os
import math
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
SCALE = 2
W, H = WIDTH * SCALE, HEIGHT * SCALE

OUTPUT_PATH = '/home/freshjinyong/techcapitallab/src/assets/images/nike-stock-crash-dtc-failure-hoka-on-running-20260914-og.png'
BG_PATH = '/home/freshjinyong/.gemini/antigravity-cli/brain/f4a1904b-1242-474d-9526-fb6b2407d6f7/nike_crash_editorial_bg_1789391960877.jpg'

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

# 2. Slate Dark Editorial Gradient Overlay (#070B16)
overlay = Image.new('RGBA', (W, H), (7, 11, 22, 0))

# Horizontal gradient ensuring left 52% is dark & legible for typography
for x in range(W):
    nx = x / W
    if nx <= 0.50:
        alpha = 255
    elif nx <= 0.78:
        t = (nx - 0.50) / (0.78 - 0.50)
        curve = 0.5 * (1.0 + math.cos(math.pi * t))
        alpha = int(40 + (255 - 40) * curve)
    else:
        t = (nx - 0.78) / (1.0 - 0.78)
        alpha = int(40 - t * 25)
    
    for y in range(H):
        cur_a = alpha
        if nx > 0.50:
            top_v = max(0.0, 1.0 - (y / (110 * SCALE)))
            bot_v = max(0.0, (y - (H - 110 * SCALE)) / (110 * SCALE))
            add_a = int(top_v * 110 + bot_v * 130)
            cur_a = min(255, cur_a + add_a)
        
        overlay.putpixel((x, y), (7, 11, 22, cur_a))

# Financial tech grid texture (left 52%)
grid_overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
draw_grid = ImageDraw.Draw(grid_overlay)
for gx in range(0, int(W * 0.52), 48 * SCALE):
    draw_grid.line([(gx, 0), (gx, H)], fill=(30, 41, 59, 25), width=1)
for gy in range(0, H, 48 * SCALE):
    draw_grid.line([(0, gy), (int(W * 0.52), gy)], fill=(30, 41, 59, 25), width=1)

canvas = Image.alpha_composite(bg_resized, overlay)
canvas = Image.alpha_composite(canvas, grid_overlay)

# 3. Typography Overlay (No-box Drop Shadow, Pretendard)
txt_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
draw = ImageDraw.Draw(txt_layer)

font_brand = ImageFont.truetype(FONT_BOLD, 20 * SCALE)
font_top_meta = ImageFont.truetype(FONT_SEMI, 16 * SCALE)
font_badge = ImageFont.truetype(FONT_BOLD, 13 * SCALE)
font_headline = ImageFont.truetype(FONT_BOLD, 44 * SCALE)
font_sub = ImageFont.truetype(FONT_SEMI, 21 * SCALE)

font_data_lbl = ImageFont.truetype(FONT_SEMI, 14 * SCALE)
font_data_val = ImageFont.truetype(FONT_BOLD, 21 * SCALE)
font_data_line = ImageFont.truetype(FONT_SEMI, 15 * SCALE)
font_tag = ImageFont.truetype(FONT_SEMI, 13 * SCALE)

lx = 75 * SCALE

def draw_shadowed_text(draw_ctx, pos, text, font, fill_color, shadow_offsets=[(0, 3*SCALE, 160), (0, 6*SCALE, 90)]):
    x, y = pos
    for dx, dy, op in shadow_offsets:
        draw_ctx.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, op))
    draw_ctx.text((x, y), text, font=font, fill=fill_color)

# --- [Header: TechCapitalLab | 글로벌 기업 심층 분석 • 2026.09.14] ---
top_y = 56 * SCALE

brand_text = "TechCapitalLab"
brand_w = font_brand.getbbox(brand_text)[2] - font_brand.getbbox(brand_text)[0]
draw_shadowed_text(draw, (lx, top_y), brand_text, font=font_brand, fill_color=(0, 229, 255, 255))

sep_text = "|"
draw.text((lx + brand_w + 14 * SCALE, top_y + 1 * SCALE), sep_text, font=font_top_meta, fill=(100, 116, 139, 200))
sep_w = font_top_meta.getbbox(sep_text)[2] - font_top_meta.getbbox(sep_text)[0]

meta_text = "글로벌 기업 심층 분석  •  2026.09.14"
meta_x = lx + brand_w + 14 * SCALE + sep_w + 14 * SCALE
draw_shadowed_text(draw, (meta_x, top_y + 1 * SCALE), meta_text, font=font_top_meta, fill_color=(148, 163, 184, 255))

# Underline accent
bar_y = top_y + 32 * SCALE
draw.line([(lx, bar_y), (lx + 64 * SCALE, bar_y)], fill=(0, 229, 255, 255), width=3 * SCALE)
draw.line([(lx + 64 * SCALE, bar_y), (lx + 320 * SCALE, bar_y)], fill=(0, 229, 255, 45), width=1 * SCALE)

# --- [Badge Line: 스포츠 제국의 균열 • 나이키 18년 만의 굴욕] ---
badg_y = 112 * SCALE
badg_pad_h = 24 * SCALE
badg_pad_w = 12 * SCALE

# Badge 1: 스포츠 제국의 균열 (Crimson Red / Wine accent)
b1_text = "스포츠 제국의 균열"
b1_w = font_badge.getbbox(b1_text)[2] - font_badge.getbbox(b1_text)[0]
draw.rounded_rectangle([lx, badg_y, lx + b1_w + badg_pad_w * 2, badg_y + badg_pad_h],
                       radius=4 * SCALE, fill=(76, 5, 25, 210), outline=(244, 63, 94, 150), width=1 * SCALE)
draw.text((lx + badg_pad_w, badg_y + 4 * SCALE), b1_text, font=font_badge, fill=(251, 113, 133, 255))

# Badge 2: 나이키 18년 만의 굴욕 (Amber Gold accent)
b2_text = "나이키 18년 만의 굴욕"
b2_w = font_badge.getbbox(b2_text)[2] - font_badge.getbbox(b2_text)[0]
b2_x = lx + b1_w + badg_pad_w * 2 + 10 * SCALE
draw.rounded_rectangle([b2_x, badg_y, b2_x + b2_w + badg_pad_w * 2, badg_y + badg_pad_h],
                       radius=4 * SCALE, fill=(30, 27, 75, 210), outline=(245, 158, 11, 140), width=1 * SCALE)
draw.text((b2_x + badg_pad_w, badg_y + 4 * SCALE), b2_text, font=font_badge, fill=(251, 191, 36, 255))

# --- [Main Headline: 나이키의 추락, 왜 중국산에도 밀렸나?] ---
hl_y = 158 * SCALE
hl_text = "나이키의 추락, 왜 중국산에도 밀렸나?"
draw_shadowed_text(draw, (lx, hl_y), hl_text, font=font_headline, fill_color=(255, 255, 255, 255),
                   shadow_offsets=[(0, 2*SCALE, 190), (0, 6*SCALE, 140), (0, 12*SCALE, 80)])

# --- [Subtitle: DTC 맹신과 러닝 혁신 실종이 부른 3대 패착] ---
sub_y = 238 * SCALE
sub_text = "DTC 맹신과 러닝 혁신 실종이 부른 3대 패착"
draw_shadowed_text(draw, (lx, sub_y), sub_text, font=font_sub, fill_color=(251, 191, 36, 255),
                   shadow_offsets=[(0, 2*SCALE, 160), (0, 5*SCALE, 100)])

# Editorial divider line
sep_line_y = 296 * SCALE
draw.line([(lx, sep_line_y), (lx + 560 * SCALE, sep_line_y)], fill=(51, 65, 85, 180), width=1 * SCALE)
draw.line([(lx, sep_line_y), (lx + 100 * SCALE, sep_line_y)], fill=(244, 63, 94, 200), width=1 * SCALE)

# --- [Data Section: 3-Column Highlights (No Box, Drop Shadow)] ---
metrics = [
    {
        "label": "주가 고점 대비",
        "value": "-55% 폭락",
        "val_color": (244, 63, 94, 255) # Rose Crimson
    },
    {
        "label": "핵심 지수 퇴출",
        "value": "18년 만에 S&P100 방출",
        "val_color": (56, 189, 248, 255) # Sky Cyan
    },
    {
        "label": "신흥 강자 약진",
        "value": "호카·온러닝 점유율 폭증",
        "val_color": (52, 211, 153, 255) # Emerald Green
    }
]

for m in metrics:
    lw = font_data_lbl.getbbox(m["label"])[2] - font_data_lbl.getbbox(m["label"])[0]
    vw = font_data_val.getbbox(m["value"])[2] - font_data_val.getbbox(m["value"])[0]
    m["width"] = max(lw, vw)

data_y = 324 * SCALE
cur_x = lx

for i, m in enumerate(metrics):
    # Dot accent
    draw.ellipse([cur_x, data_y + 4 * SCALE, cur_x + 6 * SCALE, data_y + 10 * SCALE], fill=m["val_color"])
    
    # Label
    draw_shadowed_text(draw, (cur_x + 12 * SCALE, data_y), m["label"], font=font_data_lbl, fill_color=(148, 163, 184, 240),
                       shadow_offsets=[(0, 2*SCALE, 120)])
    
    # Value
    draw_shadowed_text(draw, (cur_x, data_y + 24 * SCALE), m["value"], font=font_data_val, fill_color=m["val_color"],
                       shadow_offsets=[(0, 3*SCALE, 160), (0, 6*SCALE, 90)])
    
    cur_x += m["width"] + 18 * SCALE
    
    # Divider
    if i < len(metrics) - 1:
        draw.line([(cur_x, data_y + 4 * SCALE), (cur_x, data_y + 50 * SCALE)], fill=(51, 65, 85, 180), width=1 * SCALE)
        cur_x += 18 * SCALE

# --- [Data Line: 주가 고점 대비 -55% 폭락 | 18년 만에 S&P100 퇴출 | 호카·온러닝 점유율 폭증] ---
data_line_y = 416 * SCALE

def draw_styled_data_line(draw_ctx, start_x, y):
    parts = [
        ("주가 고점 대비 -55% 폭락", (244, 63, 94, 255)),
        (" | ", (100, 116, 139, 200)),
        ("18년 만에 S&P100 퇴출", (56, 189, 248, 255)),
        (" | ", (100, 116, 139, 200)),
        ("호카·온러닝 점유율 폭증", (52, 211, 153, 255))
    ]
    px = start_x
    for txt, col in parts:
        for dx, dy, op in [(0, 2*SCALE, 130), (0, 4*SCALE, 70)]:
            draw_ctx.text((px + dx, y + dy), txt, font=font_data_line, fill=(0, 0, 0, op))
        draw_ctx.text((px, y), txt, font=font_data_line, fill=col)
        px += font_data_line.getbbox(txt)[2] - font_data_line.getbbox(txt)[0]

draw_styled_data_line(draw, lx, data_line_y)

# --- [Tags at bottom] ---
tags_y = 486 * SCALE
tags = ["#나이키", "#DTC실패", "#호카", "#온러닝", "#안타스포츠", "#TechCapitalLab"]
tag_x = lx

for t in tags:
    tw = font_tag.getbbox(t)[2] - font_tag.getbbox(t)[0]
    draw.rounded_rectangle([tag_x, tags_y, tag_x + tw + 16 * SCALE, tags_y + 24 * SCALE],
                           radius=4 * SCALE, fill=(15, 23, 42, 210), outline=(51, 65, 85, 210), width=1 * SCALE)
    draw.text((tag_x + 8 * SCALE, tags_y + 3 * SCALE), t, font=font_tag, fill=(148, 163, 184, 255))
    tag_x += tw + 12 * SCALE

# 4. Composite and Downsample
final_canvas = Image.alpha_composite(canvas, txt_layer)
os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
final_img = final_canvas.convert("RGB").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
final_img.save(OUTPUT_PATH, format="PNG", optimize=True)
print(f"Successfully generated: {OUTPUT_PATH}")
