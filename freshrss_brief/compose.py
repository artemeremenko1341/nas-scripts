#!/usr/bin/env python3
"""
Daily Brief Compose - собирает Atlas/Daily Brief/{target}.md из freshrss_brief.json + youtube transcripts.

Запускается из daily_collect.sh после freshrss_brief и youtube_transcripts.
Без LLM. Раздел "Главное дня" - плейсхолдер с TODO для ручной редакторской работы Claude.

Usage:
    compose.py                  # вчерашняя дата
    compose.py 2026-05-06       # явная дата
    compose.py 2026-05-06 --force  # перезаписать существующий файл
"""
import os
import sys
sys.path.insert(0, '/volume1/homes/artemere-7601341/scripts')
import _env  # noqa: F401  (loads .env into os.environ)
import json
import re
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, date
from pathlib import Path

DAILY_DATA = Path("/volume1/homes/artemere-7601341/scripts/daily_data")
VAULT = Path("/volume1/obsidian")
BRIEF_DIR = VAULT / "Atlas" / "Daily Brief"
RAW_YT = VAULT / "Raw" / "youtube_transcripts"

KUMA_TOKEN = os.environ.get('TOK_BRIEF', '')
KUMA_BASE = os.environ.get('KUMA_BASE', 'http://127.0.0.1:3001/api/push')

BUCKET_ORDER = [
    ("finance",      "Финансы и рынки"),
    ("ai_biz",       "Бизнес и AI"),
    ("cinema",       "Твой рынок (кино, прокат, конкуренты)"),
    ("rental",       "Конкуренты по аренде фото-видео"),
    ("clubs",        "Компьютерные клубы"),
    ("dubai_realty", "Дубай и недвижимость"),
    ("realty_ru",    "Доходная недвижимость / self storage (РФ)"),
    ("school",       "Школа Летово"),
    ("culture",      "Культура"),
    ("politics",     "Политика"),
    ("lifestyle",    "Lifestyle"),
    ("other",        "Прочее (некатегоризированное)"),
]

BUCKET_EMOJI = {
    "finance": "\U0001F4BC",
    "ai_biz": "\U0001F916",
    "cinema": "\U0001F3AC",
    "rental": "\U0001F4F8",
    "clubs": "\U0001F3AE",
    "dubai_realty": "\U0001F3D6️",
    "realty_ru": "\U0001F3D7",
    "school": "\U0001F3EB",
    "culture": "\U0001F3AD",
    "politics": "\U0001F5F3️",
    "lifestyle": "\U0001F4DA",
    "other": "\U0001F4E6",
}

LEADING_NOISE_CHARS = (
    "↩️"          # ↩️
    "✨"                # ✨
    "✅ ⚠️"   # ✅ ⚠️
    "⬇️⬆️"
)

# Regex: ведущие эмодзи + пробелы. Используем широкий unicode range.
LEADING_RE = re.compile(
    "^[\\s"
    " "
    " -⁯"
    "←-⇿"
    "⌀-⏿"
    "■-⛿"
    "✀-➿"
    "⬀-⯿"
    "️"
    "\\U0001F000-\\U0001FFFF"
    "]+",
    re.UNICODE,
)


def clean_title(t):
    if not t:
        return "(без заголовка)"
    cleaned = LEADING_RE.sub("", t).strip()
    return cleaned or t


def clean_content(c):
    if not c:
        return ""
    return re.sub(r"\s+", " ", c).strip()


def preview(c, n=240):
    c = clean_content(c)
    if not c:
        return ""
    if len(c) <= n:
        return c
    cut = c[:n]
    sp = cut.rfind(" ")
    if sp > n * 0.7:
        cut = cut[:sp]
    return cut + "…"


def render_post_line(it, with_content=True):
    t = clean_title(it.get("title", ""))
    url = it.get("fresh_url", "")
    if with_content:
        c_full = clean_content(it.get("content", ""))
        c = c_full
        if c and t and len(t) > 10 and c.lower().startswith(t.lower()[:30]):
            c = c[len(t):].lstrip(" .,-:–—").strip()
        c = preview(c, 240)
        if c:
            return "- **[" + t + "](" + url + ")** — " + c
    return "- **[" + t + "](" + url + ")**"


def section(key, title, items):
    if not items:
        return ""
    emoji = BUCKET_EMOJI.get(key, "")
    head = ("## " + emoji + " " + title) if emoji else ("## " + title)
    lines = [head, ""]
    for it in items:
        lines.append(render_post_line(it, with_content=True))
    lines.append("")
    return "\n".join(lines)


def youtube_section(items, target_date):
    if not items:
        return ""
    lines = [
        "## \U0001F3A5 YouTube за вчера",
        "",
        "> 🟡 = pending (требуется ручное summary). 🟢 = watch / 🔴 = skip обновляется при ручной обработке.",
        "> Ссылки ведут на локальные транскрипты в Obsidian (Raw/youtube_transcripts/).",
        "",
    ]
    from datetime import datetime, timedelta
    yt_by_title = {}
    yt_by_video_id = {}
    try:
        td = datetime.strptime(target_date, "%Y-%m-%d").date()
    except Exception:
        td = None
    search_dirs = [target_date]
    if td:
        for delta in (1, 2):
            search_dirs.append((td - timedelta(days=delta)).isoformat())
    for sd in search_dirs:
        raw_dir = RAW_YT / sd
        if not raw_dir.exists():
            continue
        for f in sorted(raw_dir.glob("*.md")):
            try:
                head = f.read_text(encoding="utf-8", errors="ignore")[:2000]
                tm = re.search(r'^title:\s*"?([^"\n]+)"?\s*$', head, re.M)
                vm = re.search(r'^video_id:\s*([\w\-]+)\s*$', head, re.M)
                if tm:
                    title_norm = tm.group(1).strip().replace("\xa0", " ")
                    yt_by_title.setdefault(title_norm, f)
                if vm:
                    yt_by_video_id.setdefault(vm.group(1).strip(), f)
            except Exception:
                pass
    for it in items:
        title = clean_title(it.get("title", ""))
        feed = it.get("feed", "")
        rss_title = it.get("title", "").strip().replace("\xa0", " ")
        f = yt_by_title.get(rss_title)
        if not f:
            url = it.get("url", "") or it.get("link", "") or ""
            vid_m = re.search(r'(?:v=|youtu\.be/|/embed/|/shorts/)([\w\-]{11})', url)
            if vid_m:
                f = yt_by_video_id.get(vid_m.group(1))
        if f:
            rel = "Raw/youtube_transcripts/" + f.parent.name + "/" + urllib.parse.quote(f.name)
            yt_url = it.get("url") or it.get("link") or ""
            yt_part = " · [▶ YouTube](" + yt_url + ")" if yt_url else ""
            lines.append("- 🟡 [" + title + "](" + rel + ") — *Канал: " + feed + yt_part + "*")
        else:
            lines.append("- 🟡 **[" + title + "](" + it["fresh_url"] + ")** — *Канал: " + feed + ". Транскрипт не найден локально, fallback на FreshRSS.*")
    lines.append("")
    return "\n".join(lines)

def podcast_section(items, target_date):
    """Section for podcasts. Looks for transcripts in Raw/podcast_transcripts/{target_date}/ and
    earlier days, matches by episode_id (Spotify/Apple) or by title.
    Wiki-link to local Obsidian transcript file when found."""
    if not items:
        return ""
    lines = [
        "## \U0001F399 Подкасты за вчера",
        "",
        "> 🟡 = pending. Ссылки ведут на локальные транскрипты в Obsidian.",
        "",
    ]
    from datetime import datetime, timedelta
    pod_root = VAULT / "Raw" / "podcast_transcripts"
    pod_by_title = {}
    pod_by_episode_id = {}
    try:
        td = datetime.strptime(target_date, "%Y-%m-%d").date()
    except Exception:
        td = None
    search_dirs = [target_date]
    if td:
        for delta in (1, 2):
            search_dirs.append((td - timedelta(days=delta)).isoformat())
    for sd in search_dirs:
        raw_dir = pod_root / sd
        if not raw_dir.exists():
            continue
        for f in sorted(raw_dir.glob("*.md")):
            try:
                head = f.read_text(encoding="utf-8", errors="ignore")[:2000]
                tm = re.search(r'^title:\s*"?([^"\n]+)"?\s*$', head, re.M)
                em = re.search(r'^episode_id:\s*([\w\-]+)\s*$', head, re.M)
                if tm:
                    title_norm = tm.group(1).strip().replace("\xa0", " ")
                    pod_by_title.setdefault(title_norm, f)
                if em:
                    pod_by_episode_id.setdefault(em.group(1).strip(), f)
            except Exception:
                pass
    for it in items:
        title = clean_title(it.get("title", ""))
        feed = it.get("feed", "")
        rss_title = it.get("title", "").strip().replace("\xa0", " ")
        f = pod_by_title.get(rss_title)
        if not f:
            url = it.get("url", "") or it.get("link", "") or ""
            sp_m = re.search(r'open\.spotify\.com/episode/([\w]{22})', url)
            if sp_m:
                f = pod_by_episode_id.get(sp_m.group(1))
        if f:
            rel = "Raw/podcast_transcripts/" + f.parent.name + "/" + urllib.parse.quote(f.name)
            ep_url = it.get("url") or it.get("link") or ""
            ep_part = " · [▶ Открыть оригинал](" + ep_url + ")" if ep_url else ""
            lines.append("- 🟡 [" + title + "](" + rel + ") — *Подкаст: " + feed + ep_part + "*")
        else:
            lines.append("- 🟡 **[" + title + "](" + it["fresh_url"] + ")** — *Подкаст: " + feed + ". Транскрипт не найден локально, fallback на FreshRSS.*")
    lines.append("")
    return "\n".join(lines)

def index_section(buckets, total):
    lines = [
        "## \U0001F4CB Полный индекс (все " + str(total) + " постов)",
        "",
        "> Группировка по фиду, отсортировано **от меньшего количества к большему** (мелкие фиды сверху, шумные снизу). Кликабельно открывается в FreshRSS.",
        "",
    ]
    by_feed = {}
    for b, items in buckets.items():
        for it in items:
            by_feed.setdefault(it["feed"], []).append(it)
    for feed, items in sorted(by_feed.items(), key=lambda x: (len(x[1]), x[0])):
        lines.append("### " + feed + " (" + str(len(items)) + ")\n")
        items_sorted = sorted(items, key=lambda x: -int(x.get("entry_id", 0) or 0))
        for it in items_sorted:
            t = clean_title(it.get("title", ""))
            lines.append("- [" + t + "](" + it["fresh_url"] + ")")
        lines.append("")
    return "\n".join(lines)


def kuma_push(status, msg):
    if not KUMA_TOKEN:
        return
    try:
        url = KUMA_BASE + "/" + KUMA_TOKEN + "?status=" + status + "&msg=" + urllib.parse.quote(msg)
        urllib.request.urlopen(url, timeout=10).read()
    except Exception:
        pass


def find_brief_json(target):
    today = date.today().isoformat()
    p = DAILY_DATA / today / "freshrss_brief.json"
    if p.exists():
        try:
            d = json.load(open(p))
            if d.get("date") == target:
                return p, d
        except Exception:
            pass
    for d in sorted(DAILY_DATA.glob("*"), reverse=True):
        jp = d / "freshrss_brief.json"
        if jp.exists():
            try:
                jd = json.load(open(jp))
                if jd.get("date") == target:
                    return jp, jd
            except Exception:
                continue
    return None, None


def main():
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    if args:
        target = args[0]
    else:
        target = (date.today() - timedelta(days=1)).isoformat()

    json_path, data = find_brief_json(target)
    if not data:
        msg = "no freshrss_brief.json for " + target
        print("FAIL: " + msg)
        kuma_push("down", msg)
        sys.exit(1)

    target = data["date"]
    total = data["total"]
    feeds = data["feeds"]
    buckets = data["buckets"]

    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    out_path = BRIEF_DIR / (target + ".md")

    if out_path.exists() and not force:
        msg = target + ".md exists, skip"
        print("OK: " + msg)
        kuma_push("up", "skip " + target + " exists")
        return

    weekday_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    months_ru = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    dt = datetime.strptime(target, "%Y-%m-%d").date()
    title_human = weekday_ru[dt.weekday()] + " " + str(dt.day) + " " + months_ru[dt.month - 1] + " " + str(dt.year)

    out = []
    header = (
        "---\n"
        "date: " + target + "\n"
        "type: daily-brief\n"
        "source: FreshRSS (" + str(total) + " постов из " + str(feeds) + " каналов)\n"
        "generated: " + datetime.now().isoformat(timespec="minutes") + " (auto, NAS)\n"
        "---\n\n"
        "# Daily Brief — " + title_human + "\n\n"
        "> Жирный заголовок темы = кликабельный, ведёт на основной пост. \U0001F517 = доп. источники.\n\n"
        "---\n\n"
        "## ☀️ Главное дня\n\n"
        "> ⏳ TODO Claude (заполнить при первом утреннем сообщении): 3-5 главных событий с редакторскими акцентами. Брать из noisy + finance + ai_biz + других bucket. Эталон формата `2026-05-05.md`.\n\n"
        "---\n"
    )
    out.append(header)

    for key, sect_title in BUCKET_ORDER:
        items = buckets.get(key, [])
        s = section(key, sect_title, items)
        if s:
            out.append(s)
            out.append("---\n")

    yt = buckets.get("youtube", [])
    ys = youtube_section(yt, target)
    pods = buckets.get("podcasts", [])
    ps = podcast_section(pods, target)
    if ys:
        out.append(ys)
        out.append("---\n")

    if ps:
        out.append(ps)
        out.append("---\n")

    out.append(index_section(buckets, total))
    out.append("---\n")

    out.append(
        "## ⚙️ Что фильтровалось\n\n"
        "- **Шумные ленты** (Медуза, Труха и др.) взяты только заголовки в индексе, без контента в bucket-секциях.\n"
        "- **Финансовые ленты** сжатый формат: тикеры, цифры, гайденс.\n"
        "- **YouTube** транскрипты в `Raw/youtube_transcripts/" + target + "/`, ссылки в секции YouTube за вчера.\n"
        "- **Главное дня** плейсхолдер, заполняется Claude утром (требует редакторской оценки).\n\n"
        "---\n\n"
        "*Сгенерировано на NAS автоматически (`daily_brief_compose`) на основе FreshRSS-БД.*\n"
    )

    out_path.write_text("\n".join(out), encoding="utf-8")
    msg = target + ".md composed, " + str(total) + " posts, " + str(feeds) + " feeds"
    print("OK: " + msg)
    kuma_push("up", msg)


if __name__ == "__main__":
    main()
