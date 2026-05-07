#!/bin/bash
# photo_trigger.sh v2 — записывает новые фото в очередь для Rocky
# Запускать каждую минуту через Task Scheduler

PHOTO_DIR="/volume1/homes/artemere-7601341/CloudsBackups/yandexdisk/Фотокамера"
LAST_FILE="/tmp/last_photo_processed"
QUEUE_FILE="/volume1/homes/artemere-7601341/scripts/photo_queue.txt"
LOG="/tmp/photo_trigger.log"

# Если файл-маркер не существует — инициализируем
if [ ! -f "$LAST_FILE" ]; then
    touch "$LAST_FILE"
    echo "$(date): Инициализация маркера" >> "$LOG"
    exit 0
fi

# Ищем файлы новее маркера
NEW_FILES=$(find "$PHOTO_DIR" -maxdepth 1 -newer "$LAST_FILE" -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" -o -iname "*.heic" \) 2>/dev/null | sort)

if [ -z "$NEW_FILES" ]; then
    exit 0
fi

# Обновляем маркер
touch "$LAST_FILE"

# Записываем в очередь
while IFS= read -r filepath; do
    FNAME=$(basename "$filepath")
    echo "$(date): Новое фото в очереди: $FNAME" >> "$LOG"
    echo "$FNAME" >> "$QUEUE_FILE"
done <<< "$NEW_FILES"
