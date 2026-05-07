#!/usr/bin/env python3
'''
Скачивает transcripts вчерашних YouTube-видео из FreshRSS.
Кладёт в /volume1/obsidian/Raw/youtube_transcripts/<YYYY-MM-DD>/<channel> - <title>.md
с форматированными по абзацам субтитрами + кликабельные таймкоды (раз в 60 сек).

Запускается из daily_collect.sh после freshrss_brief.

Кеш: проверка по video_id во всех .md файлах в Raw/youtube_transcripts/ (любая дата).
'''
import json, os, re, sys, glob
from datetime import date
from pathlib import Path

PROXY = 'http://127.0.0.1:20171'
RAW_ROOT = Path('/volume1/obsidian/Raw/youtube_transcripts')
DAILY_DATA = Path('/volume1/homes/artemere-7601341/scripts/daily_data')

def extract_video_id(url):
    m = re.search(r'(?:v=|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})', url)
    return m.group(1) if m else None

def sanitize_filename(s):
    s = re.sub(r'[<>:"/\|?*]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:150]

def find_existing(video_id):
    for f in glob.glob(str(RAW_ROOT / '**' / '*.md'), recursive=True):
        try:
            with open(f, encoding='utf-8') as fp:
                head = fp.read(500)
            if f'video_id: {video_id}' in head:
                return f
        except Exception:
            pass
    return None

def fetch_transcript(video_id):
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api.proxies import GenericProxyConfig
    api = YouTubeTranscriptApi(proxy_config=GenericProxyConfig(
        http_url=PROXY, https_url=PROXY))
    try:
        t = api.fetch(video_id, languages=['ru', 'en'])
        segments = [(s.start, s.duration, s.text) for s in t]
        full_text = ' '.join([s[2] for s in segments])
        lang = 'ru' if any(c in full_text[:300] for c in 'абвгдежзийклмнопрстуфхцчшщъыьэюя') else 'en'
        return segments, lang
    except Exception as e:
        return None, str(e)

def format_paragraph_block(segments, video_id, paragraph_seconds=60):
    '''Группирует segments в абзацы примерно paragraph_seconds длинной.
    Ставит кликабельный timestamp в начало каждого абзаца.'''
    if not segments:
        return ''
    paragraphs = []
    current = []
    para_start = segments[0][0]

    for start, dur, text in segments:
        if not current:
            para_start = start
        current.append(text.strip())
        # закрываем абзац когда прошло >= paragraph_seconds
        if start + dur - para_start >= paragraph_seconds:
            paragraphs.append((para_start, ' '.join(current)))
            current = []

    # Последний хвост
    if current:
        paragraphs.append((para_start, ' '.join(current)))

    out = []
    for ts, text in paragraphs:
        h = int(ts // 3600)
        m = int((ts % 3600) // 60)
        s = int(ts % 60)
        if h > 0:
            label = f'{h}:{m:02d}:{s:02d}'
        else:
            label = f'{m:02d}:{s:02d}'
        ts_int = int(ts)
        link = f'[{label}](https://www.youtube.com/watch?v={video_id}&t={ts_int}s)'
        # очистка лишних пробелов внутри
        text = re.sub(r'\s+', ' ', text).strip()
        out.append(f'**{link}** {text}')

    return '\n\n'.join(out)

def write_md(folder, video_id, title, channel, url, fresh_url, date_published, segments, lang):
    folder.mkdir(parents=True, exist_ok=True)
    safe_channel = sanitize_filename(channel)
    safe_title = sanitize_filename(title)
    filename = f'{safe_channel} - {safe_title}.md'[:240]
    path = folder / filename

    formatted = format_paragraph_block(segments, video_id)
    char_count = sum(len(s[2]) for s in segments)

    title_q = title.replace('"', "'")
    channel_q = channel.replace('"', "'")

    content = f'''---
video_id: {video_id}
title: "{title_q}"
channel: "{channel_q}"
url: {url}
fresh_url: {fresh_url}
date_published: {date_published}
date_extracted: {date.today().isoformat()}
duration_chars: {char_count}
language: {lang}
summary_status: pending
---

# {title}

> Канал: {channel} · [Открыть в FreshRSS]({fresh_url}) · [Открыть на YouTube]({url})

## Транскрипт

{formatted}
'''
    path.write_text(content, encoding='utf-8')
    return path

def main():
    today = date.today().isoformat()
    brief_path = DAILY_DATA / today / 'freshrss_brief.json'
    if not brief_path.exists():
        print(f'NO freshrss_brief.json for {today}')
        return 1

    data = json.load(open(brief_path))
    youtube_posts = data.get('buckets', {}).get('youtube', [])
    if not youtube_posts:
        print('No YouTube videos yesterday')
        return 0

    target_date = data.get('date', today)
    folder = RAW_ROOT / target_date

    fetched = 0
    cached = 0
    failed = 0
    for p in youtube_posts:
        vid = extract_video_id(p['tg_url'])
        if not vid:
            print(f'  SKIP: cannot extract video_id from {p["tg_url"]}')
            failed += 1
            continue

        existing = find_existing(vid)
        if existing:
            print(f'  CACHE: {vid} → {existing}')
            cached += 1
            continue

        segments, lang_or_err = fetch_transcript(vid)
        if segments is None:
            print(f'  FAIL {vid}: {lang_or_err}')
            failed += 1
            continue

        path = write_md(folder, vid, p['title'], p['feed'], p['tg_url'],
                        p['fresh_url'], target_date, segments, lang_or_err)
        print(f'  OK {vid}: {len(segments)} segments → {path.name}')
        fetched += 1

    print(f'\nResult: fetched={fetched} cached={cached} failed={failed}')
    return 0 if failed == 0 else 1

if __name__ == '__main__':
    sys.exit(main())
