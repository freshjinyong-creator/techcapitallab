#!/usr/bin/env python3
import os
import re
import sys
from datetime import datetime, timezone

OBSIDIAN_DIR = "/home/freshjinyong/ObsidianVault/04. Resources"
ASTRO_POSTS_DIR = "/home/freshjinyong/techcapitallab/src/content/posts"
DEFAULT_TAGS = "  - 투자인사이트\n  - 경제분석"

# Optional "> " blockquote prefix, optional "**" bold, then label + ":"
_META_RE = re.compile(
    r"^\s*(?:>\s*)?(?:\*{1,2}\s*)?(작성일|저장\s*위치|핵심\s*키워드|분량|저장폴더|카테고리)(?:\s*\*{1,2})?\s*:"
)


def clean_obsidian_metadata(text):
    """Drop internal Obsidian metadata lines (blockquote or plain) from body."""
    lines = text.splitlines()
    return "\n".join(l for l in lines if not _META_RE.match(l)).strip()


def slugify(text):
    text = text.lower()
    return re.sub(r"[^a-z0-9가-힣]+", "-", text).strip("-")


def publish_note(filename, category="2. 뉴스 속 경제"):
    src_path = os.path.join(OBSIDIAN_DIR, filename)
    if not os.path.exists(src_path):
        print(f"Error: {src_path} not found.")
        return

    body = clean_obsidian_metadata(open(src_path, encoding="utf-8").read())

    title = filename.replace(".md", "")
    title = re.sub(r"^\d{4}-\d{2}-\d{2}[_-]", "", title).replace("_", " ")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # First paragraph of body (stop at the first blank-line or horizontal rule)
    first_para = re.split(r"\n\s*\n|^---$", body, flags=re.M)[0]
    desc = re.sub(r"[#*`_\[\]>]", "", first_para).strip()[:100]

    if body.startswith("---"):
        # reuse existing frontmatter, inject missing required fields
        fm, _, md = body[3:].partition("---")
        fields = {k.strip(): v.strip()
                  for k, v in (re.split(r":\s*", l, 1)
                               for l in fm.splitlines() if ":" in l)}
        if "pubDatetime" not in fields:
            fm = f"{fm.rstrip()}\npubDatetime: {now_iso}"
        if "category" not in fields:
            fm = f"{fm.rstrip()}\ncategory: {category}"
        if "description" not in fields:
            fm = f'{fm.rstrip()}\ndescription: "{desc}"'
        content = f"---\n{fm.strip()}\n---\n\n{md.strip()}\n"
    else:
        content = (
            f"---\ntitle: {title!r}\npubDatetime: {now_iso}\n"
            f'description: "{desc}"\ncategory: {category!r}\ntags:\n{DEFAULT_TAGS}\n---\n\n{body}\n'
        )

    dest_path = os.path.join(ASTRO_POSTS_DIR, f"{slugify(title)}.md")
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Successfully published: {filename} -> src/content/posts/{slugify(title)}.md")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 sync_post.py <filename.md> [category]")
        sys.exit(1)
    publish_note(sys.argv[1],
                 sys.argv[2] if len(sys.argv) > 2 else "2. 뉴스 속 경제")
