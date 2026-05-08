#!/usr/bin/env python3
"""
NAS-side версия number_videos.py.
Запускается на NAS DSM Task Scheduler каждый день в 23:30 МСК.
Перенумеровывает все карточки фильмов/сериалов/видео в /volume1/obsidian/2 База знаний/.

Идемпотентен - если порядок не изменился, не пишет файл.
Push в Kuma cron: number_videos для мониторинга.
"""
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, "/volume1/homes/artemere-7601341/scripts")
try:
    import _env  # noqa: F401
except ImportError:
    pass

KB = Path("/volume1/obsidian/2 База знаний")
KUMA_TOKEN = os.environ.get("TOK_NUMBER_VIDEOS", "")
KUMA_BASE = os.environ.get("KUMA_BASE", "http://127.0.0.1:3001/api/push")


def push(status, msg):
    if not KUMA_TOKEN:
        return
    url = f"{KUMA_BASE}/{KUMA_TOKEN}?" + urllib.parse.urlencode(
        {"status": status, "msg": msg, "ping": ""}
    )
    try:
        urllib.request.urlopen(url, timeout=10).read()
    except Exception:
        pass


def split_fm(content):
    m = re.match(r"^(---\n)(.*?)(\n---\n)(.*)", content, re.DOTALL)
    if m:
        return m.group(1), m.group(2), m.group(3), m.group(4)
    return None, None, None, content


def parse_fm(text):
    if not text:
        return {}
    fm = {}
    cur_k = None
    cur_lines = []
    for line in text.split("\n"):
        if re.match(r"^[a-zA-Z_][a-zA-Z0-9_-]*:", line):
            if cur_k:
                fm[cur_k] = "\n".join(cur_lines).rstrip()
            k, _, v = line.partition(":")
            cur_k = k.strip()
            cur_lines = [v.strip()]
        else:
            if cur_k:
                cur_lines.append(line)
    if cur_k:
        fm[cur_k] = "\n".join(cur_lines).rstrip()
    return fm


def serialize_fm(fm):
    lines = []
    for k, v in fm.items():
        if "\n" in v:
            lines.append(f"{k}:")
            for sub in v.split("\n"):
                if sub.strip():
                    lines.append(sub)
        else:
            lines.append(f"{k}: {v}" if v else f"{k}:")
    return "\n".join(lines)


def get_card_info(path):
    content = path.read_text(encoding="utf-8")
    fm_open, fm_text, fm_close, body = split_fm(content)
    fm = parse_fm(fm_text) if fm_text else {}

    # Skip templates
    if fm.get("type", "").strip() == "template":
        return None

    status = fm.get("status", "").strip().strip('"').lower()
    watched_raw = fm.get("watched", "").strip().strip('"')
    watched_match = re.search(r"\d{4}-\d{2}-\d{2}", watched_raw)
    watched = watched_match.group(0) if watched_match else ""

    return {
        "path": path,
        "fm": fm,
        "fm_open": fm_open,
        "fm_close": fm_close,
        "body": body,
        "status": status,
        "watched": watched,
        "name": path.stem,
    }


def sort_key(info):
    s = info["status"]
    group = 0 if s == "watched" else (1 if s == "watching" else 2)
    watched = info["watched"] or "9999-99-99"
    return (group, watched, info["name"])


def main():
    # Ищем карточки с (фильм)/(сериал)/(видео) в имени, исключаем папку Шаблоны
    cards = []
    for pattern in ("*(фильм)*.md", "*(сериал)*.md", "*(видео)*.md"):
        for p in KB.glob(pattern):
            cards.append(p)

    infos = []
    for c in cards:
        try:
            info = get_card_info(c)
        except Exception:
            continue
        if info is None:
            continue
        infos.append(info)

    infos.sort(key=sort_key)

    updated = 0
    for i, info in enumerate(infos, start=1):
        fm = info["fm"]
        old_num = fm.get("video_number", "").strip()
        new_num = str(i)
        if old_num == new_num:
            continue

        new_fm = {}
        inserted = False
        for k, v in fm.items():
            new_fm[k] = v
            if k == "type" and not inserted:
                new_fm["video_number"] = new_num
                inserted = True
        if not inserted:
            new_fm = {"video_number": new_num, **fm}

        new_content = (
            info["fm_open"]
            + serialize_fm(new_fm)
            + info["fm_close"]
            + info["body"]
        )
        try:
            info["path"].write_text(new_content, encoding="utf-8")
            updated += 1
        except Exception as e:
            print(f"ERR {info['name']}: {e}")

    msg = f"OK: {updated} renumbered, {len(infos)} total"
    print(msg)
    push("up", msg[:120])


if __name__ == "__main__":
    main()
