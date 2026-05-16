"""Production-скрипт мониторинга отзывов Delight Rent на Яндекс.Картах.

Парсит embedded JSON из HTML профиля — без Playwright/Chrome.
Раз в сутки ночью, рандомизированные паузы между филиалами 5-15с.
"""
import hashlib
import json
import random
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROFILES = {
    "Москва": "85753359038",
    "Санкт-Петербург": "185819338666",
    "Нижний Новгород": "68143413242",
    "Казань": "157614630550",
    "Ростов-на-Дону": "147776841759",
    "Краснодар": "128271743458",
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

DATA_DIR = Path("/volume1/homes/artemere-7601341/scripts/openclaw/data/reviews")
DB_PATH = DATA_DIR / "reviews.db"
DAILY_DIR_ROOT = DATA_DIR / "daily"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
  review_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  city TEXT NOT NULL,
  firm_id TEXT NOT NULL,
  author TEXT,
  rating INTEGER,
  text TEXT,
  reply TEXT,
  provider TEXT,
  date_posted TEXT,
  date_edited TEXT,
  date_fetched TEXT,
  hash TEXT,
  is_hidden INTEGER DEFAULT 0,
  is_new INTEGER DEFAULT 0,
  is_changed INTEGER DEFAULT 0,
  PRIMARY KEY (platform, review_id)
);
"""


def content_hash(text, reply):
    return hashlib.sha256(((text or "") + "|" + (reply or "")).encode("utf-8")).hexdigest()[:16]


def fetch_profile(profile_id):
    """Скачать HTML профиля Яндекса, извлечь embedded JSON с отзывами."""
    url = f"https://yandex.ru/profile/{profile_id}?lang=ru"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    # Найти массив "reviews":[ ... ]
    m = re.search(r'"reviews":\[', html)
    if not m:
        return []
    start = m.end() - 1
    depth = 0
    in_str = False
    esc = False
    end = None
    for i in range(start, len(html)):
        c = html[i]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if c == '"' and not esc:
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return []
    return json.loads(html[start:end])


def upsert(conn, city, profile_id, rev, mark_baseline=False):
    rid = str(rev["reviewId"])
    text = rev.get("text") or ""
    bc = rev.get("businessComment")
    reply = bc.get("text") if bc else None
    new_hash = content_hash(text, reply)
    fetched = datetime.now(timezone.utc).isoformat()

    row = conn.execute(
        "SELECT hash FROM reviews WHERE platform=? AND review_id=?",
        ("yandex", rid),
    ).fetchone()

    author = (rev.get("author") or {}).get("name")
    date_posted = rev.get("updatedTime")
    date_edited = (bc or {}).get("updatedTime")

    if row is None:
        conn.execute(
            """INSERT INTO reviews
            (review_id, platform, city, firm_id, author, rating, text, reply,
             provider, date_posted, date_edited, date_fetched, hash,
             is_hidden, is_new, is_changed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, "yandex", city, profile_id, author,
             rev.get("rating"), text, reply, "yandex",
             date_posted, date_edited, fetched, new_hash, 0,
             0 if mark_baseline else 1, 0),
        )
        return "baseline" if mark_baseline else "new"
    elif row[0] != new_hash:
        conn.execute(
            """UPDATE reviews SET text=?, reply=?, date_edited=?,
               date_fetched=?, hash=?, is_changed=1
               WHERE platform=? AND review_id=?""",
            (text, reply, date_edited, fetched, new_hash, "yandex", rid),
        )
        return "changed"
    else:
        conn.execute(
            "UPDATE reviews SET date_fetched=? WHERE platform=? AND review_id=?",
            (fetched, "yandex", rid),
        )
        return "unchanged"


def main(mark_baseline=False):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()

    counts = {"new": 0, "changed": 0, "unchanged": 0, "baseline": 0}
    errors = []

    cities = list(PROFILES.items())
    random.shuffle(cities)  # рандомизация порядка для anti-fingerprint

    for i, (city, pid) in enumerate(cities):
        try:
            reviews = fetch_profile(pid)
            for rev in reviews:
                status = upsert(conn, city, pid, rev, mark_baseline)
                counts[status] = counts.get(status, 0) + 1
            print(f"  {city}: {len(reviews)} reviews extracted", flush=True)
        except Exception as e:
            errors.append(f"{city}: {type(e).__name__}: {e}")
            print(f"  {city}: ERROR {e}", flush=True)

        if i < len(cities) - 1:
            pause = random.uniform(5, 13)
            time.sleep(pause)

    conn.commit()

    cur = conn.execute(
        """SELECT review_id, platform, city, author, rating, text, reply,
                  date_posted, date_edited, is_new, is_changed
           FROM reviews
           WHERE platform=? AND (is_new=1 OR is_changed=1) AND is_hidden=0
           ORDER BY date_posted DESC""", ("yandex",))
    cols = [d[0] for d in cur.description]
    new_export = [dict(zip(cols, r)) for r in cur.fetchall()]

    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = DAILY_DIR_ROOT / today
    daily_dir.mkdir(parents=True, exist_ok=True)
    export_path = daily_dir / "reviews_yandex.json"
    export_path.write_text(json.dumps(new_export, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== SUMMARY ===")
    for k, v in counts.items():
        if v > 0:
            print(f"  {k}: {v}")
    print(f"  exported new/changed: {len(new_export)} → {export_path}")
    if errors:
        print(f"  errors: {len(errors)}")
        for e in errors:
            print(f"    {e}")

    conn.close()
    return 1 if errors else 0



KUMA_URL = "http://127.0.0.1:3001/api/push/q8mINw80zFrpCrnq2Uv1"

def kuma_push(status, msg):
    try:
        import urllib.parse, urllib.request
        url = KUMA_URL + "?status=" + status + "&msg=" + urllib.parse.quote(str(msg)[:200]) + "&ping="
        urllib.request.urlopen(url, timeout=6)
    except Exception:
        pass

if __name__ == "__main__":
    baseline_mode = "--baseline" in sys.argv
    try:
        rc = main(mark_baseline=baseline_mode)
        kuma_push("up" if rc == 0 else "down", f"exit={rc}")
        sys.exit(rc)
    except Exception as e:
        kuma_push("down", f"FATAL: {type(e).__name__}: {e}")
        raise
