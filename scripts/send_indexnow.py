#!/usr/bin/env python3
import os
import sys
import glob
import json
import urllib.request
import urllib.error
import argparse

KEY = "c037996c141d4c82bcf2c2a05cf4876b"
HOST = "techcapitallab.com"
KEY_LOCATION = f"https://{HOST}/{KEY}.txt"
POSTS_DIR = "/home/freshjinyong/techcapitallab/src/content/posts"
BASE_URL = f"https://{HOST}"

def get_all_urls():
    urls = [f"{BASE_URL}/"]
    for f in sorted(glob.glob(os.path.join(POSTS_DIR, "*.md"))):
        slug = os.path.basename(f).replace(".md", "")
        urls.append(f"{BASE_URL}/posts/{slug}/")
    return urls

def get_latest_urls():
    files = glob.glob(os.path.join(POSTS_DIR, "*.md"))
    if not files:
        return [f"{BASE_URL}/"]
    files.sort(key=os.path.getmtime, reverse=True)
    latest_slug = os.path.basename(files[0]).replace(".md", "")
    return [f"{BASE_URL}/", f"{BASE_URL}/posts/{latest_slug}/"]

def main():
    parser = argparse.ArgumentParser(description="IndexNow Submitter for TechCapitalLab (Bing, Naver, Yandex)")
    parser.add_argument("--all", action="store_true", help="Submit all post URLs")
    parser.add_argument("targets", nargs="*", help="Specific URLs or slugs")
    args = parser.parse_args()

    if args.all:
        url_list = get_all_urls()
    elif args.targets:
        url_list = [f"{BASE_URL}/"]
        for t in args.targets:
            if t.startswith("http"):
                url_list.append(t)
            else:
                slug = t.replace(".md", "").strip("/")
                url_list.append(f"{BASE_URL}/posts/{slug}/")
        url_list = list(dict.fromkeys(url_list))
    else:
        url_list = get_latest_urls()

    print(f"[*] IndexNow 전송 시작 (총 {len(url_list)}개 URL)...")

    payload = {
        "host": HOST,
        "key": KEY,
        "keyLocation": KEY_LOCATION,
        "urlList": url_list
    }

    endpoint = "https://api.indexnow.org/indexnow"
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"}
    )

    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            print(f"[+] IndexNow 전송 성공! (HTTP Status: {status})")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"[-] IndexNow 전송 실패 (HTTP {e.code}): {body}")
    except Exception as e:
        print(f"[-] IndexNow 전송 에러: {e}")

if __name__ == "__main__":
    main()
