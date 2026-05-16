#!/bin/bash
# Daily auto-commit of Obsidian vault to Forgejo (NAS).

set -a
. /volume1/homes/artemere-7601341/scripts/.env
set +a

REPO=/volume1/obsidian
LOG=/tmp/vault_sync.log
KUMA_TOKEN="${TOK_VAULTSYNC:-}"
DOCKER=/usr/local/bin/docker
SSH_DIR=/var/services/homes/artemere-7601341/.ssh

push_kuma() {
    [ -z "$KUMA_TOKEN" ] && return
    curl -fsS -m 10 -G \
        --data-urlencode "status=$1" \
        --data-urlencode "msg=$2" \
        --data-urlencode "ping=" \
        "${KUMA_BASE}/${KUMA_TOKEN}" >/dev/null 2>&1 || true
}

gd() {
    $DOCKER run --rm \
        -v "$REPO:/repo" \
        -v "$SSH_DIR:/root/.ssh:ro" \
        -e GIT_AUTHOR_NAME='Artem Eremenko' \
        -e GIT_AUTHOR_EMAIL='artem.eremenko@gmail.com' \
        -e GIT_COMMITTER_NAME='Artem Eremenko' \
        -e GIT_COMMITTER_EMAIL='artem.eremenko@gmail.com' \
        -w /repo \
        alpine/git \
        -c safe.directory=/repo \
        -c init.defaultBranch=main \
        -c core.fileMode=false \
        "$@"
}

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"

cd "$REPO" || { push_kuma down "cd failed"; exit 1; }

gd add -A 2>>"$LOG"
if gd diff --cached --quiet; then
    echo 'no changes' >> "$LOG"
    push_kuma up 'no changes'
    exit 0
fi

# Count what's changing for commit message
ADDED=$(gd diff --cached --name-status | grep "^A" | wc -l)
MODIFIED=$(gd diff --cached --name-status | grep "^M" | wc -l)
DELETED=$(gd diff --cached --name-status | grep "^D" | wc -l)

MSG="auto: snapshot $(date '+%Y-%m-%d %H:%M') (+${ADDED} ~${MODIFIED} -${DELETED})"
if ! gd commit -m "$MSG" >>"$LOG" 2>&1; then
    push_kuma down 'commit failed'
    exit 1
fi

if gd push forgejo main >>"$LOG" 2>&1; then
    echo "pushed: $MSG" >> "$LOG"
    push_kuma up "$MSG"
else
    push_kuma down 'push failed'
    exit 1
fi
