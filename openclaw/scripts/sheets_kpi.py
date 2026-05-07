#!/usr/bin/env python3
"""KPI выручка по обязательствам из Google Sheets ИТОГИ 2026 + ежедневный лист.

Формат:
  Вчера по 6 регионам: цифры из листа "<Месяц> <год>" (col day)
  Месяц по 6 регионам: суммы по колонкам 1..сегодня
  Г/г %: из ИТОГИ 2026 (строки %)

Самара и Воронеж исключены - закрыты.
Подпись: данные по обязательствам.
"""
import json
import urllib.request
import urllib.parse
import urllib.error
import re
from datetime import date, timedelta
from calendar import monthrange

CREDENTIALS_FILE = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_credentials.json"
TOKEN_FILE = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_token.json"
SHEET_ID = "1vvEj3Sep3IAeQKkp3gSZof-KEBZEUk-TAaSr5VvUGmw"

MONTHS_RU = {1:"Январь", 2:"Февраль", 3:"Март", 4:"Апрель", 5:"Май", 6:"Июнь",
             7:"Июль", 8:"Август", 9:"Сентябрь", 10:"Октябрь", 11:"Ноябрь", 12:"Декабрь"}

ACTIVE = [
    ("Москва", 1),
    ("Санкт-Петербург", 2),
    ("Нижний Новгород", 3),
    ("Казань", 4),
    ("Ростов-на-Дону", 7),
    ("Краснодар", 8),
]
DAILY_ROWS = {
    "Москва": 2,
    "Санкт-Петербург": 27,
    "Нижний Новгород": 40,
    "Казань": 51,
    "Ростов-на-Дону": 61,
    "Краснодар": 70,
}


def refresh_token():
    creds = json.load(open(CREDENTIALS_FILE))["installed"]
    tok = json.load(open(TOKEN_FILE))
    data = urllib.parse.urlencode({
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": tok["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(creds["token_uri"], data=data, method="POST")
    with urllib.request.urlopen(req) as r:
        new = json.loads(r.read())
    tok["access_token"] = new["access_token"]
    json.dump(tok, open(TOKEN_FILE, "w"), indent=2)
    return tok["access_token"]


def at():
    return json.load(open(TOKEN_FILE))["access_token"]


def fetch_range(rng, allow_400=False):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{urllib.parse.quote(rng)}"
    for attempt in range(2):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + at()})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read()).get("values", [])
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 0:
                refresh_token()
                continue
            if e.code == 400 and allow_400:
                return None
            raise


def parse_n(s):
    if s is None:
        return 0
    s = str(s).strip()
    if not s or s in ("-", "—", "-р.", "р."):
        return 0
    s = re.sub(r"[^\d.,\-]", "", s)
    if not s or s in ("-", ""):
        return 0
    s = s.replace(",", "")
    try:
        return float(s)
    except:
        return 0


def fmt(n):
    return f"{int(round(n)):,}".replace(",", " ")


def yday_from_sheet(sheet_name, day_col):
    rng = f"'{sheet_name}'!A1:AH80"
    rows = fetch_range(rng, allow_400=True)
    if rows is None:
        return None
    out = {}
    for region, row_idx in DAILY_ROWS.items():
        if row_idx > len(rows):
            out[region] = 0
            continue
        row = rows[row_idx - 1]
        if day_col < len(row):
            out[region] = parse_n(row[day_col])
        else:
            out[region] = 0
    return out


def main():
    today = date.today()
    yday = today - timedelta(days=1)

    cur_month_name = MONTHS_RU[today.month]
    sheet_month = f"{cur_month_name} {today.year}"
    daily_range = f"'{sheet_month}'!A1:AH80"

    daily = fetch_range(daily_range, allow_400=True)
    cur_sheet_missing = daily is None
    if daily is None:
        daily = []

    month_by_region = {}
    for region, row_idx in DAILY_ROWS.items():
        if row_idx > len(daily):
            month_by_region[region] = 0
            continue
        row = daily[row_idx - 1]
        max_col = min(today.day, len(row) - 1) if row else 0
        total = 0.0
        for i in range(1, max_col + 1):
            total += parse_n(row[i])
        month_by_region[region] = total

    if today.day == 1:
        prev_year = today.year if today.month > 1 else today.year - 1
        prev_month = today.month - 1 if today.month > 1 else 12
        prev_sheet = f"{MONTHS_RU[prev_month]} {prev_year}"
        prev_last_day = monthrange(prev_year, prev_month)[1]
        yday_by_region = yday_from_sheet(prev_sheet, prev_last_day) or {r: 0 for r, _ in DAILY_ROWS.items()}
        yday_source = f"лист '{prev_sheet}', колонка дня {prev_last_day}"
    else:
        yday_by_region = {}
        for region, row_idx in DAILY_ROWS.items():
            if row_idx > len(daily):
                yday_by_region[region] = 0
                continue
            row = daily[row_idx - 1]
            yday_day = today.day - 1
            if yday_day < len(row):
                yday_by_region[region] = parse_n(row[yday_day])
            else:
                yday_by_region[region] = 0
        yday_source = None

    totals = fetch_range("ИТОГИ 2026!A1:K90")
    pct_by_region = {}
    yoy_abs_by_region = {}

    def find_month_row(rows, name, start, end):
        for r in rows[start:end]:
            if r and r[0].strip().lower() == name.lower():
                return r
        return None

    pct_row = find_month_row(totals, cur_month_name, 66, 82)
    abs_row = find_month_row(totals, cur_month_name, 50, 66)
    for region, col in ACTIVE:
        pct_by_region[region] = (pct_row[col].strip() if pct_row and col < len(pct_row) else "—")
        yoy_abs_by_region[region] = (parse_n(abs_row[col]) if abs_row and col < len(abs_row) else 0)

    lines = ["📈 Выручка (Google Sheets, по обязательствам)"]
    lines.append("")

    lines.append(f"Вчера ({yday.strftime('%d.%m.%Y')}):")
    yday_total = sum(yday_by_region.values())
    rows = sorted(yday_by_region.items(), key=lambda x: -x[1])
    for region, amt in rows:
        lines.append(f"  {region}: {fmt(amt)} ₽")
    lines.append(f"  Итого по 6 регионам: {fmt(yday_total)} ₽")
    if yday_source:
        lines.append(f"  (источник: {yday_source})")
    lines.append("")

    month_total = sum(month_by_region.values())
    if cur_sheet_missing:
        lines.append(f"С начала месяца ({cur_month_name} {today.year}): лист ещё не создан в таблице")
    else:
        lines.append(f"С начала месяца ({cur_month_name} {today.year}):")
        rows = sorted(month_by_region.items(), key=lambda x: -x[1])
        for region, amt in rows:
            pct = pct_by_region.get(region, "—")
            yoy = yoy_abs_by_region.get(region, 0)
            sign = ""
            if yoy:
                sign = f"  (г/г {pct}, {'+' if yoy >= 0 else ''}{fmt(yoy)} ₽)"
            lines.append(f"  {region}: {fmt(amt)} ₽{sign}")
        lines.append(f"  Итого по 6 регионам: {fmt(month_total)} ₽")

    print(chr(10).join(lines))


if __name__ == "__main__":
    main()
