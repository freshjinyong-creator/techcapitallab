#!/usr/bin/env python3
import os
import re
import glob

posts_dir = "/home/freshjinyong/techcapitallab/src/content/posts"

def sanitize_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Split frontmatter and body
    parts = content.split("---", 2)
    if len(parts) < 3:
        return False

    frontmatter = parts[1]
    body = parts[2]

    original_body = body

    # 1. Replace all **text** with <strong>text</strong>
    # Using regex to match **...** non-greedy
    body = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', body)

    # 2. Fix nested or broken <strong> tags if any
    body = re.sub(r'<strong>\s*<strong>', '<strong>', body)
    body = re.sub(r'</strong>\s*</strong>', '</strong>', body)

    # 3. Clean up spaces around strong tag inside
    # e.g., <strong> word </strong> -> <strong>word</strong>
    body = re.sub(r'<strong>\s+(.*?)\s+</strong>', r'<strong>\1</strong>', body)

    if body != original_body:
        new_content = f"---{frontmatter}---{body}"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
        return True
    return False

def main():
    files = glob.glob(os.path.join(posts_dir, "*.md"))
    modified_count = 0
    for f in sorted(files):
        if sanitize_file(f):
            print(f"Sanitized: {os.path.basename(f)}")
            modified_count += 1
    print(f"\nDone! Total {modified_count} files sanitized.")

if __name__ == "__main__":
    main()
