#!/usr/bin/env python3
"""
podcast_batch.py — пакетная обработка подкастов из FreshRSS (категория Podcasts, id=11).

Читает entries за вчера через docker cp + sqlite3 (как extract.py).
Для каждой записи парсит attributes.enclosures[0].url и вызывает podcast_transcripts.py.

Usage:
  python3 podcast_batch.py [YYYY-MM-DD]   # default = вчера

Exit codes:
  0 — все обработано (или нечего обрабатывать)
  1 — частичный fail
  2 — auth_expired у NoteGPT
"""
import sys, json, sqlite3, subprocess
from pathlib import Path
from datetime import date, timedelta

DB_SRC_CONTAINER = 'freshrss-freshrss-1:/var/www/FreshRSS/data/users/artemere/db.sqlite'
DB_TMP = '/tmp/freshrss_db_podcasts.sqlite'
DOCKER = '/usr/local/bin/docker'
PODCAST_SCRIPT = '/volume1/homes/artemere-7601341/scripts/freshrss_brief/podcast_transcripts.py'
PODCAST_CATEGORY_ID = 11


def fetch_yesterday_episodes(target):
    """Returns list of (feed_name, title, mp3_url, link)."""
    subprocess.run([DOCKER, 'cp', DB_SRC_CONTAINER, DB_TMP], check=True)
    c = sqlite3.connect(DB_TMP).cursor()
    rows = c.execute('''
        SELECT f.name, e.title, e.link, e.attributes
        FROM entry e JOIN feed f ON f.id = e.id_feed
        WHERE f.category = ?
          AND date(e.date, "unixepoch", "+3 hours") = ?
        ORDER BY e.date DESC
    ''', (PODCAST_CATEGORY_ID, target)).fetchall()
    Path(DB_TMP).unlink(missing_ok=True)

    out = []
    for feed_name, title, link, attrs_json in rows:
        try:
            attrs = json.loads(attrs_json or '{}')
        except Exception:
            continue
        encs = attrs.get('enclosures') or []
        if not encs:
            continue
        mp3_url = encs[0].get('url')
        if not mp3_url:
            continue
        # Decode HTML entities in URL (& → &)
        import html as html_mod
        mp3_url = html_mod.unescape(mp3_url)
        # Filter by audio type if available
        ftype = (encs[0].get('type') or '').lower()
        if ftype and 'audio' not in ftype:
            continue
        out.append((feed_name, title, mp3_url, link))
    return out


def process_episode(feed_name, title, mp3_url):
    print(f'  [exec] {feed_name} → {title[:60]}', file=sys.stderr)
    proc = subprocess.run(
        ['python3', PODCAST_SCRIPT, mp3_url],
        capture_output=True, text=True, timeout=1500,
    )
    if proc.returncode == 0:
        print(f'  [ok] {title[:60]}', file=sys.stderr)
        return True, 0
    if proc.returncode == 5:
        print(f'  [skip] already exists: {title[:60]}', file=sys.stderr)
        return True, 5
    print(f'  [fail exit={proc.returncode}] {title[:60]}', file=sys.stderr)
    print(f'  stderr tail: {proc.stderr[-400:]}', file=sys.stderr)
    return False, proc.returncode


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else (date.today() - timedelta(days=1)).isoformat()
    eps = fetch_yesterday_episodes(target)
    print(f'[batch] target={target}, {len(eps)} podcast episode(s) to process', file=sys.stderr)

    if not eps:
        return 0

    failed = 0
    auth_expired = False
    for feed_name, title, mp3_url, _ in eps:
        ok, rc = process_episode(feed_name, title, mp3_url)
        if not ok:
            failed += 1
            if rc == 1:
                auth_expired = True

    print(f'[done] processed={len(eps)}, failed={failed}, auth_expired={auth_expired}', file=sys.stderr)
    if auth_expired:
        return 2
    return 1 if failed > 0 else 0


if __name__ == '__main__':
    sys.exit(main())
