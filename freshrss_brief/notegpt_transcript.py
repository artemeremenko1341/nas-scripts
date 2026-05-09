#!/usr/bin/env python3
'''
NoteGPT fallback transcript fetcher.

Используется когда youtube-transcript-api возвращает IpBlocked / 429 / PO Token error.
Берёт cookies (включая HttpOnly auth) из ~/.config/notegpt-cookies.json в формате
Cookie-Editor (массив объектов с полями name/value/domain).

Endpoint: GET https://notegpt.io/api/v2/video-transcript?platform=youtube&video_id=<id>
Без кастомных headers, только Cookie + UA + Referer.
Возвращает 3 уровня детализации в data.transcripts.<lang>_auto: default / auto / custom.
Для пайплайна используем default (макс. сегментов), группируем в параграфы 60s.

Exit codes:
  0 — успех или permanent skip (нет субтитров у видео)
  1 — auth истёк (cookies протухли) — нужен ручной refresh
  2 — сетевая / транзиентная ошибка
'''
import json, sys, re
from pathlib import Path
import urllib.request, urllib.error

COOKIES_FILE = Path.home() / '.config' / 'notegpt-cookies.json'
NOTEGPT_API = 'https://notegpt.io/api/v2/video-transcript'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

def load_cookie_header():
    if not COOKIES_FILE.exists():
        raise FileNotFoundError(f'cookies file missing: {COOKIES_FILE}. Export from Cookie-Editor → JSON.')
    raw = json.loads(COOKIES_FILE.read_text(encoding='utf-8'))
    pairs = []
    for c in raw:
        name = c.get('name')
        val = c.get('value')
        if name and val is not None:
            pairs.append(f'{name}={val}')
    if not pairs:
        raise ValueError(f'cookies file empty / wrong format: {COOKIES_FILE}')
    return '; '.join(pairs)

def fetch(video_id, cookie_header=None):
    if cookie_header is None:
        cookie_header = load_cookie_header()
    url = f'{NOTEGPT_API}?platform=youtube&video_id={video_id}'
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Cookie': cookie_header,
        'Referer': 'https://notegpt.io/youtube-to-transcript',
        'Accept': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return {'error': 'http', 'status': e.code, 'msg': str(e)}
    except Exception as e:
        return {'error': 'transient', 'msg': str(e)}
    code = data.get('code')
    if code == 164003:
        return {'error': 'auth_expired', 'msg': data.get('message')}
    if code != 100000:
        return {'error': 'api', 'code': code, 'msg': data.get('message')}
    payload = data.get('data') or {}
    return {'ok': True, 'data': payload}

def parse_segments(payload, prefer='default'):
    '''Returns list of (start_sec, end_sec, text). Picks first lang in transcripts.'''
    transcripts = payload.get('transcripts') or {}
    if not transcripts:
        return None, None
    lang_code = next(iter(transcripts.keys()))
    obj = transcripts[lang_code] or {}
    arr = obj.get(prefer) or obj.get('default') or obj.get('auto') or []
    out = []
    def to_sec(ts):
        h, m, s = ts.split(':')
        return int(h)*3600 + int(m)*60 + int(s)
    for seg in arr:
        try:
            out.append((to_sec(seg['start']), to_sec(seg['end']), seg['text']))
        except (KeyError, ValueError):
            continue
    return lang_code, out

def main():
    if len(sys.argv) < 2:
        print('Usage: notegpt_transcript.py <video_id>')
        return 2
    vid = sys.argv[1]
    res = fetch(vid)
    if not res.get('ok'):
        err = res.get('error', 'unknown')
        msg = res.get('msg', '')
        print(f'FAIL [{err}]: {msg}', file=sys.stderr)
        if err == 'auth_expired':
            return 1
        return 2
    payload = res['data']
    info = payload.get('videoInfo') or {}
    lang, segs = parse_segments(payload)
    print(f'OK video_id={vid}')
    print(f'  title: {info.get("name","")[:80]}')
    print(f'  duration: {info.get("duration")} sec')
    print(f'  lang: {lang}')
    print(f'  segments: {len(segs) if segs else 0}')
    if segs:
        print(f'  first: {segs[0]}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
