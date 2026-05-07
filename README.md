# nas-scripts

Бэкап/версионирование скриптов с Synology DS1825+ (`/volume1/homes/artemere-7601341/scripts/`).

## Структура

- `daily_collect.sh` — оркестратор кронов 06:00 МСК (DSM Task id=5).
- `vault_housekeeping.py` — ежедневный перенос Inbox → База знаний (00:05 МСК).
- `photo_archive_daily.py` — архив фото/видео за вчера (02:00 МСК).
- `disk_check.sh` — мониторинг свободного места на /volume1 (каждые 30 мин).
- `tailscale_ping_check.sh` — проверка связности NAS→VPS через Tailscale (каждые 10 мин).
- `freshrss_brief/` — RSS-дайджест и YouTube transcripts:
  - `extract.py` — выгружает посты из FreshRSS SQLite в JSON.
  - `youtube_transcripts.py` — скачивает transcripts видео.
  - `compose.py` — собирает Daily Brief в Obsidian Vault.
- `openclaw/scripts/` — Adesk, Google Sheets, Tasks, Telegram, погода, налоги.

## Секреты

Все токены и API-ключи живут в `.env` (НЕ коммитится). Шаблон в `.env.example`.

Скрипты загружают `.env` через:
- Bash: `set -a; . .env; set +a` в начале
- Python: `import _env` (модуль `_env.py` в корне).

## Мониторинг

Все кроны пушат heartbeat в Uptime Kuma на `127.0.0.1:3001` через push-токены (см. `.env`). Алёрты в Telegram через kuma-telegram-relay.
