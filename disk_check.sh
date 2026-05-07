#!/bin/bash
set -a
. /volume1/homes/artemere-7601341/scripts/.env
set +a
USED=$(df /volume1 | awk 'NR==2 {gsub("%","",$5); print $5}')
KUMA="$KUMA_BASE/$TOK_DISK"
if [ "$USED" -lt 85 ]; then
    curl -fsS -m 10 -G --data-urlencode "status=up" --data-urlencode "msg=${USED}%" --data-urlencode "ping=" "$KUMA" >/dev/null
else
    curl -fsS -m 10 -G --data-urlencode "status=down" --data-urlencode "msg=${USED}% >=85" --data-urlencode "ping=" "$KUMA" >/dev/null
fi
