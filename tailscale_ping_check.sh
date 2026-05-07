#!/bin/bash
set -a
. /volume1/homes/artemere-7601341/scripts/.env
set +a
KUMA="$KUMA_BASE/$TOK_TS"
TARGET=100.75.239.31

OUT=$(timeout 15 /var/packages/Tailscale/target/bin/tailscale ping -c 1 $TARGET 2>&1)
if echo "$OUT" | grep -qE "pong from"; then
    LAT=$(echo "$OUT" | grep -oE "[0-9]+ms" | head -1)
    curl -fsS -m 10 -G --data-urlencode "status=up" --data-urlencode "msg=$LAT" --data-urlencode "ping=" "$KUMA" >/dev/null
else
    curl -fsS -m 10 -G --data-urlencode "status=down" --data-urlencode "msg=no pong" --data-urlencode "ping=" "$KUMA" >/dev/null
fi
