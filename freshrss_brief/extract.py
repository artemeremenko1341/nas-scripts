#!/usr/bin/env python3
'''
FreshRSS Daily Brief — extract posts for yesterday, group by bucket, save JSON.

Запускается из daily_collect.sh каждое утро 06:00 МСК после остальных коллекторов.
Также можно вручную: python3 extract.py [YYYY-MM-DD]

Output: /volume1/homes/artemere-7601341/scripts/daily_data/{сегодня}/freshrss_brief.json

YouTube publishedAt fix (2026-05-12 v4):
  RSSHub /youtube/playlist/UU<22> ставит pubDate каждого <item> = время генерации feed,
  поэтому в FreshRSS все YT-entries приходят с одинаковым entry.date = "сейчас".
  Чтобы найти РЕАЛЬНО вчерашние видео:
    1) Для каждого уникального YT-feed-URL (вида .../youtube/playlist/UU<22>) выводим
       channel_id (UC<22>) и тянем native YouTube RSS:
       https://www.youtube.com/feeds/videos.xml?channel_id=UC...
       через v2ray прокси. Парсим <published>YYYY-MM-DD...</published> per <entry>.
       1 запрос на канал даёт 15 latest video_id → published date.
    2) Кешируем video_id → ISO-дата в cache/youtube_publish_dates.json.
    3) Для YT entries из FreshRSS-DB смотрим расширенное окно entry.date >= target - 7 дней,
       резолвим через кеш (или через video-page fallback если нет в кеше).
    4) В bucket 'youtube' попадают только entries, где resolved date == target.
  Не-YT feed'ы (Telegram, native RSS) обрабатываются по-старому через SQL.
'''
import sqlite3, json, html, re, sys, os, subprocess, time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from urllib.request import urlopen, Request, ProxyHandler, build_opener
from urllib.error import URLError, HTTPError

DB_SRC_CONTAINER = 'freshrss-freshrss-1:/var/www/FreshRSS/data/users/artemere/db.sqlite'
DB_TMP = '/tmp/freshrss_db.sqlite'
DOCKER = '/var/packages/ContainerManager/target/usr/bin/docker'
FRESH_BASE = 'https://feed.artemere.com/i/'

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, 'cache')
PUB_CACHE_FILE = os.path.join(CACHE_DIR, 'youtube_publish_dates.json')
V2RAY_PROXY = 'http://127.0.0.1:20171'
YT_RESOLVE_TIMEOUT = 20
YT_CHANNEL_THROTTLE = 1.0  # секунд между запросами channel-RSS (47 фидов = ~50 сек)
YT_WINDOW_DAYS = 7

# Bucket mapping — Артём правит при необходимости
BUCKETS = {
    'noisy': {'Медуза — LIVE', 'Труха⚡️Україна'},
    'finance': {'Топор. Экономика.', 'bcc invest', 'Spydell_finance', 'Влад | Про деньги',
                'НЕБАФФЕТТ', 'Грязь, долги и инвестиции | Сергей Таболин'},
    'dubai_realty': {'Новости Дубай', 'Записки инвестора. Недвижимость Дубай- Абу-Даби -Москва',
                     'Макаров. Про недвижимость', 'Михаил Боровлянский | Про недвижимость',
                     'М2коммерц | Центр доходной недвижимости', 'Новостройка по расчету',
                     'Пятёрочка Франчайзинг',
                     'Аверс.PRO недвижимость в Эмиратах и мире',
                     'Недвижимость и бизнес в Абу-Даби с Ольгой Дыдыко'},
    'realty_ru': {'Максим Иванов | Доходная недвижимость',
                  'Твой Склад |Путь развития 》'},
    'ai_biz': {'ОСНОВАТЕЛИ', 'Denis Sexy IT 🤖',
               'Ppprompt | Sexy AI Prompts & Experiments | by @ponchiknews',
               'Никитствование', 'Рустам Агамалиев: "нечтение" и заметковедение',
               'Вениамин Кизеев',
               '[[PRO Obsidian]]', 'Сергей Булаев AI 🤖', 'Силиконовый Мешок'},
    'cinema': {'КиноЗавод_рентал/продакшн', 'ЗУМ ПРОКАТ МСК | ZOOM PROKAT MSK',
               'ДиЛайт Рент', 'Александр Роднянский',
               'KINOARENDA Rental | Moscow', 'KINOARENDA Rental | SPb'},
    'rental': {'RENTALL | Аренда видео и фототехники | Москва | СПб'},
    'school': {'Школа «Летово»', 'Школа «Летово Джуниор»'},
    'politics': {'Vladimir Pastukhov', 'Politics with Vladimir Pastukhov', 'ПАРТИЯ ЯБЛОКО',
                 'Гражданская инициатива', '🇷🇺 BafistaRU 🇷🇺', 'Новая газета'},
    'clubs': {'LANGAME.ru | Про компьютерные клубы',
              'LANGAME Software | Для компьютерных клубов',
              'COLIZEUM | Бизнес',
              'SmartShell | Всё для компьютерных клубов'},
    'culture': {'ПРОТЕАТР'},
    'lifestyle': {'СберПремьер'},
}

VIDEO_ID_RE = re.compile(r'(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})')
RSSHUB_PLAYLIST_RE = re.compile(r'/youtube/playlist/UU([A-Za-z0-9_-]{22})')
# Native YT RSS URL
RSSHUB_CHANNEL_NATIVE_RE = re.compile(r'channel_id=UC([A-Za-z0-9_-]{22})')
ATOM_NS = '{http://www.w3.org/2005/Atom}'
YT_NS = '{http://www.youtube.com/xml/schemas/2015}'


def normalize_feed(name):
    name = html.unescape(name)
    return name.replace(' - Telegram Channel', '').replace(' - YouTube', ' (YT)')


def is_youtube_feed(feed_url):
    if not feed_url:
        return False
    return ('/youtube/' in feed_url) or ('youtube.com/feeds' in feed_url)


def feed_url_to_channel_id(feed_url):
    '''Извлечь UC-channel-id из feed URL (RSSHub playlist UU<22> или native YT RSS).'''
    if not feed_url:
        return None
    m = RSSHUB_PLAYLIST_RE.search(feed_url)
    if m:
        return 'UC' + m.group(1)
    m = RSSHUB_CHANNEL_NATIVE_RE.search(feed_url)
    if m:
        return 'UC' + m.group(1)
    return None


def bucket_of(feed, feed_url='', category_id=None):
    if is_youtube_feed(feed_url):
        return 'youtube'
    if category_id == 11:
        return 'podcasts'
    for b, s in BUCKETS.items():
        if feed in s:
            return b
    return 'other'


def clean(s):
    if not s:
        return ''
    s = html.unescape(s)
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def extract_video_id(url):
    if not url:
        return None
    m = VIDEO_ID_RE.search(url)
    return m.group(1) if m else None


def load_pub_cache():
    if not os.path.exists(PUB_CACHE_FILE):
        return {}
    try:
        with open(PUB_CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_pub_cache(cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = PUB_CACHE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, PUB_CACHE_FILE)


def fetch_channel_rss(channel_id):
    '''Тянем native YouTube channel RSS через v2ray. Возвращает {video_id: "YYYY-MM-DD", ...}
    для 15 latest видео канала. None если fetch упал.'''
    url = f'https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}'
    proxy_handler = ProxyHandler({'http': V2RAY_PROXY, 'https': V2RAY_PROXY})
    opener = build_opener(proxy_handler)
    req = Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
        'Accept': 'application/atom+xml,application/xml',
    })
    try:
        with opener.open(req, timeout=YT_RESOLVE_TIMEOUT) as resp:
            body = resp.read()
    except (URLError, HTTPError, TimeoutError, Exception) as e:
        print(f'  CHANNEL-ERR {channel_id}: {type(e).__name__}: {e}')
        return None

    if not body:
        return None

    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        print(f'  CHANNEL-PARSE-ERR {channel_id}: {e}')
        return None

    out = {}
    for entry in root.findall(f'{ATOM_NS}entry'):
        vid_el = entry.find(f'{YT_NS}videoId')
        pub_el = entry.find(f'{ATOM_NS}published')
        if vid_el is None or pub_el is None:
            continue
        vid = (vid_el.text or '').strip()
        pub = (pub_el.text or '').strip()
        if not vid or not pub:
            continue
        date_part = pub[:10]
        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_part):
            out[vid] = date_part
    return out


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else (date.today() - timedelta(days=1)).isoformat()
    out_dir = f'/volume1/homes/artemere-7601341/scripts/daily_data/{date.today().isoformat()}'
    os.makedirs(out_dir, exist_ok=True)
    out_path = f'{out_dir}/freshrss_brief.json'

    subprocess.run([DOCKER, 'cp', DB_SRC_CONTAINER, DB_TMP], check=True)
    c = sqlite3.connect(DB_TMP).cursor()

    pub_cache = load_pub_cache()
    pre_run_cache_size = len(pub_cache)

    # ===== ШАГ 1: Тянем native YouTube RSS для каждого уникального канала =====
    yt_feeds = list(c.execute('''
        SELECT DISTINCT f.id, f.url FROM feed f
        WHERE f.url LIKE '%/youtube/%' OR f.url LIKE '%youtube.com/feeds%'
    '''))
    print(f'YT-prefetch: {len(yt_feeds)} unique YT-feed URLs')

    channels_fetched = 0
    channels_failed = 0
    new_resolves = 0
    for feed_id, feed_url in yt_feeds:
        ch_id = feed_url_to_channel_id(feed_url)
        if not ch_id:
            print(f'  CHANNEL-SKIP feed_id={feed_id} url={feed_url[:80]} — no channel_id extracted')
            continue
        result = fetch_channel_rss(ch_id)
        if result is None:
            channels_failed += 1
            time.sleep(YT_CHANNEL_THROTTLE)
            continue
        channels_fetched += 1
        added = 0
        for vid, pub_date in result.items():
            if vid not in pub_cache or not pub_cache[vid]:
                pub_cache[vid] = pub_date
                new_resolves += 1
                added += 1
        print(f'  CHANNEL {ch_id} -> {len(result)} entries, {added} new')
        # Сохраняем по каждому каналу — дёшево.
        save_pub_cache(pub_cache)
        time.sleep(YT_CHANNEL_THROTTLE)

    print(f'YT-prefetch done: {channels_fetched} channels OK, {channels_failed} failed, {new_resolves} new resolves, cache total {len(pub_cache)} (was {pre_run_cache_size})')

    # ===== ШАГ 2: Не-YT feed'ы — старый запрос по точной target-дате =====
    q_nonyt = '''SELECT e.id, e.id_feed, f.name, e.title, e.content, e.link, f.url, f.category
                 FROM entry e JOIN feed f ON f.id = e.id_feed
                 WHERE date(e.date,"unixepoch","+3 hours") = ?
                   AND NOT (f.url LIKE '%/youtube/%' OR f.url LIKE '%youtube.com/feeds%')
                 ORDER BY f.name, e.date DESC'''

    # ===== ШАГ 3: YT entries — расширенное окно, фильтруем по кешу =====
    q_yt = '''SELECT e.id, e.id_feed, f.name, e.title, e.content, e.link, f.url, f.category
              FROM entry e JOIN feed f ON f.id = e.id_feed
              WHERE date(e.date,"unixepoch","+3 hours") >= ?
                AND (f.url LIKE '%/youtube/%' OR f.url LIKE '%youtube.com/feeds%')
              ORDER BY f.name, e.date DESC'''

    yt_window_start = (date.fromisoformat(target) - timedelta(days=YT_WINDOW_DAYS)).isoformat()

    buckets = {}
    feeds_seen = set()
    total = 0

    # --- Non-YT entries ---
    for r in c.execute(q_nonyt, (target,)):
        feed = normalize_feed(r[2])
        feeds_seen.add(feed)
        b = bucket_of(feed, r[6], category_id=r[7])
        post = {
            'feed': feed,
            'feed_id': r[1],
            'entry_id': r[0],
            'title': r[3] or '',
            'content': clean(r[4])[:1500] if b != 'noisy' else '',
            'tg_url': r[5],
            'fresh_url': f'{FRESH_BASE}?get=f_{r[1]}&state=15&cid={r[0]}#flux_{r[0]}',
        }
        buckets.setdefault(b, []).append(post)
        total += 1

    # --- YT entries with resolved publish date from cache ---
    yt_candidates = list(c.execute(q_yt, (yt_window_start,)))
    yt_in_target = 0
    yt_no_cache = 0
    yt_no_vid = 0
    yt_dedup_skipped = 0
    seen_vids_in_bucket = set()

    for r in yt_candidates:
        entry_id, feed_id, fname, title, content, link, furl, cat = r
        feed = normalize_feed(fname)

        vid = extract_video_id(link)
        if not vid:
            yt_no_vid += 1
            continue

        resolved = pub_cache.get(vid)
        if resolved is None or resolved == '':
            # Не нашли в кеше (либо канал старый, либо видео >15 латест) — пропускаем.
            yt_no_cache += 1
            continue

        if resolved == target:
            # Дедуп: FreshRSS иногда имеет 2 entries для одного video_id (разные titles при truncation).
            if vid in seen_vids_in_bucket:
                yt_dedup_skipped += 1
                continue
            seen_vids_in_bucket.add(vid)

            feeds_seen.add(feed)
            post = {
                'feed': feed,
                'feed_id': feed_id,
                'entry_id': entry_id,
                'title': title or '',
                'content': clean(content)[:1500],
                'tg_url': link,
                'fresh_url': f'{FRESH_BASE}?get=f_{feed_id}&state=15&cid={entry_id}#flux_{entry_id}',
            }
            buckets.setdefault('youtube', []).append(post)
            total += 1
            yt_in_target += 1

    print(f'YT-bucket: {len(yt_candidates)} candidates, {yt_in_target} match target={target}, {yt_dedup_skipped} dedup-skipped, {yt_no_cache} not in cache, {yt_no_vid} no video_id')

    # Финальный save
    save_pub_cache(pub_cache)

    # Детекция неклассифицированных каналов
    classified = set()
    for s in BUCKETS.values():
        classified.update(s)
    unclassified = sorted({p['feed'] for p in buckets.get('other', [])} - classified)

    result_obj = {
        'date': target,
        'total': total,
        'feeds': len(feeds_seen),
        'unclassified_feeds': unclassified,
        'buckets': buckets,
    }
    with open(out_path, 'w') as f:
        json.dump(result_obj, f, ensure_ascii=False, indent=1)
    os.remove(DB_TMP)
    print(f'OK: {total} posts from {len(feeds_seen)} feeds, target={target}, -> {out_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
