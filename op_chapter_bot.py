"""
One Piece chapter watcher for tcbonepiecechapters.com

Checks the homepage every 15-30 minutes (randomized) and sends a notification
when a One Piece chapter newer than the last one seen appears.

Setup:
    pip install requests
    Set ONE of these (or both):
      DISCORD_WEBHOOK_URL  - Discord channel webhook (Channel settings > Integrations > Webhooks)
      NTFY_TOPIC           - any unique topic name; install the ntfy app and subscribe to it
    python op_chapter_bot.py          # runs forever, checks every 15-30 min
    python op_chapter_bot.py --once   # single check (used by GitHub Actions)
"""

import json
import os
import random
import re
import sys
import time
from pathlib import Path

import requests

URL = "https://tcbonepiecechapters.com/"
STATE_FILE = Path(__file__).with_name("op_state.json")
MIN_WAIT, MAX_WAIT = 15 * 60, 30 * 60  # seconds

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")

# Matches links like /chapters/8006/one-piece-chapter-1194 (also 1194.5)
# and skips spin-offs like "one-piece-nami-vs-kalifa-..."
CHAPTER_RE = re.compile(
    r'href="((?:https://tcbonepiecechapters\.com)?/chapters/\d+/one-piece-chapter-(\d+(?:\.\d+)?)[^"]*)"'
)

HEADERS = {"User-Agent": "Mozilla/5.0 (personal chapter notifier)"}


def load_last_seen() -> float:
    try:
        return float(json.loads(STATE_FILE.read_text())["last_chapter"])
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return 0.0


def save_last_seen(chapter: float) -> None:
    STATE_FILE.write_text(json.dumps({"last_chapter": chapter}))


def fetch_latest() -> tuple[float, str] | None:
    resp = requests.get(URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    found = {
        float(num): link if link.startswith("http") else URL.rstrip("/") + link
        for link, num in CHAPTER_RE.findall(resp.text)
    }
    if not found:
        return None
    latest = max(found)
    return latest, found[latest]


def fmt(ch: float) -> str:
    return str(int(ch)) if ch.is_integer() else str(ch)


def notify(chapter: float, link: str) -> None:
    msg = f"One Piece Chapter {fmt(chapter)} is out: {link}"
    print(msg)
    if DISCORD_WEBHOOK_URL:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=15)
    if NTFY_TOPIC:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode(),
                      headers={"Title": "New One Piece chapter", "Click": link}, timeout=15)


def check(last_seen: float) -> float:
    """One check. Returns the (possibly updated) last seen chapter."""
    result = fetch_latest()
    if not result:
        raise RuntimeError("No One Piece chapter found on page (layout changed or blocked?)")
    chapter, link = result
    if last_seen == 0:
        # First run: remember the current chapter, don't notify
        print(f"Baseline set to chapter {fmt(chapter)}")
        save_last_seen(chapter)
        return chapter
    if chapter > last_seen:
        notify(chapter, link)
        save_last_seen(chapter)
        return chapter
    print(f"No new chapter (latest is {fmt(chapter)})")
    return last_seen


def main() -> None:
    last_seen = load_last_seen()
    print(f"Last seen chapter: {fmt(last_seen) if last_seen else 'none'}")

    # --once: single check then exit (used by GitHub Actions)
    if "--once" in sys.argv:
        check(last_seen)  # errors propagate -> failed run is visible in GitHub
        return

    while True:
        try:
            last_seen = check(last_seen)
        except (requests.RequestException, RuntimeError) as e:
            print(f"Check failed: {e}")
        wait = random.randint(MIN_WAIT, MAX_WAIT)
        print(f"Next check in {wait // 60} min")
        time.sleep(wait)


if __name__ == "__main__":
    main()
