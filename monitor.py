import json
import logging
import os
import smtplib
import threading
import time
import winsound
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import praw
from dotenv import load_dotenv
from plyer import notification

load_dotenv()

# ── Setup ──────────────────────────────────────────────────────────────────────

with open("config.json") as f:
    CONFIG = json.load(f)

# Override config credentials with .env values if present
if os.getenv("REDDIT_CLIENT_ID"):
    CONFIG["reddit"]["client_id"] = os.getenv("REDDIT_CLIENT_ID")
if os.getenv("REDDIT_CLIENT_SECRET"):
    CONFIG["reddit"]["client_secret"] = os.getenv("REDDIT_CLIENT_SECRET")
if os.getenv("REDDIT_USER_AGENT"):
    CONFIG["reddit"]["user_agent"] = os.getenv("REDDIT_USER_AGENT")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(CONFIG["notifications"]["log_file"], encoding="utf-8"),
    ],
)
log = logging.getLogger("SocialSpy")

reddit = praw.Reddit(
    client_id=CONFIG["reddit"]["client_id"],
    client_secret=CONFIG["reddit"]["client_secret"],
    user_agent=CONFIG["reddit"]["user_agent"],
)


# ── Notifications ──────────────────────────────────────────────────────────────

def notify_desktop(title: str, message: str) -> None:
    try:
        notification.notify(
            title=title,
            message=message[:255],
            app_name="Social Spy",
            timeout=8,
        )
    except Exception as e:
        log.warning(f"Desktop notification failed: {e}")


def notify_email(subject: str, body: str) -> None:
    cfg = CONFIG["notifications"]["email"]
    try:
        msg = MIMEMultipart()
        msg["From"] = cfg["sender"]
        msg["To"] = cfg["recipient"]
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"]) as server:
            server.starttls()
            server.login(cfg["sender"], cfg["password"])
            server.send_message(msg)
    except Exception as e:
        log.warning(f"Email notification failed: {e}")


def play_sound() -> None:
    try:
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass


def fire_alert(monitor_name: str, keyword: str, source_type: str,
               subreddit: str, title: str, url: str, snippet: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    notif_cfg = CONFIG["notifications"]

    header = f'[{monitor_name}] Keyword "{keyword}" in r/{subreddit} ({source_type})'
    log.info(f"{header}\n  Title  : {title}\n  URL    : {url}\n  Snippet: {snippet[:200]}")

    if notif_cfg.get("desktop"):
        notify_desktop(header, f"{title}\n\n{snippet[:150]}")

    if notif_cfg.get("sound"):
        play_sound()

    if notif_cfg["email"].get("enabled"):
        body = (
            f"Keyword  : {keyword}\n"
            f"Monitor  : {monitor_name}\n"
            f"Subreddit: r/{subreddit}\n"
            f"Type     : {source_type}\n"
            f"Time     : {timestamp}\n"
            f"Title    : {title}\n"
            f"URL      : {url}\n\n"
            f"--- Content ---\n{snippet}"
        )
        notify_email(f"Social Spy Alert — {keyword}", body)


# ── Matching ───────────────────────────────────────────────────────────────────

def find_keyword(text: str, keyword: str, case_sensitive: bool) -> bool:
    if not case_sensitive:
        return keyword.lower() in text.lower()
    return keyword in text


def check_text(text: str, keywords: list[str], case_sensitive: bool) -> str | None:
    for kw in keywords:
        if find_keyword(text, kw, case_sensitive):
            return kw
    return None


# ── Stream Workers ─────────────────────────────────────────────────────────────

def watch_submissions(monitor: dict) -> None:
    name = monitor["name"]
    keywords = monitor["keywords"]
    case_sensitive = monitor.get("case_sensitive", False)
    sub_str = "+".join(monitor["subreddits"])
    subreddit = reddit.subreddit(sub_str)

    log.info(f'[{name}] Watching POSTS on r/{sub_str} for: {keywords}')
    while True:
        try:
            for submission in subreddit.stream.submissions(skip_existing=True):
                combined = f"{submission.title} {submission.selftext}"
                hit = check_text(combined, keywords, case_sensitive)
                if hit:
                    fire_alert(
                        monitor_name=name,
                        keyword=hit,
                        source_type="post",
                        subreddit=submission.subreddit.display_name,
                        title=submission.title,
                        url=f"https://reddit.com{submission.permalink}",
                        snippet=submission.selftext[:500] or submission.title,
                    )
        except Exception as e:
            log.error(f"[{name}] Submission stream error: {e} — reconnecting in 30s")
            time.sleep(30)


def watch_comments(monitor: dict) -> None:
    name = monitor["name"]
    keywords = monitor["keywords"]
    case_sensitive = monitor.get("case_sensitive", False)
    sub_str = "+".join(monitor["subreddits"])
    subreddit = reddit.subreddit(sub_str)

    log.info(f'[{name}] Watching COMMENTS on r/{sub_str} for: {keywords}')
    while True:
        try:
            for comment in subreddit.stream.comments(skip_existing=True):
                hit = check_text(comment.body, keywords, case_sensitive)
                if hit:
                    fire_alert(
                        monitor_name=name,
                        keyword=hit,
                        source_type="comment",
                        subreddit=comment.subreddit.display_name,
                        title=comment.submission.title,
                        url=f"https://reddit.com{comment.permalink}",
                        snippet=comment.body[:500],
                    )
        except Exception as e:
            log.error(f"[{name}] Comment stream error: {e} — reconnecting in 30s")
            time.sleep(30)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=" * 60)
    log.info("  Social Spy — Reddit Monitor  ")
    log.info("=" * 60)

    threads: list[threading.Thread] = []

    for monitor in CONFIG["monitors"]:
        if monitor.get("watch_posts", True):
            t = threading.Thread(target=watch_submissions, args=(monitor,), daemon=True)
            t.start()
            threads.append(t)

        if monitor.get("watch_comments", True):
            t = threading.Thread(target=watch_comments, args=(monitor,), daemon=True)
            t.start()
            threads.append(t)

    log.info(f"Started {len(threads)} watcher thread(s). Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()
