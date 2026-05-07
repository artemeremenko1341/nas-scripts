#!/usr/bin/env python3
"""
photo_sort.py — сортировка фото/видео по дням
Запускать на Synology через Task Scheduler

Структура:
  EntireArchivePhotoVideo/
    2026/
      2026-01/
        2026-01-01/   ← создаём
          file.jpg
"""
import os, re, shutil, sys
from datetime import datetime

# ======= НАСТРОЙКИ =======
ARCHIVE_ROOT = "/var/services/homes/artemere-7601341/Photos/EntireArchivePhotoVideo"
LOG_FILE = "/tmp/photo_sort.log"
DRY_RUN = False  # True = только показать что будет, не двигать файлы
# =========================

MEDIA_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic', '.heif',
              '.mp4', '.mov', '.avi', '.mkv', '.3gp', '.m4v', '.wmv', '.mts', '.m2ts'}

def log(msg):
    print(msg)
    with open(LOG_FILE, 'a') as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

def extract_date_from_name(filename):
    """Извлечь дату из имени файла"""
    name = os.path.splitext(filename)[0]

    # Формат: 20260101_134345 или 20260101134345
    m = re.search(r'(\d{4})(\d{2})(\d{2})[_\-]?(\d{6})?', name)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        year = int(y)
        if 2000 <= year <= 2030 and 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return y, mo, d

    # Формат: 2026-01-01 или 2026_01_01
    m = re.search(r'(\d{4})[-_](\d{2})[-_](\d{2})', name)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        year = int(y)
        if 2000 <= year <= 2030 and 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return y, mo, d

    # Формат: IMG_20260101 или VID_20260101
    m = re.search(r'(?:IMG|VID|PANO|PORTRAIT|SLOW|BURST)[-_](\d{4})(\d{2})(\d{2})', name, re.IGNORECASE)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        year = int(y)
        if 2000 <= year <= 2030 and 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return y, mo, d

    return None, None, None

def process_month_folder(month_path, year_str, month_str):
    """Обработать папку с месяцем — разложить файлы по дням"""
    moved = 0
    skipped = 0
    errors = 0

    for fname in os.listdir(month_path):
        fpath = os.path.join(month_path, fname)

        # Пропускаем папки
        if os.path.isdir(fpath):
            continue

        # Пропускаем не-медиафайлы
        ext = os.path.splitext(fname)[1].lower()
        if ext not in MEDIA_EXTS:
            continue

        # Извлекаем дату
        y, mo, d = extract_date_from_name(fname)

        # Если не удалось — используем дату изменения файла
        if not y:
            try:
                mtime = os.path.getmtime(fpath)
                dt = datetime.fromtimestamp(mtime)
                y, mo, d = str(dt.year), f"{dt.month:02d}", f"{dt.day:02d}"
                log(f"  [дата из mtime] {fname} → {y}-{mo}-{d}")
            except:
                log(f"  [ОШИБКА] Не удалось определить дату: {fname}")
                errors += 1
                continue

        # Создаём папку дня
        day_folder = f"{y}-{mo}-{d}"
        day_path = os.path.join(month_path, day_folder)

        if not DRY_RUN:
            os.makedirs(day_path, exist_ok=True)

        dest = os.path.join(day_path, fname)

        # Проверка дубля
        if os.path.exists(dest):
            log(f"  [дубль] {fname} — пропускаем")
            skipped += 1
            continue

        if DRY_RUN:
            log(f"  [DRY] {fname} → {day_folder}/")
            moved += 1
        else:
            try:
                shutil.move(fpath, dest)
                moved += 1
            except Exception as e:
                log(f"  [ОШИБКА] {fname}: {e}")
                errors += 1

    return moved, skipped, errors

def main():
    log(f"\n{'='*50}")
    log(f"Запуск photo_sort.py {'[DRY RUN]' if DRY_RUN else ''}")
    log(f"Корень: {ARCHIVE_ROOT}")

    total_moved = total_skipped = total_errors = 0

    if not os.path.exists(ARCHIVE_ROOT):
        log(f"ОШИБКА: Папка не найдена: {ARCHIVE_ROOT}")
        sys.exit(1)

    # Обходим года
    for year_dir in sorted(os.listdir(ARCHIVE_ROOT)):
        year_path = os.path.join(ARCHIVE_ROOT, year_dir)
        if not os.path.isdir(year_path):
            continue
        if not re.match(r'^\d{4}$', year_dir):
            continue

        # Обходим месяца
        for month_dir in sorted(os.listdir(year_path)):
            month_path = os.path.join(year_path, month_dir)
            if not os.path.isdir(month_path):
                continue
            if not re.match(r'^\d{4}-\d{2}$', month_dir):
                log(f"  [пропуск] нестандартная папка: {year_dir}/{month_dir}")
                continue

            # Считаем файлы не в подпапках
            files_in_month = [f for f in os.listdir(month_path)
                             if os.path.isfile(os.path.join(month_path, f))
                             and os.path.splitext(f)[1].lower() in MEDIA_EXTS]

            if not files_in_month:
                continue

            log(f"\n{year_dir}/{month_dir} — {len(files_in_month)} файлов")
            m, s, e = process_month_folder(month_path, year_dir, month_dir[5:7])
            total_moved += m
            total_skipped += s
            total_errors += e
            log(f"  Перемещено: {m}, дублей: {s}, ошибок: {e}")

    log(f"\n{'='*50}")
    log(f"ИТОГО: перемещено {total_moved}, дублей {total_skipped}, ошибок {total_errors}")

if __name__ == "__main__":
    main()
