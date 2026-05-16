"""Production-скрипт мониторинга отзывов Delight Rent на 2ГИС.
Без внешних зависимостей — только stdlib (urllib, json, sqlite3).
"""
import hashlib
import json
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BRANCHES = {
    "Москва": "70000001067116654",
    "Санкт-Петербург": "70000001038284508",
    "Нижний Новгород": "70000001039462911",
    "Казань": "70000001043874389",
    "Ростов-на-Дону": "70000001057055653",
    "Краснодар": "70000001061039355",
}

API_KEY = "6e7e1929-4ea9-4a5d-8c05-d601860389bd"
API_URL = "https://public-api.reviews.2gis.com/3.0/branches/{firm_id}/reviews"

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
CREATE INDEX IF NOT EXISTS idx_reviews_city ON reviews(city);
CREATE INDEX IF NOT EXISTS idx_reviews_fetched ON reviews(date_fetched);
"""


def content_hash(text, reply):
    return hashlib.sha256(((text or "") + "|" + (reply or "")).encode("utf-8")).hexdigest()[:16]


def fetch_branch(firm_id):
    params = {
        "limit": "50",
        "offset": "0",
        "fields": "meta.total_count,reviews.hiding_reason",
        "sort_by": "date_created",
        "key": API_KEY,
        "locale": "ru_RU",
    }
    url = API_URL.format(firm_id=firm_id) + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Origin": "https://2gis.ru",
        "Referer": "https://2gis.ru/",
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data.get("reviews", [])


def upsert(conn, city, firm_id, rev, mark_baseline=False):
    rid = str(rev["id"])
    text = rev.get("text") or ""
    answer = rev.get("official_answer")
    reply = answer.get("text") if answer else None
    new_hash = content_hash(text, reply)
    fetched = datetime.now(timezone.utc).isoformat()
    is_hidden = 1 if rev.get("is_hidden") else 0

    row = conn.execute(
        "SELECT hash FROM reviews WHERE platform=? AND review_id=?",
        ("2gis", rid),
    ).fetchone()

    if row is None:
        conn.execute(
            """INSERT INTO reviews
            (review_id, platform, city, firm_id, author, rating, text, reply,
             provider, date_posted, date_edited, date_fetched, hash,
             is_hidden, is_new, is_changed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, "2gis", city, firm_id,
             (rev.get("user") or {}).get("name"),
             rev.get("rating"), text, reply, rev.get("provider"),
             rev.get("date_created"), rev.get("date_edited"),
             fetched, new_hash, is_hidden,
             0 if mark_baseline else 1, 0),
        )
        return "baseline" if mark_baseline else ("hidden" if is_hidden else "new")
    elif row[0] != new_hash:
        conn.execute(
            """UPDATE reviews SET text=?, reply=?, date_edited=?,
               date_fetched=?, hash=?, is_changed=1
               WHERE platform=? AND review_id=?""",
            (text, reply, rev.get("date_edited"), fetched, new_hash, "2gis", rid),
        )
        return "changed"
    else:
        conn.execute(
            "UPDATE reviews SET date_fetched=? WHERE platform=? AND review_id=?",
            (fetched, "2gis", rid),
        )
        return "unchanged"


def main(mark_baseline=False):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()

    counts = {"new": 0, "changed": 0, "unchanged": 0, "baseline": 0, "hidden": 0}
    errors = []

    for city, firm_id in BRANCHES.items():
        try:
            reviews = fetch_branch(firm_id)
            for rev in reviews:
                status = upsert(conn, city, firm_id, rev, mark_baseline)
                counts[status] = counts.get(status, 0) + 1
            print(f"  {city}: {len(reviews)} reviews processed", flush=True)
        except Exception as e:
            errors.append(f"{city}: {type(e).__name__}: {e}")
            print(f"  {city}: ERROR {e}", flush=True)

    conn.commit()

    # Экспорт новых/изменённых
    cur = conn.execute(
        """SELECT review_id, platform, city, author, rating, text, reply,
                  date_posted, date_edited, is_new, is_changed
           FROM reviews
           WHERE (is_new=1 OR is_changed=1) AND is_hidden=0
           ORDER BY date_posted DESC""")
    cols = [d[0] for d in cur.description]
    new_export = [dict(zip(cols, r)) for r in cur.fetchall()]

    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = DAILY_DIR_ROOT / today
    daily_dir.mkdir(parents=True, exist_ok=True)
    export_path = daily_dir / "reviews_2gis.json"
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



KUMA_URL = "http://127.0.0.1:3001/api/push/6XMUvekK3IN8xNpf03yB"

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
