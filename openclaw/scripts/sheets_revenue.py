#!/usr/bin/env python3
"""
Ежедневный отчёт по выручке из Google Sheets (ИТОГИ 2026)
Отправляет в Telegram выручку текущего месяца по филиалам.
"""
import os
import sys
sys.path.insert(0, '/volume1/homes/artemere-7601341/scripts')
import _env  # noqa: F401  (loads .env into os.environ)
import json, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

TOKEN_FILE = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_token.json"
CREDENTIALS_FILE = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_credentials.json"
BOT_TOKEN = os.environ['TG_BOT_TOKEN']
CHAT_ID = os.environ['TG_CHAT_ID']
SHEET_ID = "1vvEj3Sep3IAeQKkp3gSZof-KEBZEUk-TAaSr5VvUGmw"
CACHE_FILE = "/volume1/homes/artemere-7601341/scripts/openclaw/data/revenue_cache.json"

MONTHS_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

BRANCHES = ["Москва", "Санкт-Петербург", "Нижний Новгород", "Казань", "Самара", "Воронеж", "Ростов-на-Дону", "Краснодар"]

def get_access_token():
    with open(TOKEN_FILE) as f:
        token = json.load(f)
    with open(CREDENTIALS_FILE) as f:
        creds = json.load(f)["installed"]
    data = urllib.parse.urlencode({
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": token["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(creds["token_uri"], data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)["access_token"]

def send_tg(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

def parse_number(s):
    """Парсим числа вида '3 217 603', '-8,986,204', '-11,122,050.50'"""
    try:
        cleaned = s.replace(" ", "").replace("\xa0", "").replace(",", "").replace("р.", "").replace("р", "").strip()
        return int(float(cleaned))
    except:
        return 0

def fmt_money(n):
    """Форматируем число с пробелами: 1234567 → 1 234 567"""
    return f"{n:,}".replace(",", " ")

def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_cache(data):
    import os
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def main():
    now_msk = datetime.now(timezone(timedelta(hours=3)))
    month_num = now_msk.month
    month_name = MONTHS_RU[month_num]
    date_str = now_msk.strftime("%d.%m.%Y")

    at = get_access_token()

    def get_month_row_from_range(range_str):
        range_name = urllib.parse.quote(range_str)
        req = urllib.request.Request(
            f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{range_name}",
            headers={"Authorization": f"Bearer {at}"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            rows = json.load(r).get("values", [])
        for row in rows:
            if row and row[0].strip() == month_name:
                return row
        return None

    # Выручка 2026
    row_2026 = get_month_row_from_range("ИТОГИ 2026!A1:K16")
    # Год к году в цифрах (строки 51-65)
    row_ytd = get_month_row_from_range("ИТОГИ 2026!A51:K65")
    # Год к году в процентах (строки 67-81)
    row_pct = get_month_row_from_range("ИТОГИ 2026!A67:K81")

    if not row_2026:
        send_tg(f"⚠️ Не нашёл строку «{month_name}» в ИТОГИ 2026")
        return

    values_2026 = [parse_number(row_2026[i]) if i < len(row_2026) else 0 for i in range(1, 9)]
    values_ytd  = [parse_number(row_ytd[i])  if row_ytd and i < len(row_ytd)  else 0 for i in range(1, 9)]
    values_pct  = [row_pct[i].strip() if row_pct and i < len(row_pct) else "—" for i in range(1, 9)]
    total_2026  = parse_number(row_2026[10]) if len(row_2026) > 10 else sum(values_2026)
    total_ytd   = parse_number(row_ytd[10])  if row_ytd and len(row_ytd) > 10 else sum(values_ytd)
    total_pct   = row_pct[10].strip() if row_pct and len(row_pct) > 10 else "—"

    # Загружаем вчерашние данные для сравнения
    cache = load_cache()
    cache_key = f"{month_name}_2026"
    yesterday_values = cache.get(cache_key, {}).get("values", [0]*8)
    yesterday_total  = cache.get(cache_key, {}).get("total", 0)

    # Сохраняем сегодняшние как новый кеш
    cache[cache_key] = {
        "date": date_str,
        "values": values_2026,
        "total": total_2026
    }
    save_cache(cache)

    # Формируем сообщение — порядок фиксированный как в таблице
    header = f"📊 Выручка {month_name} 2026 (на {date_str})"

    table_lines = []
    table_lines.append(f"{'Филиал':<22} {'2026':>12}  {'г/г ₽':>12}  {'г/г %':>7}  {'за день':>10}")
    table_lines.append("─" * 68)

    for branch, v26, ytd, pct, v_yday in zip(BRANCHES, values_2026, values_ytd, values_pct, yesterday_values):
        ytd_str = (f"+{fmt_money(ytd)}" if ytd >= 0 else f"-{fmt_money(abs(ytd))}") if ytd != 0 else "—"
        v26_str = fmt_money(v26) if v26 else "—"
        day_delta = v26 - v_yday
        day_str = (f"+{fmt_money(day_delta)}" if day_delta > 0 else (f"-{fmt_money(abs(day_delta))}" if day_delta < 0 else "—")) if v_yday else "—"
        table_lines.append(f"{branch:<22} {v26_str:>12}  {ytd_str:>12}  {pct:>7}  {day_str:>10}")

    table_lines.append("─" * 68)
    ytd_total_str = (f"+{fmt_money(total_ytd)}" if total_ytd >= 0 else f"-{fmt_money(abs(total_ytd))}") if total_ytd else "—"
    day_total_delta = total_2026 - yesterday_total
    day_total_str = (f"+{fmt_money(day_total_delta)}" if day_total_delta > 0 else (f"-{fmt_money(abs(day_total_delta))}" if day_total_delta < 0 else "—")) if yesterday_total else "—"
    table_lines.append(f"{'ИТОГО':<22} {fmt_money(total_2026):>12}  {ytd_total_str:>12}  {total_pct:>7}  {day_total_str:>10}")

    lines = [f"<b>{header}</b>\n<pre>{chr(10).join(table_lines)}</pre>"]

    msg = chr(10).join(lines)
    print(msg)
    try:
        send_tg(msg)
        print("# tg sent")
    except Exception as e:
        import sys; print(f"# tg skipped: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
