#!/bin/bash
# Daily auto-sync of NAS scripts to GitHub.
# Pushes changes if any, pings Kuma cron: github_sync (id=17).
set -a
. /volume1/homes/artemere-7601341/scripts/.env
set +a

cd /volume1/homes/artemere-7601341/scripts || exit 1
LOG=/tmp/github_sync.log
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"

push() {
    curl -fsS -m 10 -G         --data-urlencode "status=$1"         --data-urlencode "msg=$2"         --data-urlencode "ping="         "$KUMA_BASE/$TOK_GITSYNC" >/dev/null 2>&1 || true
}

./git-docker.sh add -A 2>>"$LOG"
if ./git-docker.sh diff --cached --quiet; then
    echo 'no changes' >> "$LOG"
    push up 'no changes'
    exit 0
fi

MSG="auto-sync $(date '+%Y-%m-%d %H:%M')"
if ! ./git-docker.sh commit -m "$MSG" >>"$LOG" 2>&1; then
    push down 'commit failed'
    exit 1
fi

if ./git-docker.sh push origin main >>"$LOG" 2>&1; then
    echo "pushed: $MSG" >> "$LOG"
    push up "$MSG"
else
    push down 'push failed'
    exit 1
fi
