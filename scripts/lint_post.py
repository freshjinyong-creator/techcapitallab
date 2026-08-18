#!/usr/bin/env python3
"""
TechCapitalLab Blog Post Linter & Auto-Fixer
Prevents markdown parsing bugs (e.g. **'keyword'**), leaks of internal terms, and verifies formatting.
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

    original_content = content
    fixed = False
    errors = []

    # 1. Check & Auto-fix: **'text'** or **"text"** markdown bold parsing bug
    bold_quote_pattern = re.compile(r"\*\*([\'\"])(.*?)\1\*\*")
    if bold_quote_pattern.search(content):
        print("  [FIX] Found bold-quote conflict pattern (**'text'**). Auto-fixing to **text**...")
        content = bold_quote_pattern.sub(r"**\2**", content)
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
