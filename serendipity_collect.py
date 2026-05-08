#!/usr/bin/env python3
"""
Serendipity Collect - собирает vault-контекст за неделю в один pending file
для последующего анализа Claude Code на ноуте.

Запускается DSM Task Scheduler воскресенье 16:00 МСК.

Output: /volume1/obsidian/Atlas/_pending_serendipity/YYYY-MM-DD-context.md

Что собирает (vault, доступный на NAS):
1. Дневники за последние 7 дней (3 Дневник/YYYY/ + 1 Входящие/)
2. Daily Brief'ы за 7 дней + извлечение !-помеченных пунктов
3. freshrss_brief.json за 7 дней (bucket counts)
4. Свежие карточки 2 База знаний (30 дней)
5. Полный текст Daily Brief'ов

НЕ читает (на ноуте, читает Claude локально):
- CLAUDE.md
- memory/*.md
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, '/volume1/homes/artemere-7601341/scripts')
import _env  # noqa: F401

VAULT = Path("/volume1/obsidian")
DIARY_INBOX = VAULT / "1 Входящие"
DIARY_ARCHIVE = VAULT / "3 Дневник"
DAILY_BRIEF = VAULT / "Atlas" / "Daily Brief"
DAILY_DATA = Path("/volume1/homes/artemere-7601341/scripts/daily_data")
PENDING = VAULT / "Atlas" / "_pending_serendipity"
KB = VAULT / "2 База знаний"

KUMA_TOKEN = os.environ.get("TOK_SEREND", "")
KUMA_BASE = os.environ.get("KUMA_BASE", "http://127.0.0.1:3001/api/push")


def push(status: str, msg: str) -> None:
    if not KUMA_TOKEN:
        return
    url = f"{KUMA_BASE}/{KUMA_TOKEN}?" + urllib.parse.urlencode(
        {"status": status, "msg": msg, "ping": ""}
    )
    try:
        urllib.request.urlopen(url, timeout=10).read()
    except Exception:
        pass


def find_diary(d: date):
    iso = d.isoformat()
    for candidate in (
        DIARY_INBOX / f"{iso}.md",
        DIARY_ARCHIVE / str(d.year) / f"{iso}.md",
        DIARY_ARCHIVE / f"{iso}.md",
    ):
        if candidate.exists():
            return candidate
    return None


def collect_diaries(today: date):
    out = []
    for offset in range(7, -1, -1):
        d = today - timedelta(days=offset)
        p = find_diary(d)
        if not p:
            continue
        try:
            out.append((d, p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


def collect_briefs(today: date):
    out = []
    mark_re = re.compile(r"^[\-\*]?\s*!\s")
    for offset in range(7, -1, -1):
        d = today - timedelta(days=offset)
        p = DAILY_BRIEF / f"{d.isoformat()}.md"
        if not p.exists():
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except Exception:
            continue
        marked = []
        for line in content.split("\n"):
            stripped = line.lstrip()
            if mark_re.match(stripped) or stripped.startswith("! "):
                marked.append(line.strip())
        out.append((d, content, marked))
    return out


def collect_freshrss(today: date):
    out = []
    for offset in range(7, -1, -1):
        d = today - timedelta(days=offset)
        p = DAILY_DATA / d.isoformat() / "freshrss_brief.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            buckets = data.get("buckets", {})
            counts = {k: len(v) for k, v in buckets.items()}
            out.append(
                (d, {"total": data.get("total", 0), "feeds": data.get("feeds", 0), "counts": counts})
            )
        except Exception:
            pass
    return out


def collect_recent_kb_cards(today: date, days: int = 30):
    cutoff = (today - timedelta(days=days)).isoformat()
    found = []
    if not KB.exists():
        return found
    try:
        for p in KB.rglob("*.md"):
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime).date().isoformat()
                if mtime >= cutoff:
                    found.append(p)
            except Exception:
                continue
    except Exception:
        pass
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found[:30]


def main() -> int:
    today = date.today()
    PENDING.mkdir(parents=True, exist_ok=True)
    archive_dir = PENDING / "_archived"
    archive_dir.mkdir(parents=True, exist_ok=True)

    out_path = PENDING / f"{today.isoformat()}-context.md"
    if out_path.exists():
        msg = f"skip {today.isoformat()} exists"
        print(msg)
        push("up", msg)
        return 0

    diaries = collect_diaries(today)
    briefs = collect_briefs(today)
    freshrss = collect_freshrss(today)
    kb_cards = collect_recent_kb_cards(today)

    parts = []
    parts.append("---")
    parts.append("type: serendipity-context")
    parts.append(f"date: {today.isoformat()}")
    parts.append("period: weekly")
    parts.append(f"generated: {datetime.now().isoformat(timespec='seconds')}")
    parts.append("---")
    parts.append("")
    parts.append(f"# Serendipity Context Pack - неделя до {today.isoformat()}")
    parts.append("")
    parts.append("> Упакованный vault-контекст за 7 дней для скилла `weekly-serendipity-research`.")
    parts.append("> Собран автоматически на NAS воскресенье 16:00 МСК.")
    parts.append("> Claude Code на ноуте читает этот файл + добавляет CLAUDE.md / memory/, анализирует, пишет финальный Serendipity Report.")
    parts.append("")

    parts.append("## 📔 Дневники за 7 дней")
    parts.append("")
    if not diaries:
        parts.append("_Дневников за период не найдено._")
    else:
        for d, content in diaries:
            parts.append(f"### {d.isoformat()} ({d.strftime('%A')})")
            parts.append("")
            parts.append("```markdown")
            parts.append(content.strip())
            parts.append("```")
            parts.append("")
    parts.append("")

    parts.append("## 🌟 !-помеченные пункты Daily Brief за неделю")
    parts.append("")
    total_marks = sum(len(m) for _, _, m in briefs)
    if total_marks == 0:
        parts.append("_За неделю ни одного `!`-помеченного пункта._")
    else:
        parts.append(f"Всего {total_marks} помеченных пунктов:")
        parts.append("")
        for d, _, marked in briefs:
            if not marked:
                continue
            parts.append(f"### {d.isoformat()}")
            parts.append("")
            for m in marked:
                parts.append(f"- {m}")
            parts.append("")
    parts.append("")

    parts.append("## 📡 FreshRSS bucket counts за 7 дней")
    parts.append("")
    parts.append("Что было в источниках (контекст: что доступно, что мог пропустить).")
    parts.append("")
    if not freshrss:
        parts.append("_freshrss_brief.json за период не найден._")
    else:
        parts.append("| Дата | Total | Feeds | Top buckets |")
        parts.append("|---|---|---|---|")
        for d, info in freshrss:
            top = sorted(info["counts"].items(), key=lambda x: x[1], reverse=True)[:5]
            top_str = ", ".join(f"{k}={v}" for k, v in top)
            parts.append(f"| {d.isoformat()} | {info['total']} | {info['feeds']} | {top_str} |")
    parts.append("")

    parts.append("## 📚 Свежие карточки 2 База знаний (30 дней)")
    parts.append("")
    if not kb_cards:
        parts.append("_Свежих карточек не найдено._")
    else:
        parts.append("Топ-30 по дате изменения:")
        parts.append("")
        for p in kb_cards[:30]:
            try:
                mtime_str = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
                rel = p.relative_to(VAULT)
                parts.append(f"- `{mtime_str}` - [[{p.stem}]] (`{rel}`)")
            except Exception:
                continue
    parts.append("")

    parts.append("## 📰 Полный текст Daily Brief за 7 дней")
    parts.append("")
    parts.append("> Включаю целиком для полного контекста.")
    parts.append("")
    max_chars = 30000
    for d, content, _ in briefs:
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[...truncated, original {len(content)} chars]"
        parts.append(f"### Daily Brief {d.isoformat()}")
        parts.append("")
        parts.append("```markdown")
        parts.append(content)
        parts.append("```")
        parts.append("")
    parts.append("")

    parts.append("---")
    parts.append("")
    parts.append("## 🎯 Что делать Claude дальше (на ноуте)")
    parts.append("")
    parts.append("1. Прочитать этот файл (vault-контекст).")
    parts.append("2. **Дополнительно** прочитать на ноуте:")
    parts.append("   - `C:/Users/artem/CLAUDE.md`")
    parts.append("   - `C:/Users/artem/.claude/projects/C--Users-artem/memory/*.md`")
    parts.append("3. Применить 5 рамок: cross-domain bridge / adjacent possible / out-of-domain / книжный кандидат / рынок-тренд.")
    parts.append("4. Записать финальный отчёт в `Atlas/Serendipity Reports/{сегодня}.md` по формату SKILL.md.")
    parts.append("5. Архивировать pending file в `Atlas/_pending_serendipity/_archived/`.")
    parts.append("6. Запись в дневник.")
    parts.append("")

    out_path.write_text("\n".join(parts), encoding="utf-8")
    size_kb = out_path.stat().st_size // 1024
    msg = (
        f"OK {today.isoformat()}: {len(diaries)} diaries, {len(briefs)} briefs, "
        f"{total_marks} marks, {len(kb_cards)} kb cards, {size_kb}KB"
    )
    print(msg)
    push("up", msg[:100])
    return 0


if __name__ == "__main__":
    sys.exit(main())
