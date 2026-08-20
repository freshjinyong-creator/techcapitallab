#!/usr/bin/env python3
import os
import glob
import json
import time
import urllib.request
import urllib.error
from google.oauth2 import service_account
from google.auth.transport.requests import Request

KEY_PATH = "/home/freshjinyong/techcapitallab/google_indexing_key.json"
POSTS_DIR = "/home/freshjinyong/techcapitallab/src/content/posts"
BASE_URL = "https://techcapitallab.com"

def get_urls():
    urls = [f"{BASE_URL}/"]
    for f in sorted(glob.glob(os.path.join(POSTS_DIR, "*.md"))):
        slug = os.path.basename(f).replace(".md", "")
        urls.append(f"{BASE_URL}/posts/{slug}/")
    return urls

def main():
    scopes = ["https://www.googleapis.com/auth/indexing"]
    credentials = service_account.Credentials.from_service_account_file(KEY_PATH, scopes=scopes)
    credentials.refresh(Request())
    
    urls = get_urls()
    print(f"[*] 총 {len(urls)}개 URL 전송 시작...")
    
    endpoint = "https://indexing.googleapis.com/v3/urlNotifications:publish"
    
    success_count = 0
    fail_count = 0
    
    for idx, target_url in enumerate(urls, 1):
        payload = {
            "url": target_url,
            "type": "URL_UPDATED"
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {credentials.token}"
            }
        )
        try:
            with urllib.request.urlopen(req) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                notify_time = res_data.get("urlNotificationMetadata", {}).get("latestUpdate", {}).get("notifyTime", "")
                print(f"[{idx}/{len(urls)}] 성공: {target_url} ({notify_time})")
                success_count += 1
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            print(f"[{idx}/{len(urls)}] 실패 ({e.code}): {target_url}\n  -> {err_body}")
            fail_count += 1
        except Exception as e:
            print(f"[{idx}/{len(urls)}] 에러: {target_url} -> {e}")
            fail_count += 1
        time.sleep(0.3)
        
    print(f"\n[결과] 전송 완료: 성공 {success_count}개 / 실패 {fail_count}개")

if __name__ == "__main__":
    main()
