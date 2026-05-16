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
    echo "github pushed: $MSG" >> "$LOG"
    GITHUB_OK=1
else
    echo "github push FAILED" >> "$LOG"
    GITHUB_OK=0
fi

if ./git-docker.sh push forgejo main >>"$LOG" 2>&1; then
    echo "forgejo pushed: $MSG" >> "$LOG"
    FORGEJO_OK=1
else
    echo "forgejo push FAILED" >> "$LOG"
    FORGEJO_OK=0
fi

if [ "$GITHUB_OK" = "1" ] && [ "$FORGEJO_OK" = "1" ]; then
    push up "$MSG (both)"
elif [ "$GITHUB_OK" = "1" ]; then
    push up "$MSG (github only)"
elif [ "$FORGEJO_OK" = "1" ]; then
    push up "$MSG (forgejo only)"
else
    push down "both push failed"
    exit 1
fi
