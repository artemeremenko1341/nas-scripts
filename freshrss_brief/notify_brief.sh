#!/bin/bash
# Send arbitrary text to Telegram via bot, using v2raya HTTP-proxy (api.telegram.org blocked in RU).
# Usage:
#   echo 'message' | bash notify_brief.sh
#   bash notify_brief.sh 'message'
set -a
. /volume1/homes/artemere-7601341/scripts/.env
set +a

PROXY=http://127.0.0.1:20171

if [ -n "$1" ]; then
    MSG="$1"
else
    MSG=$(cat)
fi

if [ -z "$MSG" ]; then
    echo 'ERR: empty message' >&2
    exit 1
fi

RESP=$(curl -fsS -m 15 --proxy $PROXY     --data-urlencode "chat_id=$TG_CHAT_ID"     --data-urlencode "text=$MSG"     --data-urlencode 'parse_mode=HTML'     --data-urlencode 'disable_web_page_preview=true'     "https://api.telegram.org/bot$TG_BOT_TOKEN/sendMessage" 2>&1)

if echo "$RESP" | grep -q '"ok":true'; then
    LEN=$(echo "$MSG" | wc -c)
    echo "OK sent $LEN chars"
else
    echo "ERR: $RESP" >&2
    exit 1
fi
