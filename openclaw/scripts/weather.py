#!/usr/bin/env python3
"""Погода Москва на сегодня через wttr.in (без ключей).
Сохраняет JSON в daily_data/YYYY-MM-DD/weather.json"""
import urllib.request, json
from datetime import datetime, timezone, timedelta
from pathlib import Path

MSK = timezone(timedelta(hours=3))
DATA_ROOT = Path("/volume1/homes/artemere-7601341/scripts/daily_data")

def fetch(url, timeout=10):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "curl/8.0")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8").strip()

def main():
    today = datetime.now(MSK).date()
    out_dir = DATA_ROOT / today.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {"date": today.isoformat(), "fetched_at": datetime.now(MSK).isoformat()}
    try:
        result["short"] = fetch("https://wttr.in/Moscow?format=%C+%t+%w+%h&lang=ru&m")
    except Exception as e:
        result["short_error"] = str(e)
    try:
        # Sunrise/sunset/moon
        result["astro"] = fetch("https://wttr.in/Moscow?format=%S+sun+%s+moon+%m&lang=ru&m")
    except Exception as e:
        result["astro_error"] = str(e)

    out_file = out_dir / "weather.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"OK: {out_file}")
    if "short" in result:
        print(f"   {result['short']}")
    if "astro" in result:
        print(f"   {result['astro']}")

if __name__ == "__main__":
    main()
