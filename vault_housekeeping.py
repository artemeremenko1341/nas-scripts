#!/usr/bin/env python3
"""Утренний vault housekeeping для Obsidian.

Запускается в 00:30 МСК через Synology Task Scheduler.

Делает три вещи:
  1. Создаёт сегодняшний дневник `1 Входящие/{сегодня}.md` из шаблона
     `_Resourses/Шаблоны/Шаблон Ежедневная заметка.md` (если ещё нет).
  2. Переносит вчерашний дневник `1 Входящие/{вчера}.md` → `3 Дневник/{год}/{вчера}.md`.
  3. Переносит карточки из `1 Входящие/`, созданные до сегодняшней полуночи МСК,
     в `2 База знаний/`. Исключения: дневники YYYY-MM-DD.md, скрытые файлы.

Дубликаты (карточка с тем же именем уже есть в `2 База знаний/`) НЕ перезаписываются —
оставляются в Inbox, помечаются в логе, Claude разбирает в утреннем брифинге.

Лог: /volume1/homes/artemere-7601341/scripts/daily_data/{сегодня}/housekeeping.log
"""
import os
import sys
sys.path.insert(0, '/volume1/homes/artemere-7601341/scripts')
import _env  # noqa: F401  (loads .env into os.environ)
import shutil
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

VAULT = Path("/volume1/obsidian")
INBOX = VAULT / "1 Входящие"
KB = VAULT / "2 База знаний"
DIARY = VAULT / "3 Дневник"
TEMPLATE = VAULT / "_Resourses" / "Шаблоны" / "Шаблон Ежедневная заметка.md"

DATA_DIR = Path("/volume1/homes/artemere-7601341/scripts/daily_data")

MSK = timezone(timedelta(hours=3))


def log(lines, log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")


def diary_pattern():
    return re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


def create_today_diary(today_str, log_lines):
    target = INBOX / f"{today_str}.md"
    if target.exists():
        log_lines.append(f"  diary today: already exists, skip")
        return
    if not TEMPLATE.exists():
        log_lines.append(f"  diary today: ERROR template not found at {TEMPLATE}")
        return
    tpl = TEMPLATE.read_text(encoding="utf-8")
    content = tpl.replace("{{date}}", today_str)
    target.write_text(content, encoding="utf-8")
    log_lines.append(f"  diary today: created {target.name}")


def move_yesterday_diary(yesterday_str, log_lines):
    src = INBOX / f"{yesterday_str}.md"
    if not src.exists():
        log_lines.append(f"  diary yesterday: not in Inbox (skipped/missing)")
        return
    year = yesterday_str[:4]
    dst_dir = DIARY / year
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if dst.exists():
        log_lines.append(f"  diary yesterday: DUPL target exists at {dst}, kept in Inbox")
        return
    shutil.move(str(src), str(dst))
    log_lines.append(f"  diary yesterday: moved {src.name} -> 3 Дневник/{year}/")


def move_old_cards(today_midnight_msk, log_lines):
    diary_re = diary_pattern()
    moved = 0
    skipped_today = 0
    duplicates = []
    for f in INBOX.iterdir():
        if not f.is_file():
            continue
        if f.name.startswith("."):
            continue
        if diary_re.match(f.name):
            continue  # дневники обрабатываются отдельно
        # Время создания файла в UTC (NAS), переводим в MSK
        ctime_utc = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        ctime_msk = ctime_utc.astimezone(MSK)
        if ctime_msk >= today_midnight_msk:
            skipped_today += 1
            continue
        target = KB / f.name
        if target.exists():
            duplicates.append(f.name)
            continue
        shutil.move(str(f), str(target))
        moved += 1
        log_lines.append(f"  card moved: {f.name}")
    log_lines.append(f"  cards summary: moved={moved}, kept_today={skipped_today}, duplicates={len(duplicates)}")
    if duplicates:
        log_lines.append(f"  DUPL cards (kept in Inbox): {', '.join(duplicates)}")


def main():
    now_msk = datetime.now(MSK)
    today_str = now_msk.strftime("%Y-%m-%d")
    yesterday_str = (now_msk - timedelta(days=1)).strftime("%Y-%m-%d")
    today_midnight_msk = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)

    log_path = DATA_DIR / today_str / "housekeeping.log"
    log_lines = [f"=== Vault housekeeping {now_msk.isoformat()} ==="]

    if not VAULT.exists():
        log_lines.append(f"ERROR: vault not found at {VAULT}")
        log(log_lines, log_path)
        sys.exit(1)

    log_lines.append(f"[create today's diary]")
    try:
        create_today_diary(today_str, log_lines)
    except Exception as e:
        log_lines.append(f"  ERROR: {e}")

    log_lines.append(f"[move yesterday's diary]")
    try:
        move_yesterday_diary(yesterday_str, log_lines)
    except Exception as e:
        log_lines.append(f"  ERROR: {e}")

    log_lines.append(f"[move old cards from Inbox]")
    try:
        move_old_cards(today_midnight_msk, log_lines)
    except Exception as e:
        log_lines.append(f"  ERROR: {e}")

    log_lines.append(f"=== Done {datetime.now(MSK).isoformat()} ===")
    log(log_lines, log_path)
    # echo финальной строки в stdout - удобно для Task Scheduler
    print(log_lines[-1])


if __name__ == "__main__":
    main()

# Kuma push (success ping)
try:
    import urllib.request
    urllib.request.urlopen(f"{os.environ['KUMA_BASE']}/{os.environ['TOK_VAULT']}?status=up&msg=OK&ping=", timeout=10)
except Exception:
    pass
