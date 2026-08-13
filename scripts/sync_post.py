#!/usr/bin/env python3
import os
import sys
import re
from datetime import datetime, timezone

OBSIDIAN_DIR = "/home/freshjinyong/ObsidianVault/04. Resources"
ASTRO_POSTS_DIR = "/home/freshjinyong/techcapitallab/src/content/posts"

def clean_obsidian_metadata(text):
    """
    Strips internal Obsidian header lines like:
    작성일: ...
    저장 위치: ... / 저장위치: ...
    핵심 키워드: ...
    분량: ...
    """
    lines = text.splitlines()
    cleaned_lines = []
    for line in lines:
        # Strip lines starting with metadata keywords
        if re.match(r'^\s*(작성일|저장\s*위치|핵심\s*키워드|분량|저장폴더|카테고리)\s*:', line):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()

def slugify(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9가-힣]+', '-', text)
    return text.strip('-')

def publish_note(filename, category="2. 뉴스 속 경제"):
    src_path = os.path.join(OBSIDIAN_DIR, filename)
    if not os.path.exists(src_path):
        print(f"Error: {src_path} not found.")
        return

    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()

    title = filename.replace(".md", "")
    clean_title = re.sub(r'^\d{4}-\d{2}-\d{2}[_-]', '', title)
    clean_title = clean_title.replace("_", " ")

    # Check if frontmatter exists
    has_fm = content.startswith("---")
    
    if has_fm:
        parts = content.split("---", 2)
        fm = parts[1]
        body = parts[2] if len(parts) > 2 else ""
        body = clean_obsidian_metadata(body)
        
        if "category:" not in fm:
            fm = fm.strip() + f"\ncategory: {category}\n"
        if "pubDatetime:" not in fm:
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            fm = fm.strip() + f"\npubDatetime: {now_iso}\n"
        if "description:" not in fm:
            desc = re.sub(r'[#*`_\[\]]', '', body).strip().replace('\n', ' ')[:100]
            fm = fm.strip() + f"\ndescription: \"{desc}\"\n"
        
        new_content = f"---{fm}---\n\n{body}\n"
    else:
        body = clean_obsidian_metadata(content)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        desc = re.sub(r'[#*`_\[\]]', '', body).strip().replace('\n', ' ')[:100]
        new_content = f"""---
title: "{clean_title}"
pubDatetime: {now_iso}
description: "{desc}"
category: "{category}"
tags:
  - 투자인사이트
  - 경제분석
---

{body}
"""

    slug = slugify(clean_title)
    dest_filename = f"{slug}.md"
    dest_path = os.path.join(ASTRO_POSTS_DIR, dest_filename)

    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"Successfully published: {filename} -> src/content/posts/{dest_filename}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 sync_post.py <filename.md> [category]")
        sys.exit(1)
    
    fname = sys.argv[1]
    cat = sys.argv[2] if len(sys.argv) > 2 else "2. 뉴스 속 경제"
    publish_note(fname, cat)
