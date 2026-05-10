#!/usr/bin/env python3
"""
podcast_transcripts.py — production pipeline для подкастов.

Принимает Spotify-URL эпизода или прямой MP3-URL, делает end-to-end:
  1. Резолв URL → MP3 download URL (для Spotify через oEmbed → simplecast/libsyn).
  2. Скачивание MP3 в /tmp/podcast/<episode_id>.mp3.
  3. Запуск Playwright upload в docker-контейнере notegpt-pw-gui (с включённым diarization).
  4. Получение record_id, потом meta через /api/v2/notes/get-video-by-id.
  5. Скачивание .txt транскрипта с CDN.
  6. Конвертация в Obsidian-md с timestamps + speaker labels.
  7. Сохранение в /volume1/obsidian/Raw/podcast_transcripts/{today}/<podcast> - <episode>.md.

Usage:
  python3 podcast_transcripts.py <spotify_or_mp3_url> [--podcast-name "Имя"] [--episode-title "Название"]

Exit codes:
  0 — success
  1 — auth expired (state.json) — нужен ручной refresh login_in_container.py
  2 — transient (network / NoteGPT busy)
  3 — upload failed
  4 — timeout
  5 — already exists (idempotent skip)
  6 — couldn't resolve URL to MP3
"""
import os, sys, json, re, urllib.request, urllib.parse, subprocess, hashlib
from pathlib import Path
from datetime import date, datetime

# ===== CONFIG =====
STATE_FILE = Path("/volume1/docker/notegpt-pw/state/notegpt-state.json")
PROXY = "http://127.0.0.1:20171"
DOCKER = "/usr/local/bin/docker"
NOTEGPT_IMAGE = "notegpt-pw-gui:latest"
SCRIPTS_DIR = Path("/volume1/docker/notegpt-pw/scripts")
AUDIO_TMP = Path("/volume1/docker/notegpt-pw/audio")
RAW_ROOT = Path("/volume1/obsidian/Raw/podcast_transcripts")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"

AUDIO_TMP.mkdir(parents=True, exist_ok=True)


def setup_proxy():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    urllib.request.install_opener(opener)


def load_cookies():
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return "; ".join(f"{c['name']}={c['value']}" for c in state["cookies"]
                     if "notegpt.io" in (c.get("domain", "") or "").lstrip("."))


def http_get(url, headers=None, timeout=60, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"  [http_get retry {attempt+1}] {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            import time; time.sleep(3)


def resolve_url(input_url):
    """Resolve Spotify episode URL to MP3 + episode metadata.
    Returns (mp3_url, episode_id, episode_title, podcast_name) or raises."""

    # Direct MP3 URL
    if input_url.endswith(".mp3") or ".mp3?" in input_url:
        # Extract a stable hash as episode_id
        eid = hashlib.sha256(input_url.encode()).hexdigest()[:16]
        return input_url, eid, "Unknown Episode", "Unknown Podcast"

    # Spotify episode URL
    spm = re.search(r"open\.spotify\.com/episode/([a-zA-Z0-9]{22})", input_url)
    if spm:
        eid = spm.group(1)
        # 1. oEmbed for title
        oembed = json.loads(http_get(
            f"https://open.spotify.com/oembed?url=https://open.spotify.com/episode/{eid}",
            headers={"User-Agent": UA},
        ))
        title = oembed.get("title", "Unknown Episode")
        # 2. Embed page for MP3 URL
        embed_html = http_get(
            f"https://open.spotify.com/embed/episode/{eid}",
            headers={"User-Agent": UA},
        ).decode("utf-8", errors="ignore")
        mp3_match = re.search(r'(https://[^"\\]*\.mp3[^"\\]*)', embed_html)
        if not mp3_match:
            raise RuntimeError(f"No MP3 URL in Spotify embed for {eid}. Probably Spotify-exclusive episode.")
        mp3_url = mp3_match.group(1).replace("\\u0026", "&").replace("\\/", "/")
        # Try to extract podcast name from RSS feed if URL has feed param
        podcast_name = "Unknown Podcast"
        feed_m = re.search(r"feed=([\w]+)", mp3_url)
        if feed_m:
            try:
                rss = http_get(
                    f"https://feeds.simplecast.com/{feed_m.group(1)}",
                    headers={"User-Agent": UA}, timeout=15,
                ).decode("utf-8", errors="ignore")
                pn = re.search(r"<channel>.*?<title>([^<]+)</title>", rss, re.DOTALL)
                if pn:
                    podcast_name = pn.group(1).strip()
            except Exception as e:
                print(f"  [warn] RSS lookup failed: {e}", file=sys.stderr)
        return mp3_url, eid, title, podcast_name

    raise RuntimeError(f"Unsupported URL format: {input_url}")


def download_mp3(mp3_url, episode_id):
    out_path = AUDIO_TMP / f"{episode_id}.mp3"
    if out_path.exists():
        print(f"[cache] MP3 already exists: {out_path} ({out_path.stat().st_size} bytes)", file=sys.stderr)
        return out_path
    print(f"[dl] {mp3_url[:100]}...", file=sys.stderr)
    data = http_get(mp3_url, headers={"User-Agent": UA}, timeout=180)
    out_path.write_bytes(data)
    print(f"[dl] saved: {out_path} ({len(data)} bytes)", file=sys.stderr)
    return out_path


def notegpt_upload(mp3_path):
    """Run Playwright upload in docker container. Returns record_id or None."""
    print(f"[upload] starting NoteGPT upload via Playwright...", file=sys.stderr)
    cmd = [
        DOCKER, "run", "--rm", "--network=host",
        "-v", f"{SCRIPTS_DIR}:/scripts:ro",
        "-v", "/volume1/docker/notegpt-pw/state:/data/state:ro",
        "-v", f"{AUDIO_TMP}:/data/audio:ro",
        "-e", "NOTEGPT_STATE=/data/state/notegpt-state.json",
        "-e", f"NOTEGPT_PROXY={PROXY}",
        NOTEGPT_IMAGE,
        "python", "/scripts/notegpt_upload.py", f"/data/audio/{mp3_path.name}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    if proc.returncode != 0:
        print(f"[upload] FAIL exit={proc.returncode}", file=sys.stderr)
        print(f"  stderr last lines:\n{proc.stderr[-1000:]}", file=sys.stderr)
        return None, proc.returncode
    # Parse last stdout line as JSON
    last_line = (proc.stdout.strip().split("\n") or [""])[-1]
    try:
        result = json.loads(last_line)
        return result.get("record_id"), 0
    except Exception:
        print(f"[upload] couldn't parse stdout: {proc.stdout[-500:]}", file=sys.stderr)
        return None, 3


def fetch_meta(record_id, cookies):
    headers = {"User-Agent": UA, "Cookie": cookies, "Referer": "https://notegpt.io/"}
    body = http_get(
        f"https://notegpt.io/api/v2/notes/get-video-by-id?video_id={record_id}",
        headers=headers, timeout=60,
    )
    return json.loads(body)


def extract_txt_url(meta):
    extra = meta.get("data", {}).get("extra_data", "{}")
    try:
        extra_d = json.loads(extra)
    except Exception:
        return None
    text_arr = extra_d.get("text", [])
    for t in text_arr:
        link = t.get("data_link")
        if link and link.endswith(".txt"):
            return link
    return None


def fetch_transcript(txt_url):
    body = http_get(txt_url, headers={"User-Agent": UA}, timeout=120)
    return json.loads(body)


def fmt_ts(s):
    s = int(s)
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def convert_to_md(segments, episode_title, podcast_name, episode_url, mp3_url, episode_id, duration):
    # Group adjacent same-speaker segments
    lines = []
    cur_spk = None
    buf = []
    buf_start = None
    for seg in segments:
        spk = seg.get("speaker", "?")
        txt = (seg.get("text", "") or "").strip()
        if not txt:
            continue
        if spk != cur_spk:
            if buf:
                lines.append(f"**[{fmt_ts(buf_start)} — {cur_spk}]** {' '.join(buf)}")
            cur_spk = spk
            buf = [txt]
            buf_start = seg.get("start", 0)
        else:
            buf.append(txt)
    if buf:
        lines.append(f"**[{fmt_ts(buf_start)} — {cur_spk}]** {' '.join(buf)}")

    duration_min = int(duration / 60) if duration else "?"
    md = f"""---
episode_id: {episode_id}
title: "{episode_title.replace('"', "'")}"
podcast: "{podcast_name.replace('"', "'")}"
url: {episode_url}
mp3_url: {mp3_url}
date_published: {date.today().isoformat()}
date_extracted: {date.today().isoformat()}
duration_min: {duration_min}
language: ru
summary_status: pending
fetch_source: notegpt-audio-to-text
---

# {episode_title}

> Подкаст: {podcast_name} · [Открыть оригинал]({episode_url}) · ~{duration_min} мин

## Транскрипт

{chr(10).join(f"{l}{chr(10)}" for l in lines)}
"""
    return md


def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', '', name)[:200]


def main():
    if len(sys.argv) < 2:
        print("Usage: podcast_transcripts.py <spotify_or_mp3_url>", file=sys.stderr)
        return 2
    input_url = sys.argv[1]
    setup_proxy()
    cookies = load_cookies()

    # 1. Resolve URL
    print(f"[1/6] resolving {input_url}", file=sys.stderr)
    try:
        mp3_url, eid, ep_title, pod_name = resolve_url(input_url)
    except Exception as e:
        print(f"ERR resolve: {e}", file=sys.stderr)
        return 6
    print(f"  → episode_id={eid}", file=sys.stderr)
    print(f"  → title={ep_title}", file=sys.stderr)
    print(f"  → podcast={pod_name}", file=sys.stderr)

    # Idempotency check: if file already exists, skip
    out_dir = RAW_ROOT / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(f"{pod_name} - {ep_title}.md")
    out_path = out_dir / safe_name
    if out_path.exists():
        print(f"[skip] already exists: {out_path}", file=sys.stderr)
        return 5

    # 2. Download MP3
    print(f"[2/6] downloading MP3", file=sys.stderr)
    mp3_path = download_mp3(mp3_url, eid)

    # 3. Upload via Playwright
    print(f"[3/6] uploading to NoteGPT", file=sys.stderr)
    record_id, rc = notegpt_upload(mp3_path)
    if not record_id:
        return rc or 3
    print(f"  → record_id={record_id}", file=sys.stderr)

    # 4. Fetch meta
    print(f"[4/6] fetching meta", file=sys.stderr)
    meta = fetch_meta(record_id, cookies)
    txt_url = extract_txt_url(meta)
    if not txt_url:
        print(f"ERR: no .txt URL in meta", file=sys.stderr)
        return 3
    print(f"  → txt_url={txt_url[:100]}...", file=sys.stderr)

    # 5. Fetch transcript
    print(f"[5/6] fetching transcript", file=sys.stderr)
    segments = fetch_transcript(txt_url)
    duration = max((s.get("end", 0) for s in segments), default=0)
    print(f"  → {len(segments)} segments, {duration:.0f}s", file=sys.stderr)

    # 6. Convert + save
    print(f"[6/6] saving to {out_path}", file=sys.stderr)
    md = convert_to_md(segments, ep_title, pod_name, input_url, mp3_url, eid, duration)
    out_path.write_text(md, encoding="utf-8")
    print(f"  → {out_path.stat().st_size} bytes", file=sys.stderr)
    print(json.dumps({"record_id": record_id, "out_path": str(out_path), "segments": len(segments), "duration_sec": int(duration)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
