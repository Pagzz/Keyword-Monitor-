import json
import threading
from collections import deque
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from monitor import CONFIG, log, watch_comments, watch_submissions
from facebook_monitor import watch_facebook

PORT = 3013

# ── In-memory alert store (last 200 alerts) ───────────────────────────────────

alerts: deque[dict] = deque(maxlen=200)

# Patch fire_alert to also push into the web store
import monitor as _monitor

_original_fire = _monitor.fire_alert

def _patched_fire(monitor_name, keyword, source_type, subreddit, title, url, snippet):
    from datetime import datetime
    alerts.appendleft({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "monitor": monitor_name,
        "keyword": keyword,
        "type": source_type,
        "subreddit": subreddit,
        "title": title,
        "url": url,
        "snippet": snippet[:300],
    })
    _original_fire(monitor_name, keyword, source_type, subreddit, title, url, snippet)

_monitor.fire_alert = _patched_fire


# ── Start monitor threads on startup ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(f"Starting Social Spy on http://localhost:{PORT}")
    for monitor in CONFIG["monitors"]:
        if monitor.get("watch_posts", True):
            threading.Thread(target=watch_submissions, args=(monitor,), daemon=True).start()
        if monitor.get("watch_comments", True):
            threading.Thread(target=watch_comments, args=(monitor,), daemon=True).start()

    for fb_monitor in CONFIG.get("facebook", {}).get("monitors", []):
        threading.Thread(
            target=watch_facebook,
            args=(fb_monitor, _monitor.fire_alert),
            daemon=True,
        ).start()

    yield


app = FastAPI(title="Social Spy", lifespan=lifespan)


# ── API endpoints ──────────────────────────────────────────────────────────────

@app.get("/alerts")
def get_alerts():
    return list(alerts)


@app.get("/status")
def get_status():
    monitors = [
        {
            "name": m["name"],
            "subreddits": m["subreddits"],
            "keywords": m["keywords"],
        }
        for m in CONFIG["monitors"]
    ]
    return {"status": "running", "monitors": monitors, "alert_count": len(alerts)}


# ── Dashboard ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Social Spy</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #0f0f0f; color: #e0e0e0; padding: 24px; }
    h1 { font-size: 1.6rem; margin-bottom: 4px; color: #fff; }
    .sub { color: #888; font-size: 0.85rem; margin-bottom: 24px; }
    .status-bar { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
    .pill { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 8px;
            padding: 10px 16px; font-size: 0.82rem; color: #aaa; }
    .pill span { color: #fff; font-weight: 600; }
    #feed { display: flex; flex-direction: column; gap: 12px; }
    .card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 10px; padding: 16px; }
    .card-header { display: flex; justify-content: space-between; align-items: center;
                   margin-bottom: 8px; flex-wrap: wrap; gap: 8px; }
    .badge { font-size: 0.72rem; padding: 3px 10px; border-radius: 99px; font-weight: 600; }
    .post  { background: #1e3a5f; color: #60a5fa; }
    .comment { background: #1e3d2f; color: #4ade80; }
    .kw { background: #3b2f1e; color: #fb923c; }
    .card-title { font-size: 0.95rem; font-weight: 600; color: #fff; margin-bottom: 6px; }
    .card-title a { color: #60a5fa; text-decoration: none; }
    .card-title a:hover { text-decoration: underline; }
    .snippet { font-size: 0.82rem; color: #999; line-height: 1.5;
               white-space: pre-wrap; word-break: break-word; }
    .meta { font-size: 0.75rem; color: #555; margin-top: 8px; }
    .empty { text-align: center; color: #555; padding: 60px 0; font-size: 0.9rem; }
    .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
           background: #4ade80; margin-right: 6px; animation: pulse 2s infinite; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  </style>
</head>
<body>
  <h1><span class="dot"></span>Social Spy</h1>
  <p class="sub">Live keyword monitor — auto-refreshes every 10 seconds</p>

  <div class="status-bar" id="status-bar">
    <div class="pill">Loading...</div>
  </div>

  <div id="feed"><div class="empty">Waiting for keyword matches...</div></div>

  <script>
    async function loadStatus() {
      const res = await fetch('/status');
      const d = await res.json();
      const bar = document.getElementById('status-bar');
      bar.innerHTML =
        `<div class="pill">Status <span>Running</span></div>` +
        `<div class="pill">Alerts <span>${d.alert_count}</span></div>` +
        d.monitors.map(m =>
          `<div class="pill">r/${m.subreddits.join(', r/')} <span>${m.keywords.join(' · ')}</span></div>`
        ).join('');
    }

    async function loadAlerts() {
      const res = await fetch('/alerts');
      const data = await res.json();
      const feed = document.getElementById('feed');
      if (!data.length) {
        feed.innerHTML = '<div class="empty">Waiting for keyword matches...</div>';
        return;
      }
      feed.innerHTML = data.map(a => `
        <div class="card">
          <div class="card-header">
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
              <span class="badge ${a.type}">${a.type}</span>
              <span class="badge kw">${a.keyword}</span>
              <span style="font-size:0.78rem;color:#666">r/${a.subreddit}</span>
            </div>
            <span style="font-size:0.75rem;color:#555">${a.time}</span>
          </div>
          <div class="card-title"><a href="${a.url}" target="_blank">${a.title}</a></div>
          <div class="snippet">${a.snippet}</div>
          <div class="meta">${a.monitor}</div>
        </div>
      `).join('');
    }

    function refresh() { loadStatus(); loadAlerts(); }
    refresh();
    setInterval(refresh, 10000);
  </script>
</body>
</html>
"""


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)
