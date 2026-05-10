#!/usr/bin/env python3
'''
Скачивает transcripts вчерашних YouTube-видео из FreshRSS.

ДВУХСЛОЙНЫЙ FETCH (с 2026-05-09, версия 2 — порядок изменён):
  Слой 1 — NoteGPT API через v2ray прокси с auth state.json (основной).
           Лучше работает с русскими видео (auto-gen ASR), не зависит от
           YT IP-block / PO Token, всегда стабильно если state.json валиден.
  Слой 2 — youtube-transcript-api через v2ray прокси (fallback).
           Используется когда NoteGPT недоступен (auth_expired / transient /
           видео не поддерживается на их стороне).

Кладёт в /volume1/obsidian/Raw/youtube_transcripts/<YYYY-MM-DD>/<channel> - <title>.md
с frontmatter + транскриптом по абзацам с кликабельными таймкодами (раз в 60 сек).

Саммари НЕ генерирует — это делает scheduled task `transcript-summaries-daily`
(Claude Code) после 06:30, читает свежие .md файлы и дописывает русский ## Саммари
в начало каждого видео, выставляет summary_status: done в frontmatter.

Запускается из daily_collect.sh после freshrss_brief.

Кеш: проверка по video_id во всех .md файлах в Raw/youtube_transcripts/ (любая дата).

Exit codes:
  0 — нет транзиентных ошибок (всё ок, или только permanent skips на ОБОИХ слоях)
  1 — реальные транзиентные ошибки на обоих слоях (сеть, прокси)
  2 — NoteGPT auth_expired (state.json протух) — нужен ручной refresh GUI контейнера
'''
import json, os, re, sys, glob
from datetime import date
from pathlib import Path

PROXY = 'http://127.0.0.1:20171'
RAW_ROOT = Path('/volume1/obsidian/Raw/youtube_transcripts')
DAILY_DATA = Path('/volume1/homes/artemere-7601341/scripts/daily_data')

# Постоянные провалы (не алертить, это свойство видео, а не сбой системы)
try:
    from youtube_transcript_api._errors import (
        TranscriptsDisabled, NoTranscriptFound, VideoUnavailable,
    )
    PERMANENT_EXC = (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable)
except ImportError:
    PERMANENT_EXC = ()

PERMANENT_MARKERS = (
    'Subtitles are disabled',
    'No transcripts',
    'no transcripts',
    'Video unavailable',
    'video is no longer available',
    'Video is unavailable',
    'This video is unavailable',
)


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

def fetch_l1_notegpt(video_id):
    '''Слой 1 (основной): NoteGPT API через v2ray прокси с auth state.json.
    Возвращает (segments, lang_code, status):
      status='ok' — успех, segments=[(start_sec, duration_sec, text), ...]
      status='auth_expired' — state.json протух, нужен ручной refresh
      status='no_subs' — у NoteGPT нет транскрипта для этого видео
      status='transient' — сеть, прокси, временное'''
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        import notegpt_fetch as ngf
    except ImportError as e:
        return None, None, 'transient'

    try:
        result = ngf.fetch(video_id)
    except Exception as e:
        return None, None, 'transient'

    code = result.get('code')
    if code == 164003:
        return None, None, 'auth_expired'
    if code != 100000:
        return None, None, 'transient'

    payload = result.get('data') or {}
    transcripts = payload.get('transcripts') or {}
    if not transcripts:
        return None, None, 'no_subs'

    lang_key = next(iter(transcripts.keys()))
    obj = transcripts[lang_key] or {}
    arr = obj.get('default') or obj.get('auto') or []
    if not arr:
        return None, None, 'no_subs'

    def hms_to_sec(ts):
        h, m, s = ts.split(':')
        return int(h) * 3600 + int(m) * 60 + int(s)

    segments = []
    for seg in arr:
        try:
            start = hms_to_sec(seg['start'])
            end = hms_to_sec(seg['end'])
            duration = max(0.1, end - start)
            segments.append((float(start), float(duration), seg['text']))
        except (KeyError, ValueError):
            continue

    # Strip "_auto" suffix from language_code
    lang_raw = payload.get('language_code') or lang_key or 'en'
    if isinstance(lang_raw, list):
        lang_raw = lang_raw[0] if lang_raw else 'en'
    lang = str(lang_raw).replace('_auto', '').replace('-auto', '')
    return segments, lang, 'ok'


def fetch_l2_yt(video_id):
    '''Слой 2 (fallback): youtube-transcript-api через v2ray прокси.
    Используется когда NoteGPT недоступен.
    Возвращает (segments, lang_code, is_permanent_skip).'''
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api.proxies import GenericProxyConfig
    api = YouTubeTranscriptApi(proxy_config=GenericProxyConfig(
        http_url=PROXY, https_url=PROXY))
    try:
        t = api.fetch(video_id, languages=['ru', 'en'])
        segments = [(s.start, s.duration, s.text) for s in t]
        full_text = ' '.join([s[2] for s in segments])
        lang = 'ru' if any(c in full_text[:300] for c in 'абвгдежзийклмнопрстуфхцчшщъыьэюя') else 'en'
        return segments, lang, False
    except PERMANENT_EXC as e:
        return None, str(e).split('\n')[0], True
    except Exception as e:
        msg = str(e)
        is_permanent = any(m in msg for m in PERMANENT_MARKERS)
        return None, msg.split('\n')[0], is_permanent


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
        if start + dur - para_start >= paragraph_seconds:
            paragraphs.append((para_start, ' '.join(current)))
            current = []

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
        text = re.sub(r'\s+', ' ', text).strip()
        out.append(f'**{link}** {text}')

    return '\n\n'.join(out)

def write_md(folder, video_id, title, channel, url, fresh_url, date_published, segments, lang, source='l1'):
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
fetch_source: {source}
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

    fetched_l1 = 0   # NoteGPT
    fetched_l2 = 0   # youtube-transcript-api fallback
    cached = 0
    skipped = 0      # permanent на ОБОИХ слоях
    failed = 0       # transient на ОБОИХ слоях
    auth_expired = False

    for p in youtube_posts:
        vid = extract_video_id(p['tg_url'])
        if not vid:
            print(f'  SKIP cannot extract video_id from {p["tg_url"]}')
            skipped += 1
            continue

        existing = find_existing(vid)
        if existing:
            print(f'  CACHE {vid}: {existing}')
            cached += 1
            continue

        # Слой 1: NoteGPT (основной)
        l1_segments, l1_lang, l1_status = (None, None, 'auth_expired') if auth_expired else fetch_l1_notegpt(vid)
        if l1_status == 'ok':
            path = write_md(folder, vid, p['title'], p['feed'], p['tg_url'],
                            p['fresh_url'], target_date, l1_segments, l1_lang, source='l1-notegpt')
            print(f'  OK[L1-notegpt] {vid}: {len(l1_segments)} segments → {path.name}')
            fetched_l1 += 1
            continue

        if l1_status == 'auth_expired':
            auth_expired = True

        # Слой 2: youtube-transcript-api (fallback)
        l2_segments, l2_lang, l2_permanent = fetch_l2_yt(vid)
        if l2_segments is not None:
            path = write_md(folder, vid, p['title'], p['feed'], p['tg_url'],
                            p['fresh_url'], target_date, l2_segments, l2_lang, source='l2-yt-api')
            print(f'  OK[L2-yt] {vid}: {len(l2_segments)} segments → {path.name}  (L1 {l1_status})')
            fetched_l2 += 1
            continue

        # Оба слоя упали
        l2_msg = l2_lang  # в фейл-бранче lang содержит сообщение об ошибке
        # Permanent если ОБА слоя сказали "no subs / unavailable"
        is_permanent = (l1_status == 'no_subs') and l2_permanent
        if is_permanent:
            print(f'  SKIP[L1+L2 no_subs] {vid}: {l2_msg[:60]}')
            skipped += 1
        else:
            print(f'  FAIL[L1={l1_status} L2={"perm" if l2_permanent else "transient"}] {vid}: {l2_msg[:60]}')
            failed += 1

    total_fetched = fetched_l1 + fetched_l2
    print(f'\nResult: fetched={total_fetched} (l1-notegpt={fetched_l1} l2-yt={fetched_l2}) cached={cached} skipped={skipped} failed={failed}')
    if auth_expired:
        print('NOTE: NoteGPT state.json expired — run GUI container to refresh:')
        print('      cd /volume1/docker/notegpt-pw && docker compose -f compose.yaml up login')
        return 2
    return 0 if failed == 0 else 1

if __name__ == '__main__':
    sys.exit(main())
