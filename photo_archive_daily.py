#!/usr/bin/env python3
"""
photo_archive_daily.py — ежедневный архив фото/видео за предыдущий день.

Запускается на Synology DS1825+ ежедневно в 02:00 МСК через DSM Task Scheduler.

Что делает:
  1. Обходит 18 источников в MobileBackup + OBS Studio записи
  2. Берёт файлы с датой == target_date (по умолчанию — вчера)
  3. Перемещает в EntireArchivePhotoVideo/YYYY/YYYY-MM/YYYY-MM-DD/
  4. Вставляет ссылки в дейлик Obsidian, группируя по ЧАСОВЫМ блокам
     (## HH:00 - HH:00), согласно правилу в CLAUDE.md.

Важно: в отличие от предыдущей версии на Rocky/VPS:
  - Работает НАПРЯМУЮ с файловой системой (нет WebDAV/API)
  - Медиа выводятся как точечный H2-заголовок `## HH:MM` + пустая строка +
    HTML-тег без отступа (вне списка) — это даёт fullwidth-рендер в Obsidian.
    Часовые блоки `## HH:00 - HH:00` сохраняются для лог-сообщений.
  - Хранение целей перенесено: уже лежим на Synology, пути локальные

Формат вставки внутри блока:
  ## 14:47

  <img src="file:///Z:/home/Photos/.../20260417_144700.jpg">

  ## 14:23

  <video src="file:///Z:/home/Photos/.../20260417_142300.mp4" controls></video>

Порядок: внутри блока свежее время сверху (reverse-chronological),
между блоками — тоже свежее сверху (## 14:00 - 15:00 выше ## 13:00 - 14:00).

Дедупликация: если файл уже упомянут в дейлике (по имени в `file:///`-ссылке),
повторно не вставляем.

Идемпотентность: скрипт можно запускать повторно за одну и ту же дату.

Логи: /tmp/photo_archive_daily.log + stdout (последний перенаправляется
в DSM Task Scheduler notifications, если настроены).
"""
from __future__ import annotations

import os
import sys
sys.path.insert(0, '/volume1/homes/artemere-7601341/scripts')
import _env  # noqa: F401  (loads .env into os.environ)

import argparse
import logging
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

# -----------------------------------------------------------------------------
# Конфиг: пути на файловой системе Synology
# -----------------------------------------------------------------------------

HOME = "/volume1/homes/artemere-7601341"
BACKUP_ROOT = f"{HOME}/Photos/MobileBackup/A73 пользователя Артем"
ARCHIVE_ROOT = f"{HOME}/Photos/EntireArchivePhotoVideo"
OBSIDIAN_ROOT = "/volume1/obsidian"
OBS_SRC = f"{HOME}/SynologyDrive/obs-studio"
RECORDER_ARCHIVE = f"{HOME}/recorder"

# Windows-путь, который попадает в file:///-ссылки в дейлике.
# Z:\home\ на ноуте Артёма примаплена на /volume1/homes/artemere-7601341/.
# Z:\home\Photos\...  соответствует /volume1/homes/artemere-7601341/Photos/...
WIN_ROOT_PHOTOS = "Z:/home/Photos/EntireArchivePhotoVideo"
WIN_ROOT_RECORDER = "Z:/home/recorder"

PHOTO_EXTS = {"jpg", "jpeg", "png", "heic", "heif", "webp"}
VIDEO_EXTS = {"mp4", "mov", "mkv", "m4v", "3gp", "avi", "wmv", "mts", "m2ts", "ts"}
MEDIA_EXTS = PHOTO_EXTS | VIDEO_EXTS

# Источники в BACKUP_ROOT. Структура внутри каждой: {year}/{month:02d}/<файлы>
SOURCES = [
    # DCIM
    ("Camera",                  "{backup}/DCIM/Camera/{year}/{month:02d}"),
    ("Screenshots",             "{backup}/DCIM/Screenshots/{year}/{month:02d}"),
    ("Screen recordings",       "{backup}/DCIM/Screen recordings/{year}/{month:02d}"),
    ("Getcontact",              "{backup}/DCIM/Getcontact/{year}/{month:02d}"),
    ("Videocaptures",           "{backup}/DCIM/Videocaptures/{year}/{month:02d}"),
    # Корень
    ("Avito",                   "{backup}/Avito/{year}/{month:02d}"),
    ("Download",                "{backup}/Download/{year}/{month:02d}"),
    ("Instagram",               "{backup}/Instagram/{year}/{month:02d}"),
    ("Pictures",                "{backup}/Pictures/{year}/{month:02d}"),
    ("snaps",                   "{backup}/snaps/{year}/{month:02d}"),
    ("Telegram Images",         "{backup}/Telegram Images/{year}/{month:02d}"),
    ("Telegram",                "{backup}/Telegram/{year}/{month:02d}"),
    ("VK",                      "{backup}/VK/{year}/{month:02d}"),
    ("WhatsApp",                "{backup}/WhatsApp/{year}/{month:02d}"),
    ("WhatsApp Images",         "{backup}/WhatsApp Images/{year}/{month:02d}"),
    ("WhatsApp Profile Photos", "{backup}/WhatsApp Profile Photos/{year}/{month:02d}"),
    ("WhatsApp Video",          "{backup}/WhatsApp Video/{year}/{month:02d}"),
    ("Файлы",                   "{backup}/Файлы/{year}/{month:02d}"),
]

FRONTMATTER_TEMPLATE = """\
---
up:
related:
aliases:
author: "[[Ерёменко, Артём Валерьевич]]"
tags:
  - Дневник
  - Рефлексия
created: {date_str}
Мой_Вес:
Висцеральный_жир:
Количество_шагов:
Количество_воды:
Качество_сна:
Время_отхода_ко_сну_вчера:
Время_подъема_утром:
---

"""

# -----------------------------------------------------------------------------
# Парсинг имён файлов
# -----------------------------------------------------------------------------

# Порядок паттернов важен: более специфичные — первыми.
_PATTERNS = [
    # OBS Studio: 2026-04-16 14-23-45.mkv
    re.compile(r"(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})[ _](?P<h>\d{2})-(?P<mi>\d{2})-(?P<se>\d{2})"),
    # Samsung / стандарт: 20260416_142345  |  IMG-20260416-WA0001 (без времени)
    re.compile(r"(?P<y>\d{4})(?P<mo>\d{2})(?P<d>\d{2})[_-](?P<h>\d{2})(?P<mi>\d{2})(?P<se>\d{2})"),
    # WhatsApp: IMG-20260416-WA0001 (дата есть, времени нет)
    re.compile(r"(?P<y>\d{4})(?P<mo>\d{2})(?P<d>\d{2})(?!\d)"),
    # ISO с дефисами: 2026-04-16 (без времени)
    re.compile(r"(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})(?!\d)"),
]


def parse_date_time(filename: str) -> tuple[str | None, str | None]:
    """Возвращает (date_str 'YYYY-MM-DD', time_str 'HH:MM') или (None, None).

    Если паттерн матчит — валидируем month 1-12, day 1-31, year 2000-2099.
    Иначе возвращаем None, чтобы caller мог fallback на mtime
    (важно для PICOOC и аналогов, кладущих unix-ms в имя файла).
    """
    for pat in _PATTERNS:
        for m in pat.finditer(filename):
            gd = m.groupdict()
            try:
                y, mo, d = int(gd["y"]), int(gd["mo"]), int(gd["d"])
            except (TypeError, ValueError):
                continue
            if not (2000 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31):
                continue
            d_str = f"{y:04d}-{mo:02d}-{d:02d}"
            t_str = None
            if gd.get("h"):
                try:
                    h, mi = int(gd["h"]), int(gd["mi"])
                    if 0 <= h <= 23 and 0 <= mi <= 59:
                        t_str = f"{h:02d}:{mi:02d}"
                except (TypeError, ValueError):
                    pass
            return d_str, t_str
    return None, None


# -----------------------------------------------------------------------------
# Сбор и копирование файлов
# -----------------------------------------------------------------------------

def collect_files_for_day(target_date: date, logger: logging.Logger) -> list[dict]:
    """Проходим по SOURCES + OBS_SRC и собираем файлы с датой == target_date."""
    year, month = target_date.year, target_date.month
    target_str = target_date.strftime("%Y-%m-%d")
    results: list[dict] = []

    def scan_dir(src_path: Path, src_name: str) -> None:
        if not src_path.is_dir():
            return
        try:
            entries = list(src_path.iterdir())
        except PermissionError as e:
            logger.warning("permission denied scanning %s: %s", src_path, e)
            return
        for p in entries:
            if not p.is_file():
                continue
            ext = p.suffix.lower().lstrip(".")
            if ext not in MEDIA_EXTS:
                continue
            fdate, ftime = parse_date_time(p.name)
            # Fallback на mtime, когда дата из имени не извлекается
            # (например, PICOOC сохраняет как title<unix-ms>.jpg — только mtime).
            if fdate is None:
                try:
                    mtime = datetime.fromtimestamp(p.stat().st_mtime)
                    fdate = mtime.strftime("%Y-%m-%d")
                    ftime = mtime.strftime("%H:%M")
                except OSError:
                    continue
            if fdate != target_str:
                continue
            # Для файлов с датой в имени, но без времени — пытаемся взять время из mtime
            if ftime is None:
                try:
                    mtime = datetime.fromtimestamp(p.stat().st_mtime)
                    if mtime.strftime("%Y-%m-%d") == target_str:
                        ftime = mtime.strftime("%H:%M")
                    else:
                        ftime = "00:00"
                except OSError:
                    ftime = "00:00"
            results.append({
                "src": p,
                "fname": p.name,
                "time": ftime,
                "is_photo": ext in PHOTO_EXTS,
                "source": src_name,
            })

    # 18 источников
    for src_name, src_tmpl in SOURCES:
        src_path = Path(src_tmpl.format(backup=BACKUP_ROOT, year=year, month=month))
        scan_dir(src_path, src_name)

    # OBS Studio (плоская папка, без year/month)
    scan_dir(Path(OBS_SRC), "OBS Studio")

    return results


def move_to_archive(item: dict, target_date: date, dry_run: bool,
                    logger: logging.Logger) -> Path | None:
    """Перемещает файл в EntireArchivePhotoVideo/YYYY/YYYY-MM/YYYY-MM-DD/ или
    recorder/... для OBS. Возвращает путь назначения или None при ошибке.
    Дубликат (файл уже в целевой папке) — считается успехом."""
    src: Path = item["src"]
    if item["source"] == "OBS Studio":
        root = RECORDER_ARCHIVE
    else:
        root = ARCHIVE_ROOT
    day_dir = Path(root) / str(target_date.year) / target_date.strftime("%Y-%m") / target_date.strftime("%Y-%m-%d")
    dst = day_dir / src.name

    if dst.exists():
        logger.debug("already archived: %s", dst)
        # Если исходник тоже ещё на месте — удалим, чтобы не застревали дубли
        if src.exists() and src != dst:
            if dry_run:
                logger.info("[dry-run] would remove duplicate src %s", src)
            else:
                try:
                    src.unlink()
                except OSError as e:
                    logger.warning("could not remove duplicate src %s: %s", src, e)
        return dst

    if dry_run:
        logger.info("[dry-run] would move %s -> %s", src, dst)
        return dst

    day_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src), str(dst))
        return dst
    except Exception as e:
        logger.error("move failed %s -> %s: %s", src, dst, e)
        return None


# -----------------------------------------------------------------------------
# Вставка в дейлик
# -----------------------------------------------------------------------------

HOURLY_HEADER_RE = re.compile(r"^## (\d{2}):00 - \d{2}:00\s*$", re.MULTILINE)
ANY_TIME_HEADER_RE = re.compile(r"^## \d{2}:\d{2}(?: - \d{2}:\d{2})?\s*$", re.MULTILINE)


def find_daily_path(target_date: date) -> Path:
    """Ищет дейлик в 1 Входящие / 3 Дневник / 3 Дневник/YYYY.
    Возвращает первый существующий; если нет — дефолтный путь в 3 Дневник/YYYY."""
    y = str(target_date.year)
    ds = target_date.strftime("%Y-%m-%d")
    candidates = [
        Path(OBSIDIAN_ROOT) / "1 Входящие" / f"{ds}.md",
        Path(OBSIDIAN_ROOT) / "3 Дневник" / y / f"{ds}.md",
        Path(OBSIDIAN_ROOT) / "3 Дневник" / f"{ds}.md",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return Path(OBSIDIAN_ROOT) / "3 Дневник" / y / f"{ds}.md"


def hour_block_header(hour: int) -> str:
    """## 14:00 - 15:00 (с переносом для 23 → 00)"""
    nxt = (hour + 1) % 24
    return f"## {hour:02d}:00 - {nxt:02d}:00"


def media_bullet(time_str: str, is_photo: bool, fname: str, is_obs: bool, target_date=None) -> str:
    """## HH:MM\n\n<img src=...> | <video src=... controls></video>"""
    root = WIN_ROOT_RECORDER if is_obs else WIN_ROOT_PHOTOS
    # Собираем под YYYY/YYYY-MM/YYYY-MM-DD (дата берётся из fname)
    fdate, _ = parse_date_time(fname)
    if target_date is not None:
        fdate = target_date.strftime("%Y-%m-%d")
    if not fdate:
        # fallback — в корень, но такого быть не должно (мы отфильтровали по дате)
        url = f"file:///{root}/{fname}"
    else:
        y, mo, d = fdate.split("-")
        url = f"file:///{root}/{y}/{y}-{mo}/{fdate}/{fname}"
    # Формат: время на строке буллета, медиа на следующей строке с 2-пробельным
    # отступом (markdown-продолжение списочного элемента).
    if is_photo:
        return f'## {time_str}\n\n<img src="{url}">'
    return f'## {time_str}\n\n<video src="{url}" controls></video>'


def find_existing_media_filenames(content: str) -> set[str]:
    """Находит имена файлов из `file:///...`-ссылок в тексте дейлика."""
    names = set()
    for m in re.finditer(r'file:///[^\s"<>)]+', content):
        url = m.group(0)
        # отрезаем query/anchors если есть
        url = url.split("?", 1)[0].split("#", 1)[0]
        fname = url.rsplit("/", 1)[-1]
        if "." in fname:
            names.add(fname)
    return names


def _block_span(content: str, header_match: re.Match) -> tuple[int, int]:
    """Возвращает (start, end) символьных смещений блока.
    start — начало строки с заголовком, end — до начала следующего `## ` или `---`
    или конца файла. Включает и сам заголовок и тело."""
    start = header_match.start()
    # Ищем следующий заголовок уровня ## или горизонтальный разделитель
    tail_offset = header_match.end()
    nxt_header = ANY_TIME_HEADER_RE.search(content, tail_offset)
    # Также границей может быть любой другой `## ` (например ## План на завтра)
    nxt_any = re.search(r"^## ", content[tail_offset:], flags=re.MULTILINE)
    nxt_hr = re.search(r"^---\s*$", content[tail_offset:], flags=re.MULTILINE)
    ends: list[int] = []
    if nxt_header:
        ends.append(nxt_header.start())
    if nxt_any:
        ends.append(tail_offset + nxt_any.start())
    if nxt_hr:
        ends.append(tail_offset + nxt_hr.start())
    end = min(ends) if ends else len(content)
    return start, end


def insert_into_hourly_block(content: str, hour: int, bullets: list[str]) -> str:
    """Вставляет media bullets в часовой блок `## HH:00 - (HH+1):00`.
    Если блок есть — кладёт буллеты в НАЧАЛО тела (сразу после заголовка и
    пустой строки), сохраняя уже имеющийся контент ниже.
    Если блока нет — создаёт новый блок в правильной reverse-chron позиции."""
    desired_header = hour_block_header(hour)

    # 1. Ищем существующий блок с точным совпадением заголовка
    m = re.search(rf"^{re.escape(desired_header)}\s*$", content, flags=re.MULTILINE)
    if m:
        start, end = _block_span(content, m)
        block = content[start:end]
        # Разбиваем на заголовок + тело
        lines = block.splitlines(keepends=False)
        # lines[0] — сам заголовок; после него обычно пустая строка и контент
        header_line = lines[0]
        body = "\n".join(lines[1:]).lstrip("\n")
        new_body = "\n\n".join(bullets)
        if body:
            # Вставляем bullets в начало тела (перед существующим контентом),
            # с пустой строкой-разделителем.
            merged = f"{header_line}\n\n{new_body}\n\n{body.rstrip()}\n"
        else:
            merged = f"{header_line}\n\n{new_body}\n"
        # Сохраняем разделитель между блоками (одну пустую строку после)
        tail = content[end:]
        if tail and not tail.startswith("\n"):
            merged = merged + "\n"
        return content[:start] + merged + tail

    # 2. Блока нет — создаём. Позиция: reverse-chron (свежее сверху).
    # Собираем существующие хедеры с их часами и позициями.
    headers: list[tuple[int, int]] = []  # (hour, start_pos)
    for hm in HOURLY_HEADER_RE.finditer(content):
        h = int(re.match(r"## (\d{2}):", hm.group(0)).group(1))
        headers.append((h, hm.start()))
    # Также собираем любые ## HH:MM-точечные (на случай если ещё остались)
    for hm in re.finditer(r"^## (\d{2}):(\d{2})(?:\s|$)", content, flags=re.MULTILINE):
        h = int(hm.group(1))
        headers.append((h, hm.start()))

    block_text = f"{desired_header}\n\n" + "\n\n".join(bullets) + "\n\n"

    if headers:
        # В reverse-chron дейлике: первый по файлу = самый поздний.
        # Мы хотим вставить новый блок ПЕРЕД первым блоком с часом < hour.
        headers.sort(key=lambda x: x[1])  # по позиции
        insert_pos = None
        for h, pos in headers:
            if h < hour:
                insert_pos = pos
                break
        if insert_pos is None:
            # наш hour — самый ранний: вставляем перед >[!NOTE] или концом
            insert_pos = _find_tail_anchor(content)
        return content[:insert_pos] + block_text + "\n" + content[insert_pos:]

    # 3. Нет ни одного ## хедера вообще — вставляем перед >[!NOTE] / концом.
    pos = _find_tail_anchor(content)
    return content[:pos] + block_text + "\n" + content[pos:]


def _find_tail_anchor(content: str) -> int:
    """Позиция, перед которой уместно добавлять новые блоки, если нет ориентиров."""
    for marker in ["\n>[!NOTE]", "\n***\n", "\n## План на завтра"]:
        idx = content.find(marker)
        if idx != -1:
            return idx + 1  # перед \n, но оставляем \n перед маркером
    return len(content)


def ensure_daily_exists(path: Path, target_date: date, dry_run: bool,
                       logger: logging.Logger) -> str:
    """Читает содержимое дейлика или создаёт новый с frontmatter."""
    if path.is_file():
        return path.read_text(encoding="utf-8")
    content = FRONTMATTER_TEMPLATE.format(date_str=target_date.strftime("%Y-%m-%d"))
    logger.info("daily not found, will create: %s", path)
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return content


def normalize_spacing(content: str) -> str:
    """Ровно одна пустая строка перед `## HH:...` и после заголовка перед списком."""
    # 2+ пустых перед `## HH:` → одна
    content = re.sub(r"\n{3,}(?=## \d{2}:\d{2})", "\n\n", content)
    # Между заголовком `## HH:MM[ - HH:MM]` и следующей непустой строкой — ровно одна пустая.
    # [ \t]* чтобы не захватывать \n и не смещать поведение $ при MULTILINE.
    content = re.sub(
        r"(^## \d{2}:\d{2}(?: - \d{2}:\d{2})?)[ \t]*\n+(?=\S)",
        r"\1\n\n",
        content,
        flags=re.MULTILINE,
    )
    return content


def insert_media_into_daily(daily_path: Path, items: list[dict], dry_run: bool,
                            logger: logging.Logger, target_date: date) -> int:
    """items: list[{time, fname, is_photo, source}]. Возвращает число вставленных."""
    content = ensure_daily_exists(daily_path, target_date, dry_run, logger)

    # Дедупликация
    existing = find_existing_media_filenames(content)
    deduped = [it for it in items if it["fname"] not in existing]
    skipped = len(items) - len(deduped)
    if skipped:
        logger.info("skip %d files already present in daily", skipped)

    if not deduped:
        return 0

    # Группируем по часу
    by_hour: dict[int, list[dict]] = defaultdict(list)
    for it in deduped:
        hh = int(it["time"].split(":")[0])
        by_hour[hh].append(it)

    # Обрабатываем по часам от позднего к раннему, чтобы позиции сохранялись корректно
    for hour in sorted(by_hour.keys(), reverse=True):
        group = by_hour[hour]
        # Внутри часа — от позднего времени к раннему (reverse)
        group.sort(key=lambda it: it["time"], reverse=True)
        bullets = [
            media_bullet(
                time_str=it["time"],
                is_photo=it["is_photo"],
                fname=it["fname"],
                is_obs=(it["source"] == "OBS Studio"),
                target_date=target_date,
            )
            for it in group
        ]
        content = insert_into_hourly_block(content, hour, bullets)

    content = normalize_spacing(content)

    if dry_run:
        logger.info("[dry-run] would write %d bytes to %s", len(content.encode("utf-8")), daily_path)
    else:
        daily_path.write_text(content, encoding="utf-8")
        logger.info("updated daily: %s (%d bullets)", daily_path, len(deduped))

    return len(deduped)


# -----------------------------------------------------------------------------
# Главный пайплайн
# -----------------------------------------------------------------------------

def process_day(target_date: date, dry_run: bool, logger: logging.Logger) -> int:
    logger.info("=" * 60)
    logger.info("processing %s (dry-run=%s)", target_date.isoformat(), dry_run)

    items = collect_files_for_day(target_date, logger)
    logger.info("found %d media files for %s", len(items), target_date.isoformat())

    moved: list[dict] = []
    for it in items:
        dst = move_to_archive(it, target_date, dry_run, logger)
        if dst is not None:
            it["dst"] = dst
            moved.append(it)

    logger.info("moved/archived: %d/%d", len(moved), len(items))

    if not moved:
        logger.info("nothing to insert into daily")
        return 0

    daily_path = find_daily_path(target_date)
    inserted = insert_media_into_daily(daily_path, moved, dry_run, logger, target_date)
    logger.info("inserted %d media into %s", inserted, daily_path)
    return inserted


def setup_logging(verbose: bool) -> logging.Logger:
    logger = logging.getLogger("photo_archive_daily")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    # Всегда пишем и в stdout, и в файл
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    # Персистентный лог рядом со скриптом (переживает перезагрузки в отличие от /tmp)
    log_candidates = [
        "/volume1/homes/artemere-7601341/scripts/photo_archive_daily.log",
        "/tmp/photo_archive_daily.log",
    ]
    for path in log_candidates:
        try:
            fh = logging.FileHandler(path, encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
            break
        except OSError:
            continue
    return logger



def kuma_push(status: str, msg: str) -> None:
    import urllib.request, urllib.parse
    qs = urllib.parse.urlencode({'status': status, 'msg': msg, 'ping': ''})
    try:
        urllib.request.urlopen(f"{os.environ['KUMA_BASE']}/{os.environ['TOK_PHOTO']}?{qs}", timeout=10)
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("date", nargs="?", help="Целевая дата YYYY-MM-DD (по умолчанию — вчера)")
    ap.add_argument("--dry-run", action="store_true", help="Показать что произойдёт, ничего не трогать")
    ap.add_argument("-v", "--verbose", action="store_true", help="Подробный лог")
    ap.add_argument("--days", type=int, default=7,
                    help="Окно ретроспективы в днях (default 7). Игнорируется если задан позиционный date. Первый день - вчера.")
    args = ap.parse_args()

    logger = setup_logging(args.verbose)

    if args.date:
        try:
            target = date.fromisoformat(args.date)
        except ValueError:
            logger.error("invalid date %r, expected YYYY-MM-DD", args.date)
            return 2
        targets = [target]
    else:
        # Окно: [вчера-N+1 ... вчера]. От старого к новому, чтобы блок-блоки шли последовательно.
        yesterday = date.today() - timedelta(days=1)
        targets = [yesterday - timedelta(days=i) for i in range(args.days - 1, -1, -1)]
        logger.info("window scan: %d days from %s to %s",
                    len(targets), targets[0].isoformat(), targets[-1].isoformat())

    failures = 0
    for d in targets:
        try:
            process_day(d, args.dry_run, logger)
        except Exception as e:
            failures += 1
            logger.exception("processing failed for %s: %s", d.isoformat(), e)
    if failures:
        kuma_push('down', f'failures={failures}/{len(targets)}')
    else:
        kuma_push('up', f'OK days={len(targets)}')
    return 1 if failures and len(targets) == 1 else 0


if __name__ == "__main__":
    sys.exit(main())

