#!/bin/bash
# Ночной мастер сбора YT-транскриптов и подкастов.
# Запускается в 01:00 МСК через DSM Task Scheduler.
# Цикл: extract → transcripts × 4 (01:00, 02:30, 04:00, 05:00) → podcast.
# Утренний daily_collect.sh в 06:00 заберёт уже готовые данные через кеш.

set -a
. /volume1/homes/artemere-7601341/scripts/.env
set +a

TODAY=$(date +%F)
DATA=/volume1/homes/artemere-7601341/scripts/daily_data/$TODAY
mkdir -p "$DATA"
LOG="$DATA/nightly.log"

echo "=== Nightly YT pipeline started $(date -Iseconds) ===" > "$LOG"

run() {
    local name=$1; shift
    echo "" >> "$LOG"
    echo "[$name at $(date -Iseconds)]" >> "$LOG"
    if "$@" >> "$LOG" 2>&1; then
        echo "  OK" >> "$LOG"
        return 0
    else
        echo "  FAIL exit=$?" >> "$LOG"
        return 1
    fi
}

sleep_until() {
    local hhmm=$1
    while [ "$(date +%H%M)" \< "$hhmm" ]; do
        sleep 60
    done
}

# === 01:00 — extract: создаёт freshrss_brief.json (target=yesterday) + YT prefetch ===
run extract_full python3 /volume1/homes/artemere-7601341/scripts/freshrss_brief/extract.py

# === 01:05 ish — transcripts pass 1 (основной через NoteGPT) ===
run transcripts_pass1 python3 /volume1/homes/artemere-7601341/scripts/freshrss_brief/youtube_transcripts.py

# === 02:30 — transcripts pass 2 (retry для FAIL transient) ===
sleep_until 0230
run transcripts_pass2 python3 /volume1/homes/artemere-7601341/scripts/freshrss_brief/youtube_transcripts.py

# === 04:00 — transcripts pass 3 ===
sleep_until 0400
run transcripts_pass3 python3 /volume1/homes/artemere-7601341/scripts/freshrss_brief/youtube_transcripts.py

# === 05:00 — transcripts pass 4 (финальный) ===
sleep_until 0500
run transcripts_pass4 python3 /volume1/homes/artemere-7601341/scripts/freshrss_brief/youtube_transcripts.py

# === 05:10 — podcasts (Spotify через NoteGPT audio-to-text) ===
run podcast_batch python3 /volume1/homes/artemere-7601341/scripts/freshrss_brief/podcast_batch.py

echo "" >> "$LOG"
echo "=== Done $(date -Iseconds) ===" >> "$LOG"
