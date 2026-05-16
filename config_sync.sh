#!/bin/bash
# Daily snapshot of NAS infrastructure config into git.
# Собирает: docker-compose, DSM tasks, references на ключевые скрипты.

set -a
. /volume1/homes/artemere-7601341/scripts/.env
set +a

REPO=/volume1/homes/artemere-7601341/git-repos/nas-config
GIT_DOCKER=/volume1/homes/artemere-7601341/scripts/git-docker.sh
LOG=/tmp/config_sync.log
KUMA_TOKEN="${TOK_CONFIGSYNC:-}"

push() {
    curl -fsS -m 10 -G \
        --data-urlencode "status=$1" \
        --data-urlencode "msg=$2" \
        --data-urlencode "ping=" \
        "${KUMA_BASE}/${KUMA_TOKEN}" >/dev/null 2>&1 || true
}

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" > "$LOG"

mkdir -p "$REPO"
cd "$REPO" || { push down "cd failed"; exit 1; }

# Подкаталоги
mkdir -p docker dsm

# 1. Сбор docker-compose файлов
for d in /volume1/docker/*/; do
    name=$(basename "$d")
    [ "$name" = "@eaDir" ] && continue
    for f in docker-compose.yml docker-compose.yaml compose.yaml compose.yml; do
        if [ -f "${d}${f}" ]; then
            mkdir -p "docker/$name"
            cp "${d}${f}" "docker/$name/${f}"
            echo "captured docker/$name/${f}" >> "$LOG"
        fi
    done
done

# 2. Экспорт DSM Task Scheduler
sudo -n /usr/syno/bin/synowebapi --exec api=SYNO.Core.TaskScheduler method=list version=1 2>/dev/null > dsm/tasks.raw.json
python3 -c "
import json, sys
raw = open('dsm/tasks.raw.json').read()
# strip leading log lines from synowebapi
idx = raw.find('{')
data = json.loads(raw[idx:])
tasks = data.get('data', {}).get('tasks', [])
# фильтруем — оставляем только ключевые поля, sort by id
out = sorted([
    {'id': t.get('id'), 'name': t.get('name'), 'enable': t.get('enable'),
     'action': t.get('action'), 'next_trigger_time': t.get('next_trigger_time'),
     'owner': t.get('owner'), 'type': t.get('type')}
    for t in tasks
], key=lambda x: x.get('id', 0))
open('dsm/tasks.json', 'w').write(json.dumps(out, ensure_ascii=False, indent=2))
" 2>>"$LOG" && rm dsm/tasks.raw.json

# 3. Список ключевых скриптов в ~/scripts (только manifest, не контент — он в nas-scripts)
ls -la /volume1/homes/artemere-7601341/scripts/*.sh /volume1/homes/artemere-7601341/scripts/*.py 2>/dev/null \
    | awk '{print $NF, $5, $6, $7, $8}' > scripts_manifest.txt 2>/dev/null

# 4. README обновить если нет
if [ ! -f README.md ]; then
    cat > README.md << 'EOF'
# nas-config — Snapshot of NAS infrastructure

Daily snapshot via `/volume1/homes/artemere-7601341/scripts/config_sync.sh` (DSM cron).

## Layout

- `docker/<service>/docker-compose.yml` — все docker-compose файлы из `/volume1/docker/*/`
- `dsm/tasks.json` — все DSM Task Scheduler tasks (id, name, action, schedule)
- `scripts_manifest.txt` — manifest скриптов в `~/scripts/` (контент — в репо `nas-scripts`)

## Восстановление NAS из этого snapshot

1. Восстановить базу пакетов DSM
2. `git clone http://100.91.174.104:3000/artem/nas-config.git /volume1/homes/artemere-7601341/git-repos/nas-config`
3. Для каждой папки в `docker/`: `cd /volume1/docker/<service> && cp <repo>/docker/<service>/* . && docker compose up -d`
4. Из `dsm/tasks.json` восстановить cron'ы через `synowebapi method=create`
5. Из `nas-scripts` (parallel репо) — `git pull` скрипты

## Не в этом репо

- Реальные данные сервисов (БД, файлы) — Hyper Backup на 8TB
- Скрипты (`.sh`, `.py`) — в `nas-scripts`
- Obsidian vault — в `obsidian-vault`
- Claude memory — в `claude-memory`
EOF
fi

# 5. Init / commit / push
if [ ! -d .git ]; then
    $GIT_DOCKER init >> "$LOG" 2>&1
    $GIT_DOCKER branch -M main >> "$LOG" 2>&1
    TOKEN=$(cat /tmp/forgejo_token.txt)
    $GIT_DOCKER remote add forgejo "http://artem:${TOKEN}@172.17.0.1:3000/artem/nas-config.git" >> "$LOG" 2>&1
fi

# Replace REPO path in git-docker.sh on-the-fly: we use REPO=/repo via -v
# Need a temp wrapper or call docker directly with our REPO
DOCKER=/usr/local/bin/docker
SSH_DIR=/var/services/homes/artemere-7601341/.ssh
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

# (Re)init if needed
if [ ! -d .git ]; then
    gd init >> "$LOG" 2>&1
    gd branch -M main >> "$LOG" 2>&1
    TOKEN=$(cat /tmp/forgejo_token.txt)
    gd remote add forgejo "http://artem:${TOKEN}@172.17.0.1:3000/artem/nas-config.git" >> "$LOG" 2>&1
fi

gd add -A 2>>"$LOG"
if gd diff --cached --quiet; then
    echo 'no changes' >> "$LOG"
    push up 'no changes'
    exit 0
fi

MSG="auto-sync $(date '+%Y-%m-%d %H:%M')"
if ! gd commit -m "$MSG" >>"$LOG" 2>&1; then
    push down 'commit failed'
    exit 1
fi

if gd push -u forgejo main >>"$LOG" 2>&1; then
    echo "forgejo pushed: $MSG" >> "$LOG"
    push up "$MSG"
else
    push down 'forgejo push failed'
    exit 1
fi
