#!/bin/bash
set -a
. /volume1/homes/artemere-7601341/scripts/.env
set +a
# Утренний сбор данных для брифинга Claude.
# Запускается в 06:00 МСК через Synology Task Scheduler.

TODAY=$(date +%F)
SCRIPTS=/volume1/homes/artemere-7601341/scripts/openclaw/scripts
DATA=/volume1/homes/artemere-7601341/scripts/daily_data/$TODAY
mkdir -p "$DATA"
LOG="$DATA/collect.log"

KUMA="$KUMA_BASE"

push() {
    local token=$1 status=$2 msg=$3
    curl -fsS -m 10 --retry 2 -G \
        --data-urlencode "status=$status" \
        --data-urlencode "msg=$msg" \
        --data-urlencode "ping=" \
        "$KUMA/$token" >/dev/null || true
}

FAILS=()

run() {
    local name=$1; shift
    echo "" >> "$LOG"
    echo "[$name]" >> "$LOG"
    if "$@" >> "$LOG" 2>&1; then
        echo "  OK" >> "$LOG"
        return 0
    else
        local rc=$?
        echo "  FAIL exit=$rc" >> "$LOG"
        FAILS+=("$name")
        return $rc
    fi
}

run_capture() {
    local name=$1 out=$2; shift 2
    echo "" >> "$LOG"
    echo "[$name -> $out]" >> "$LOG"
    if "$@" > "$DATA/$out" 2>>"$LOG"; then
        echo "  OK" >> "$LOG"
        return 0
    else
        local rc=$?
        echo "  FAIL exit=$rc" >> "$LOG"
        FAILS+=("$name")
        return $rc
    fi
}

echo "=== Daily collect started $(date -Iseconds) ===" > "$LOG"

run "adesk_daily_save" python3 "$SCRIPTS/adesk_daily_save.py"
run_capture "adesk_revenue" "adesk_revenue.txt" python3 "$SCRIPTS/adesk_revenue.py"
run "weather" python3 "$SCRIPTS/weather.py"

# По понедельникам добавляем --trend для блока «Тренд 2026 г/г % по месяцам»
SHEETS_KPI_ARGS=""
if [ "$(date +%u)" = "1" ]; then
    SHEETS_KPI_ARGS="--trend"
fi
if run_capture "sheets_kpi" "sheets_kpi.txt" python3 "$SCRIPTS/sheets_kpi.py" $SHEETS_KPI_ARGS; then
    push $TOK_SHEETS up OK
else
    push $TOK_SHEETS down "exit=$?"
fi

if run "tasks_rotate" python3 "$SCRIPTS/tasks_rotate.py"; then
    push $TOK_TASKS up OK
else
    push $TOK_TASKS down "exit=$?"
fi

if run "freshrss_brief" python3 "/volume1/homes/artemere-7601341/scripts/freshrss_brief/extract.py"; then
    push $TOK_FRESH up OK
else
    push $TOK_FRESH down "exit=$?"
fi

if run "youtube_transcripts" python3 "/volume1/homes/artemere-7601341/scripts/freshrss_brief/youtube_transcripts.py"; then
    push $TOK_YT up OK
else
    push $TOK_YT down "exit=$?"
fi

if run "podcast_batch" python3 "/volume1/homes/artemere-7601341/scripts/freshrss_brief/podcast_batch.py"; then
    :
else
    :  # podcast batch is non-critical (Spotify-exclusives, network), tracked via daily_collect msg
fi

if run "daily_brief_compose" python3 "/volume1/homes/artemere-7601341/scripts/freshrss_brief/compose.py"; then
    :
else
    push $TOK_BRIEF down "exit=$?"
fi

# Аудит TaskNotes ↔ Google Tasks связок (ежедневно).
# Exit 0 = OK, exit 1 = drift. daily_collect не падает, файл в daily_data покажет статус.
run_capture "tasknotes_audit" "tasknotes_audit.txt" python3 "$SCRIPTS/tasknotes_audit.py" || true

if [ "$(date +%u)" = "1" ]; then
    run_capture "weekly_tax_brief" "weekly_tax_brief.txt" python3 "$SCRIPTS/weekly_tax_brief.py"
fi

echo "" >> "$LOG"
echo "=== Done $(date -Iseconds) ===" >> "$LOG"

if [ ${#FAILS[@]} -eq 0 ]; then
    push $TOK_DAILY up OK
else
    # Pipeline дошёл до конца. Дочерние мониторы (TOK_YT, TOK_SHEETS итд) показывают DOWN сами.
    # daily_collect остаётся UP, msg показывает какие шаги упали для контекста.
    push $TOK_DAILY up "completed with ${#FAILS[@]} non-critical fail(s): ${FAILS[*]}"
fi
