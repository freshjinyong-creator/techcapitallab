import os
import glob
import re

POSTS_DIR = "/home/freshjinyong/techcapitallab/src/content/posts"

def process_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()

    # Split frontmatter
    fm_match = re.match(r"^---\n(.*?)\n---\n*(.*)", raw, re.DOTALL)
    if not fm_match:
        print(f"[SKIP] Frontmatter parse failed: {filepath}")
        return False

    fm_text = fm_match.group(1)
    body_text = fm_match.group(2).strip()

    # If already has Direct Answer box, skip
    if "핵심 직답 (Direct Answer)" in body_text or "핵심 직답(Direct Answer)" in body_text:
        return False

    # Extract title
    title_m = re.search(r'title:\s*["\']?(.*?)["\']?\n(?=[a-zA-Z0-9_-]+:|$)', fm_text)
    title = title_m.group(1).strip("\"'") if title_m else ""

    # Extract description
    desc_m = re.search(r'description:\s*["\']?(.*?)["\']?\n(?=[a-zA-Z0-9_-]+:|$)', fm_text)
    desc = desc_m.group(1).strip("\"'") if desc_m else ""

    if not desc or len(desc) < 15:
        # Extract from first paragraph or TL;DR
        first_p_m = re.search(r'^(?!>|#|\s*$)(.+)', body_text, re.MULTILINE)
        if first_p_m:
            desc = first_p_m.group(1).strip()
            # truncate to 150 chars if too long
            if len(desc) > 150:
                desc = desc[:147] + "..."
        else:
            desc = f"{title}에 대한 실측 데이터와 심층 분석 내용입니다."

    # Clean desc
    desc = desc.replace("\n", " ").strip()

    # Construct direct answer box
    direct_answer_box = f"> <strong>핵심 직답 (Direct Answer)</strong><br>\n> {desc}\n"

    # Assemble new content
    new_content = f"---\n{fm_text}\n---\n\n{direct_answer_box}\n{body_text}\n"

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)

    return True

def main():
    files = sorted(glob.glob(os.path.join(POSTS_DIR, "*.md")))
    updated = 0
    for f in files:
        if process_file(f):
            updated += 1
            print(f"[UPDATED] {os.path.basename(f)}")

    print(f"\nTotal files: {len(files)}, Updated: {updated}")

if __name__ == "__main__":
    main()
