#!/usr/bin/env python3
"""
NoteGPT transcript fetcher.

Read cookies (incl HttpOnly) from Playwright storage_state.json, build a Cookie
header, fetch the API with stdlib urllib (forces IPv4 to dodge slow IPv6 path).

Usage:
  python3 notegpt_fetch.py <video_id>

Env:
  NOTEGPT_STATE=/path/to/state.json   (default: ~/.config/notegpt-state.json)

Exit codes:
  0 — success / permanent skip
  1 — auth_expired (state stale, refresh on laptop)
  2 — transient (network)
"""
import os, sys, json, socket, urllib.request, urllib.parse
from pathlib import Path

STATE_FILE = Path(os.environ.get(
    "NOTEGPT_STATE",
    "/volume1/docker/notegpt-pw/state/notegpt-state.json",
))
PROXY = os.environ.get("NOTEGPT_PROXY", "http://127.0.0.1:20171")
NOTEGPT_API = "https://notegpt.io/api/v2/video-transcript"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

# Setup proxy at module level (login + all subsequent fetch must share IP — use v2ray exit)
if PROXY:
    proxy_handler = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    opener = urllib.request.build_opener(proxy_handler)
    urllib.request.install_opener(opener)

def load_cookie_header():
    if not STATE_FILE.exists():
        raise FileNotFoundError(f"missing {STATE_FILE}")
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    pairs = []
    for c in state.get("cookies", []):
        domain = (c.get("domain") or "").lstrip(".")
        if "notegpt.io" not in domain:
            continue
        name = c.get("name")
        val = c.get("value")
        if name and val is not None:
            pairs.append(f"{name}={val}")
    if not pairs:
        raise ValueError(f"no notegpt.io cookies in {STATE_FILE}")
    return "; ".join(pairs)

def fetch(video_id: str) -> dict:
    cookie = load_cookie_header()
    qs = urllib.parse.urlencode({"platform": "youtube", "video_id": video_id})
    url = f"{NOTEGPT_API}?{qs}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Cookie": cookie,
        "Referer": "https://notegpt.io/youtube-to-transcript",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data

def parse_segments(data: dict, prefer: str = "default"):
    payload = data.get("data") or {}
    transcripts = payload.get("transcripts") or {}
    if not transcripts:
        return None, None, None
    lang_key = next(iter(transcripts.keys()))
    obj = transcripts[lang_key] or {}
    arr = obj.get(prefer) or obj.get("default") or obj.get("auto") or []
    return lang_key, arr, payload.get("videoInfo") or {}

def main():
    if len(sys.argv) < 2:
        print("Usage: notegpt_fetch.py <video_id>", file=sys.stderr)
        return 2
    vid = sys.argv[1]
    try:
        result = fetch(vid)
    except Exception as e:
        print(f"FAIL transient: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return 2
    code = result.get("code")
    if code == 164003:
        print("FAIL auth_expired: state.json expired — refresh on laptop", file=sys.stderr)
        return 1
    if code != 100000:
        print(f"FAIL api: code={code} msg={result.get('message')}", file=sys.stderr)
        return 2
    lang, segs, info = parse_segments(result)
    print(f"OK video_id={vid}")
    print(f"  title:    {(info.get('name') or '')[:80]}")
    print(f"  duration: {info.get('duration')} sec")
    print(f"  channel:  {info.get('author')}")
    print(f"  lang:     {lang}")
    print(f"  segments: {len(segs) if segs else 0}")
    if segs:
        print(f"  first:    {segs[0]}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
