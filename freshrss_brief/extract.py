#!/usr/bin/env python3
'''
FreshRSS Daily Brief — extract posts for yesterday, group by bucket, save JSON.

Запускается из daily_collect.sh каждое утро 06:00 МСК после остальных коллекторов.
Также можно вручную: python3 extract.py [YYYY-MM-DD]

Output: /volume1/homes/artemere-7601341/scripts/daily_data/{сегодня}/freshrss_brief.json
Структура:
  {
    'date': '2026-05-05',
    'total': 185,
    'feeds': 36,
    'buckets': {
      'noisy': [...],
      'finance': [...],
      ...
    }
  }
Каждый пост: {feed, feed_id, entry_id, title, content (truncated 1500), tg_url, fresh_url}
'''
import sqlite3, json, html, re, sys, os, subprocess
from datetime import date, timedelta

DB_SRC_CONTAINER = 'freshrss-freshrss-1:/var/www/FreshRSS/data/users/artemere/db.sqlite'
DB_TMP = '/tmp/freshrss_db.sqlite'
DOCKER = '/var/packages/ContainerManager/target/usr/bin/docker'
FRESH_BASE = 'https://feed.artemere.com/i/'

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

def normalize_feed(name):
    name = html.unescape(name)
    return name.replace(' - Telegram Channel','').replace(' - YouTube',' (YT)')

def bucket_of(feed, feed_url='', category_id=None):
    # YouTube override - любой канал из RSSHub /youtube/ или нативного YouTube RSS
    if '/youtube/' in feed_url or 'youtube.com/feeds' in feed_url:
        return 'youtube'
    # Podcasts override - всё в категории FreshRSS "Podcasts" (id=11)
    if category_id == 11:
        return 'podcasts'
    for b, s in BUCKETS.items():
        if feed in s:
            return b
    return 'other'

def clean(s):
    if not s: return ''
    s = html.unescape(s)
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def main():
    target = sys.argv[1] if len(sys.argv) > 1 else (date.today() - timedelta(days=1)).isoformat()
    out_dir = f'/volume1/homes/artemere-7601341/scripts/daily_data/{date.today().isoformat()}'
    os.makedirs(out_dir, exist_ok=True)
    out_path = f'{out_dir}/freshrss_brief.json'

    subprocess.run([DOCKER, 'cp', DB_SRC_CONTAINER, DB_TMP], check=True)
    c = sqlite3.connect(DB_TMP).cursor()

    q = '''SELECT e.id, e.id_feed, f.name, e.title, e.content, e.link, f.url, f.category
           FROM entry e JOIN feed f ON f.id = e.id_feed
           WHERE date(e.date,"unixepoch","+3 hours") = ?
           ORDER BY f.name, e.date DESC'''

    buckets = {}
    feeds_seen = set()
    total = 0
    for r in c.execute(q, (target,)):
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

    # Детекция неклассифицированных каналов
    classified = set()
    for s in BUCKETS.values():
        classified.update(s)
    unclassified = sorted({p['feed'] for p in buckets.get('other', [])} - classified)

    result = {
        'date': target,
        'total': total,
        'feeds': len(feeds_seen),
        'unclassified_feeds': unclassified,
        'buckets': buckets,
    }
    with open(out_path, 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    os.remove(DB_TMP)
    print(f'OK: {total} posts from {len(feeds_seen)} feeds, target={target}, → {out_path}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
