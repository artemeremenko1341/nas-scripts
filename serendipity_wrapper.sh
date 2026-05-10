#!/bin/bash
# Wrapper для serendipity_collect.
# Запускается DSM Task Scheduler ежедневно (id=13).
# Реальную работу делает только в воскресенье; в остальные дни — пушит UP в Kuma
# чтобы heartbeat-monitor не падал.
#
# Backup перед патчем 09.05.2026: wrapper пушил Kuma ТОЛЬКО в вс,
# в остальные 6 дней Kuma не получал heartbeat -> DOWN-alert.

set -a
. /volume1/homes/artemere-7601341/scripts/.env
set +a

DAY=$(date +%w)
KUMA_URL="${KUMA_BASE:-http://127.0.0.1:3001/api/push}/${TOK_SEREND}"

if [ "$DAY" != "0" ]; then
  # Не воскресенье - daily heartbeat в Kuma, без работы
  curl -sS -m 10 "${KUMA_URL}?status=up&msg=heartbeat+day=${DAY}+(skip+work+not+Sunday)&ping=" > /dev/null
  echo "Not Sunday (day=$DAY), heartbeat pushed, skip work"
  exit 0
fi

# Воскресенье - Python скрипт сам пушит UP/DOWN после работы
python3 /volume1/homes/artemere-7601341/scripts/serendipity_collect.py
