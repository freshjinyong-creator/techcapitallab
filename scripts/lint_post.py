#!/usr/bin/env python3
"""
TechCapitalLab Blog Post Linter & Auto-Fixer
Prevents markdown parsing bugs (e.g. **'keyword'**, ~ strikethrough), leaks of internal terms, and verifies formatting.
"""

import sys
import re
import os
from datetime import datetime, timezone

INTERNAL_BANNED_WORDS = ["슈퍼스토커", "super-stocker", "super_stocker", "워렌메스"]

def lint_and_fix(file_path):
    if not os.path.exists(file_path):
        print(f"[ERROR] File not found: {file_path}")
        return False

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    fixed = False
    errors = []

    # 1. Check & Auto-fix: **'text'** or quotes inside bold markdown
    def fix_bold_quotes(match):
        inner = match.group(1).replace("'", "").replace('"', "")
        return f"**{inner}**"

    bold_quote_pattern = re.compile(r"\*\*([^\*\n\r]*?[\'\"][^\*\n\r]*?)\*\*")
    if bold_quote_pattern.search(content):
        print("  [FIX] Found quotes inside bold markdown. Auto-fixing...")
        content = bold_quote_pattern.sub(fix_bold_quotes, content)
        fixed = True

    # 1-2. Check & Auto-fix: **Korean(English)**조사 -> **Korean**(English)조사
    bold_paren_pattern = re.compile(r"\*\*([^\*\(\)\n\r]+)\(([^\*\(\)\n\r]+)\)\*\*([가-힣])")
    if bold_paren_pattern.search(content):
        print("  [FIX] Found bold-parenthesis particle conflict pattern (**Text(En)**Particle). Auto-fixing...")
        content = bold_paren_pattern.sub(r"**\1**(\2)\3", content)
        fixed = True

    # 1-3. Check & Auto-fix: **[text]**조사 -> **text**조사 (Bold bracket particle conflict)
    bold_bracket_particle_pattern = re.compile(r"\*\*\[([^\]\n\r]{5,})\]\*\*([가-힣])")
    if bold_bracket_particle_pattern.search(content):
        print("  [FIX] Found bold-bracket particle conflict pattern (**[long text]**Particle). Auto-fixing to **text**Particle...")
        content = bold_bracket_particle_pattern.sub(r"**\1**\2", content)
        fixed = True

    # 1-4. Check & Auto-fix: Tilde range causing markdown strikethrough (e.g. 15~20% -> 15%에서 20% 수준)
    tilde_range_pattern = re.compile(r"(\d+)~(\d+)(%|자|개|원|달러|배)?")
    if tilde_range_pattern.search(content):
        print("  [FIX] Found tilde (~) range that triggers markdown strikethrough (<del>). Auto-fixing...")
        def fix_tilde(m):
            unit = m.group(3) if m.group(3) else ""
            if unit:
                return f"{m.group(1)}{unit}에서 {m.group(2)}{unit}"
            return f"{m.group(1)}에서 {m.group(2)}"
        content = tilde_range_pattern.sub(fix_tilde, content)
        fixed = True

    # 1-5. Check & Auto-fix: Misplaced '작성 기준일' in middle of body (must only be at bottom)
    lines = content.splitlines()
    cleaned_lines = []
    found_mid_date = False
    in_bottom_section = False
    for i, line in enumerate(lines):
        if line.strip().startswith("**작성 기준일:") or line.strip().startswith("*작성 기준일:"):
            # Check if this is before the last 15 lines of the document
            if i < len(lines) - 15:
                print("  [FIX] Removed misplaced '작성 기준일' from middle of body.")
                found_mid_date = True
                continue
        cleaned_lines.append(line)

    if found_mid_date:
        content = "\n".join(cleaned_lines)
        fixed = True

    # 2. Check for banned internal terms
    for term in INTERNAL_BANNED_WORDS:
        if term in content:
            errors.append(f"Contains banned internal keyword: '{term}'")

    # 3. Check filename for dots
    filename = os.path.basename(file_path)
    base_name = filename.rsplit(".", 1)[0]
    if "." in base_name:
        errors.append(f"Filename contains dot (.) in slug name: {filename}. Use hyphens only!")

    # 4. Check pubDatetime
    pubdate_match = re.search(r"pubDatetime:\s*([^\n\r]+)", content)
    if pubdate_match:
        try:
            pubdate_str = pubdate_match.group(1).strip().strip("'\"")
            # Parse ISO date
            pub_dt = datetime.fromisoformat(pubdate_str.replace("Z", "+00:00"))
            now_dt = datetime.now(timezone.utc)
            if pub_dt > now_dt:
                errors.append(f"pubDatetime is in the future ({pub_dt} > {now_dt}). Astro will treat as scheduled and fail to generate index.html!")
        except Exception as e:
            pass

    # 5. Check for raw LaTeX syntax ($$ or \frac) which fails to render in AstroPaper
    if "$$" in content or r"\frac" in content:
        errors.append("Contains raw LaTeX math syntax ($$ or \\frac). AstroPaper does not render LaTeX by default. Use code block or plain text box instead!")

    # 6. Measure character count
    char_count = len(content)
    print(f"  [INFO] Verified char count: {char_count} chars (including whitespace)")

    if fixed:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        print("  [SUCCESS] Auto-fixed markdown errors successfully saved to file.")

    if errors:
        print("\n[LINT ERRORS FOUND]:")
        for err in errors:
            print(f"  ❌ {err}")
        return False

    print("  ✅ Post passed all validation rules.")
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 lint_post.py <path_to_post.md>")
        sys.exit(1)

    success = lint_and_fix(sys.argv[1])
    if not success:
        sys.exit(1)
