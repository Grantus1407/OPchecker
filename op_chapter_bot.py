"""
One Piece chapter watcher for tcbonepiecechapters.com

Finds the newest One Piece chapter in two ways and sends a notification
when it is newer than the last one seen:
  1. the homepage's chapter list (can be served from a stale cache)
  2. probing the next chapter IDs directly (/chapters/<id>), which are not cached

Setup:
    pip install requests
    Set ONE of these (or both):
      DISCORD_WEBHOOK_URL  - Discord channel webhook (Channel settings > Integrations > Webhooks)
      NTFY_TOPIC           - any unique topic name; install the ntfy app and subscribe to it
    python op_chapter_bot.py          # runs forever, checks every 15-30 min
    python op_chapter_bot.py --once   # single check (used by GitHub Actions)
    python op_chapter_bot.py --test   # send a test notification only
"""

import json
import os
import random
import re
import sys
import time
from pathlib import Path

import requests

BASE = "https://tcbonepiecechapters.com"
STATE_FILE = Path(__file__).with_name("op_state.json")
MIN_WAIT, MAX_WAIT = 15 * 60, 30 * 60  # seconds
# Chapter IDs are shared by all series and have gaps (deleted/unpublished IDs,
# e.g. 7999-8001 are missing between One Piece 1191 and 1192).
PROBE_MISSES = 10  # stop probing after this many missing IDs in a row
PROBE_MAX = 60     # never probe more than this many IDs per check

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")

# Homepage links like /chapters/8006/one-piece-chapter-1194 (also 1194.5);
# skips spin-offs like "one-piece-nami-vs-kalifa-..."
LINK_RE = re.compile(
    r'href="(?:https://tcbonepiecechapters\.com)?/chapters/(\d+)/one-piece-chapter-(\d+(?:\.\d+)?)[^"]*"'
)
# Chapter page title, e.g. "One Piece  Chapter 1195 | TCB Scans"
TITLE_RE = re.compile(r"<title>\s*One Piece\s+Chapter\s+(\d+(?:\.\d+)?)\s*\|", re.I)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (personal chapter notifier)",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

Found = tuple[float, int]  # (chapter number, chapter page id)


def load_state() -> dict:
    try:
        state = json.loads(STATE_FILE.read_text())
        return {"last_chapter": float(state.get("last_chapter", 0)),
                "last_id": int(state.get("last_id", 0))}
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return {"last_chapter": 0.0, "last_id": 0}


def save_state(chapter: float, page_id: int) -> None:
    STATE_FILE.write_text(json.dumps({"last_chapter": chapter, "last_id": page_id}))


def link(page_id: int) -> str:
    return f"{BASE}/chapters/{page_id}"


def from_homepage() -> Found | None:
    resp = requests.get(f"{BASE}/?t={int(time.time())}", headers=HEADERS, timeout=20)
    resp.raise_for_status()
    found = [(float(num), int(pid)) for pid, num in LINK_RE.findall(resp.text)]
    return max(found) if found else None


def from_probe(start_id: int) -> Found | None:
    """Check /chapters/<id> for IDs after start_id; IDs are shared by all series."""
    best, misses = None, 0
    for page_id in range(start_id + 1, start_id + 1 + PROBE_MAX):
        resp = requests.get(link(page_id), headers=HEADERS, timeout=20)
        if resp.status_code == 404:
            misses += 1
            if misses >= PROBE_MISSES:
                break
            continue
        resp.raise_for_status()
        misses = 0
        m = TITLE_RE.search(resp.text)
        if m and (best is None or float(m.group(1)) > best[0]):
            best = (float(m.group(1)), page_id)
    return best


def fetch_latest(last_id: int) -> Found | None:
    candidates = []
    try:
        if (home := from_homepage()):
            candidates.append(home)
    except requests.RequestException as e:
        print(f"Homepage check failed: {e}")
    start = max([last_id] + [pid for _, pid in candidates])
    if start:
        if (probed := from_probe(start)):
            candidates.append(probed)
    return max(candidates) if candidates else None


def fmt(ch: float) -> str:
    return str(int(ch)) if ch.is_integer() else str(ch)


def send(title: str, msg: str, click: str = "") -> None:
    print(msg)
    if DISCORD_WEBHOOK_URL:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=15).raise_for_status()
    if NTFY_TOPIC:
        headers = {"Title": title}
        if click:
            headers["Click"] = click
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode(),
                      headers=headers, timeout=15).raise_for_status()


def check(state: dict) -> dict:
    """One check. Returns the (possibly updated) state."""
    result = fetch_latest(state["last_id"])
    if not result:
        raise RuntimeError("No One Piece chapter found (layout changed or blocked?)")
    chapter, page_id = result
    if state["last_chapter"] == 0:
        print(f"Baseline set to chapter {fmt(chapter)} (id {page_id})")
    elif chapter > state["last_chapter"]:
        send("New One Piece chapter",
             f"One Piece Chapter {fmt(chapter)} is out: {link(page_id)}", link(page_id))
    else:
        print(f"No new chapter (latest is {fmt(max(chapter, state['last_chapter']))})")
        if page_id <= state["last_id"]:
            return state
    save_state(max(chapter, state["last_chapter"]), max(page_id, state["last_id"]))
    return load_state()


def main() -> None:
    if "--test" in sys.argv:
        if not (DISCORD_WEBHOOK_URL or NTFY_TOPIC):
            raise RuntimeError("No NTFY_TOPIC or DISCORD_WEBHOOK_URL set")
        send("OPchecker test", "Test: OPchecker notifications are working")
        return

    state = load_state()
    print(f"Last seen chapter: {fmt(state['last_chapter']) if state['last_chapter'] else 'none'}")

    if "--once" in sys.argv:
        check(state)  # errors propagate -> failed run is visible in GitHub
        return

    while True:
        try:
            state = check(state)
        except (requests.RequestException, RuntimeError) as e:
            print(f"Check failed: {e}")
        wait = random.randint(MIN_WAIT, MAX_WAIT)
        print(f"Next check in {wait // 60} min")
        time.sleep(wait)


if __name__ == "__main__":
    main()
