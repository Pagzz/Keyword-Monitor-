import json
import os
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

with open("config.json") as f:
    CONFIG = json.load(f)

FB_CFG = CONFIG.get("facebook", {})

# Override access token with .env value if present
if os.getenv("FACEBOOK_ACCESS_TOKEN"):
    FB_CFG["access_token"] = os.getenv("FACEBOOK_ACCESS_TOKEN")

BASE_URL = "https://graph.facebook.com/v19.0"


# ── Graph API helpers ──────────────────────────────────────────────────────────

def graph_get(path: str, token: str, params: dict = {}) -> dict | None:
    try:
        r = requests.get(
            f"{BASE_URL}/{path}",
            params={"access_token": token, **params},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        print(f"[Facebook] HTTP error on /{path}: {e.response.status_code} {e.response.text[:200]}")
    except Exception as e:
        print(f"[Facebook] Request error: {e}")
    return None


# ── Keyword matching ───────────────────────────────────────────────────────────

def find_keyword(text: str, keyword: str, case_sensitive: bool) -> bool:
    if not case_sensitive:
        return keyword.lower() in text.lower()
    return keyword in text


def check_text(text: str, keywords: list, case_sensitive: bool) -> str | None:
    for kw in keywords:
        if find_keyword(text, kw, case_sensitive):
            return kw
    return None


# ── Poll a group or page feed ──────────────────────────────────────────────────

def poll_feed(monitor: dict, seen_ids: set, fire_alert_fn) -> None:
    name = monitor["name"]
    keywords = monitor["keywords"]
    case_sensitive = monitor.get("case_sensitive", False)
    token = FB_CFG.get("access_token", "")
    source_type = monitor.get("type", "group")  # "group" or "page"

    for target_id in monitor["ids"]:
        data = graph_get(
            f"{target_id}/feed",
            token,
            params={"fields": "id,message,story,permalink_url,created_time,from"},
        )
        if not data:
            continue

        posts = data.get("data", [])
        for post in posts:
            post_id = post.get("id")
            if post_id in seen_ids:
                continue
            seen_ids.add(post_id)

            text = post.get("message") or post.get("story") or ""
            if not text:
                continue

            hit = check_text(text, keywords, case_sensitive)
            if hit:
                fire_alert_fn(
                    monitor_name=name,
                    keyword=hit,
                    source_type=f"facebook_{source_type}_post",
                    subreddit=target_id,
                    title=f"Facebook {source_type.title()} Post",
                    url=post.get("permalink_url", f"https://facebook.com/{post_id}"),
                    snippet=text[:500],
                )

            # Also check comments on matching OR all posts
            if monitor.get("watch_comments", True):
                poll_comments(post_id, target_id, name, keywords,
                              case_sensitive, seen_ids, fire_alert_fn, token)


def poll_comments(post_id: str, source_id: str, monitor_name: str,
                  keywords: list, case_sensitive: bool,
                  seen_ids: set, fire_alert_fn, token: str) -> None:
    data = graph_get(
        f"{post_id}/comments",
        token,
        params={"fields": "id,message,permalink_url,created_time,from"},
    )
    if not data:
        return

    for comment in data.get("data", []):
        cid = comment.get("id")
        if cid in seen_ids:
            continue
        seen_ids.add(cid)

        text = comment.get("message", "")
        if not text:
            continue

        hit = check_text(text, keywords, case_sensitive)
        if hit:
            fire_alert_fn(
                monitor_name=monitor_name,
                keyword=hit,
                source_type="facebook_comment",
                subreddit=source_id,
                title="Facebook Comment",
                url=comment.get("permalink_url", f"https://facebook.com/{cid}"),
                snippet=text[:500],
            )


# ── Main watcher loop ──────────────────────────────────────────────────────────

def watch_facebook(monitor: dict, fire_alert_fn) -> None:
    name = monitor["name"]
    interval = FB_CFG.get("poll_interval_seconds", 60)
    seen_ids: set = set()

    print(f"[Facebook] [{name}] Starting — polling every {interval}s")
    while True:
        try:
            poll_feed(monitor, seen_ids, fire_alert_fn)
        except Exception as e:
            print(f"[Facebook] [{name}] Error: {e}")
        time.sleep(interval)
