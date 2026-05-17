#!/usr/bin/env python3
"""KPI выручка, сделки, новые клиенты из Google Sheets.

Источники:
- 'ИТОГИ {год}' — готовые месячные итоги по всем метрикам и г/г сравнения.
  Структура (фиксированная):
    Блок 1 «Выручка»                       row 2-13 (янв-дек)
    Блок 2 «Наращивание клиентской базы»   row 19-30
    Блок 3 «Общее количество заказов»      row 36-47
    Блок 4 «Год к году в цифрах»           row 53-64
    Блок 5 «Год к году соотношение в %»    row 69-80
  Колонки B-I = МСК / СПБ / НИНО / Казань / Самара / Воронеж / Ростов / Краснодар
- '{Месяц} {год}' ежедневный — для «вчера» и для г/г нарастающим.
  Регионы динамически (заголовок региона + следующие строки Выручка/Сделки/Новые)
  Колонки B-AF = дни 1-31.
  Сотрудники заполняют ПН-ПТ; данные пятницы/субботы/воскресенья появляются в ПН.
"""
import os
import sys
sys.path.insert(0, '/volume1/homes/artemere-7601341/scripts')
import _env  # noqa: F401

import json
import urllib.request
import urllib.parse
import urllib.error
import re
from datetime import date, timedelta

CREDENTIALS_FILE = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_credentials.json"
TOKEN_FILE = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_token.json"
SHEET_ID = "1vvEj3Sep3IAeQKkp3gSZof-KEBZEUk-TAaSr5VvUGmw"

MONTHS_RU = {1:"Январь", 2:"Февраль", 3:"Март", 4:"Апрель", 5:"Май", 6:"Июнь",
             7:"Июль", 8:"Август", 9:"Сентябрь", 10:"Октябрь", 11:"Ноябрь", 12:"Декабрь"}

REGIONS = [
    ("Москва", 1),
    ("Санкт-Петербург", 2),
    ("Нижний Новгород", 3),
    ("Казань", 4),
    ("Ростов-на-Дону", 7),
    ("Краснодар", 8),
]
REGION_HEADERS_IN_DAILY = {
    "Москва": ["Москва"],
    "Санкт-Петербург": ["Санкт-Петербург", "Санкт Петербург", "СПБ"],
    "Нижний Новгород": ["Нижний Новгород", "НН", "НИНО"],
    "Казань": ["Казань"],
    "Ростов-на-Дону": ["Ростов-на-Дону", "Ростов на Дону", "Ростов"],
    "Краснодар": ["Краснодар"],
}
METRIC_LABELS = {
    "revenue": ["выручка"],
    "orders": ["общее количество сделок", "количество сделок", "сделок"],
    "new_clients": ["новые клиенты"],
}
# Названия секций в ИТОГИ; ищем первую строку с этим label, потом смещение
ITOGI_SECTIONS = {
    "revenue": ("Месяц", 1),  # row с label 'Месяц' = заголовки, +1..+12 = месяцы
    "new_clients": ("Наращивание клиентской базы", 2),
    "orders": ("Общее количество заказов", 2),
    "yoy_abs": ("Год к году в цифрах", 2),
    "yoy_pct": ("Год к году соотношение в процентах", 2),
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
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"
           f"/values/{urllib.parse.quote(rng)}")
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
    if not s or s in ("-", "—", "-р.", "р.", "-%"):
        return 0
    s = re.sub(r"[^\d.,\-]", "", s)
    if not s or s == "-":
        return 0
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0


def parse_pct(s):
    """'%' возвращает 0.0 если '-%'."""
    if s is None:
        return None
    s = str(s).strip()
    if not s or s in ("-", "—", "-%", "%"):
        return None
    s = s.replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def fmt(n):
    return f"{int(round(n)):,}".replace(",", " ")


def fmt_signed(n):
    if n >= 0:
        return f"+{fmt(n)}"
    return fmt(n)


def fmt_pct(p):
    if p is None:
        return "—"
    sign = "+" if p >= 0 else ""
    return f"{sign}{p:.1f}%"


def find_section_row(itogi_rows, label):
    """Возвращает 0-based индекс строки с label в первой колонке."""
    for i, row in enumerate(itogi_rows):
        if row and row[0].strip().lower() == label.lower():
            return i
    return None


def fetch_itogi_metric(itogi_rows, section_label, header_offset, month):
    """Возвращает {region: amount} для указанного блока в ИТОГИ.

    section_label — заголовок секции (например 'Год к году в цифрах').
    header_offset — сколько строк после заголовка до начала месяцев
        (для 'Год к году в цифрах' = 2 = заголовок + строка с регионами; +1 = январь).
    """
    sec = find_section_row(itogi_rows, section_label)
    if sec is None:
        return {}
    month_row_idx = sec + header_offset + (month - 1)  # 0-based индекс месяца в массиве
    if month_row_idx >= len(itogi_rows):
        return {}
    row = itogi_rows[month_row_idx]
    out = {}
    for region, col in REGIONS:
        if section_label == "Год к году соотношение в процентах":
            out[region] = parse_pct(row[col]) if col < len(row) else None
        else:
            out[region] = parse_n(row[col]) if col < len(row) else 0
    return out


def find_region_rows(daily, headers_by_region):
    """Для каждого региона возвращает {metric: row_idx_0based}.
    Ищет заголовок региона, затем строки 'Выручка', 'Общее количество сделок',
    'Новые клиенты' идущие после него (до следующего заголовка региона).
    """
    all_headers = set()
    for hs in headers_by_region.values():
        all_headers.update(hs)

    result = {region: {} for region in headers_by_region}
    region_blocks = []  # [(start_row, region_name), ...]

    # Найдём начало каждого блока
    for i, row in enumerate(daily):
        if not row:
            continue
        label = row[0].strip()
        for region, headers in headers_by_region.items():
            if label in headers:
                region_blocks.append((i, region))
                break

    # Москва часто не имеет явного заголовка-региона на первой строке
    # (иногда row 1 = 'Москва', иногда сразу 'Выручка').
    # Особый случай: если 'Москва' не нашли в блоках, считаем её первым блоком
    if "Москва" not in [r for _, r in region_blocks]:
        region_blocks.insert(0, (0, "Москва"))
    region_blocks.sort()

    for idx, (start, region) in enumerate(region_blocks):
        end = region_blocks[idx + 1][0] if idx + 1 < len(region_blocks) else len(daily)
        for j in range(start, end):
            if j >= len(daily) or not daily[j]:
                continue
            lab = daily[j][0].strip().lower()
            for metric, candidates in METRIC_LABELS.items():
                if metric in result[region]:
                    continue
                for c in candidates:
                    if lab == c or lab.startswith(c):
                        result[region][metric] = j
                        break
    return result


def sum_first_n_days(daily, row_idx, n_days):
    if row_idx is None or row_idx >= len(daily):
        return 0
    row = daily[row_idx]
    total = 0.0
    for d in range(1, n_days + 1):
        if d < len(row):
            total += parse_n(row[d])
    return total


def day_value(daily, row_idx, day):
    if row_idx is None or row_idx >= len(daily):
        return 0
    row = daily[row_idx]
    if day < len(row):
        return parse_n(row[day])
    return 0


def main():
    today = date.today()
    yday = today - timedelta(days=1)

    cur_month = MONTHS_RU[today.month]
    cur_sheet = f"{cur_month} {today.year}"
    prev_sheet = f"{cur_month} {today.year - 1}"

    # === ИТОГИ ===
    itogi_cur = fetch_range(f"ИТОГИ {today.year}!A1:K100", allow_400=True) or []
    itogi_prev = fetch_range(f"ИТОГИ {today.year - 1}!A1:K100", allow_400=True) or []

    revenue_cur = fetch_itogi_metric(itogi_cur, "Месяц", 1, today.month)
    orders_cur = fetch_itogi_metric(itogi_cur, "Общее количество заказов", 2, today.month)
    new_cli_cur = fetch_itogi_metric(itogi_cur, "Наращивание клиентской базы", 2, today.month)
    yoy_abs = fetch_itogi_metric(itogi_cur, "Год к году в цифрах", 2, today.month)
    yoy_pct = fetch_itogi_metric(itogi_cur, "Год к году соотношение в процентах", 2, today.month)

    # Прошлый месяц и прошлогодний месяц для контекста
    prev_m = today.month - 1 if today.month > 1 else 12
    prev_m_year = today.year if today.month > 1 else today.year - 1
    revenue_prev_month = fetch_itogi_metric(itogi_cur if prev_m_year == today.year else itogi_prev,
                                            "Месяц", 1, prev_m)

    def itogi_prev_block_revenue(month):
        """В ИТОГИ {прошлого года} первая ячейка может быть пустой, не 'Месяц'.
        Используем фиксированный offset: row 2 = январь, поэтому row (1 + month) 0-based."""
        idx = month  # 0-based: row 2 = январь (month=1) → idx 1; общая формула: month
        if idx >= len(itogi_prev):
            return {}
        row = itogi_prev[idx]
        return {region: parse_n(row[col]) if col < len(row) else 0
                for region, col in REGIONS}

    revenue_prev_year_same = itogi_prev_block_revenue(today.month)
    orders_prev_year_same = fetch_itogi_metric(itogi_prev, "Общее количество заказов", 2, today.month)
    new_cli_prev_year_same = fetch_itogi_metric(itogi_prev, "Наращивание клиентской базы", 2, today.month)

    # === Ежедневные листы ===
    daily_cur = fetch_range(f"'{cur_sheet}'!A1:AH85", allow_400=True) or []
    daily_prev = fetch_range(f"'{prev_sheet}'!A1:AH85", allow_400=True) or []
    rows_cur = find_region_rows(daily_cur, REGION_HEADERS_IN_DAILY)
    rows_prev = find_region_rows(daily_prev, REGION_HEADERS_IN_DAILY)

    # Вчера по метрикам
    yday_revenue = {r: day_value(daily_cur, rows_cur.get(r, {}).get("revenue"), yday.day)
                    for r, _ in REGIONS}
    yday_orders = {r: day_value(daily_cur, rows_cur.get(r, {}).get("orders"), yday.day)
                   for r, _ in REGIONS}
    yday_new_cli = {r: day_value(daily_cur, rows_cur.get(r, {}).get("new_clients"), yday.day)
                    for r, _ in REGIONS}

    # Г/г нарастающим — текущий N дней vs прошлогодний те же N дней
    days_so_far = yday.day
    yoy_running_revenue = {}
    yoy_running_orders = {}
    yoy_running_new = {}
    for r, _ in REGIONS:
        cur_rev = sum_first_n_days(daily_cur, rows_cur.get(r, {}).get("revenue"), days_so_far)
        prev_rev = sum_first_n_days(daily_prev, rows_prev.get(r, {}).get("revenue"), days_so_far)
        yoy_running_revenue[r] = (cur_rev, prev_rev)
        cur_o = sum_first_n_days(daily_cur, rows_cur.get(r, {}).get("orders"), days_so_far)
        prev_o = sum_first_n_days(daily_prev, rows_prev.get(r, {}).get("orders"), days_so_far)
        yoy_running_orders[r] = (cur_o, prev_o)
        cur_n = sum_first_n_days(daily_cur, rows_cur.get(r, {}).get("new_clients"), days_so_far)
        prev_n = sum_first_n_days(daily_prev, rows_prev.get(r, {}).get("new_clients"), days_so_far)
        yoy_running_new[r] = (cur_n, prev_n)

    # === Вывод ===
    lines = ["📈 Выручка / сделки / клиенты (Google Sheets, по обязательствам)"]
    lines.append("")

    # ВЧЕРА
    lines.append(f"Вчера ({yday.strftime('%d.%m.%Y, %a')}):")
    yt_rev = sum(yday_revenue.values())
    yt_ord = sum(yday_orders.values())
    yt_nc = sum(yday_new_cli.values())
    if yt_rev == 0 and yt_ord == 0:
        lines.append("  (в Sheets не закрыто — сотрудники заполняют пн-пт, "
                     "за выходные данные появляются в понедельник)")
    else:
        for r, _ in sorted(REGIONS, key=lambda x: -yday_revenue.get(x[0], 0)):
            rev = yday_revenue.get(r, 0)
            o = yday_orders.get(r, 0)
            n = yday_new_cli.get(r, 0)
            lines.append(f"  {r}: {fmt(rev)} ₽  |  {fmt(o)} сд.  |  {fmt(n)} нов. кл.")
        lines.append(f"  Итого: {fmt(yt_rev)} ₽  |  {fmt(yt_ord)} сд.  |  {fmt(yt_nc)} нов. кл.")
    lines.append("")

    # МЕСЯЦ из ИТОГИ
    lines.append(f"С начала месяца ({cur_month} {today.year}, по ИТОГИ):")
    for r, _ in sorted(REGIONS, key=lambda x: -revenue_cur.get(x[0], 0)):
        rev = revenue_cur.get(r, 0)
        o = orders_cur.get(r, 0)
        n = new_cli_cur.get(r, 0)
        lines.append(f"  {r}: {fmt(rev)} ₽  |  {fmt(o)} сд.  |  {fmt(n)} нов. кл.")
    tot_rev = sum(revenue_cur.values())
    tot_o = sum(orders_cur.values())
    tot_n = sum(new_cli_cur.values())
    lines.append(f"  Итого: {fmt(tot_rev)} ₽  |  {fmt(tot_o)} сд.  |  {fmt(tot_n)} нов. кл.")
    lines.append("")

    # (Блок «Г/г по таблице ИТОГИ» убран из ежедневного — Артём видит в таблице сам)

    # Г/Г нарастающим за тот же N дней — честное
    lines.append(f"Г/г нарастающим за первые {days_so_far} дн. {cur_month} (честное):")
    tot_cur_rev = sum(v[0] for v in yoy_running_revenue.values())
    tot_prev_rev = sum(v[1] for v in yoy_running_revenue.values())
    tot_cur_ord = sum(v[0] for v in yoy_running_orders.values())
    tot_prev_ord = sum(v[1] for v in yoy_running_orders.values())
    tot_cur_nc = sum(v[0] for v in yoy_running_new.values())
    tot_prev_nc = sum(v[1] for v in yoy_running_new.values())
    for r, _ in REGIONS:
        cur_rev, prev_rev = yoy_running_revenue.get(r, (0, 0))
        pct_r = ((cur_rev - prev_rev) / prev_rev * 100) if prev_rev > 0 else None
        cur_o, prev_o = yoy_running_orders.get(r, (0, 0))
        pct_o = ((cur_o - prev_o) / prev_o * 100) if prev_o > 0 else None
        cur_n, prev_n = yoy_running_new.get(r, (0, 0))
        pct_n = ((cur_n - prev_n) / prev_n * 100) if prev_n > 0 else None
        lines.append(f"  {r}: выручка {fmt_pct(pct_r)} | сд. {fmt_pct(pct_o)} "
                     f"| нов.кл. {fmt_pct(pct_n)}")
    if tot_prev_rev > 0:
        ptr = (tot_cur_rev - tot_prev_rev) / tot_prev_rev * 100
        pto = (tot_cur_ord - tot_prev_ord) / tot_prev_ord * 100 if tot_prev_ord > 0 else None
        ptn = (tot_cur_nc - tot_prev_nc) / tot_prev_nc * 100 if tot_prev_nc > 0 else None
        lines.append(f"  Итого: выручка {fmt_pct(ptr)} ({fmt(tot_cur_rev)} vs {fmt(tot_prev_rev)})"
                     f"  | сд. {fmt_pct(pto)}  | нов.кл. {fmt_pct(ptn)}")
    lines.append("")

    # (Блок «Контекст» убран из ежедневного — Артём видит в Sheets сам)

    # === Тренд 2026 г/г % по месяцам — только по флагу --trend (раз в неделю, понедельник) ===
    if "--trend" not in sys.argv:
        print("\n".join(lines))
        return

    lines.append("")
    lines.append(f"Тренд {today.year} г/г % по завершённым месяцам:")

    def itogi_prev_block(rows_prev, section_label, header_offset, month):
        """Достаёт строку метрики из ИТОГИ прошлого года.
        В прошлогодней таблице первая строка может быть пустой (не 'Месяц'),
        используем 'Прошлый год Среднее значение' как якорь — выручка идёт
        от row 2, остальные блоки находим по их заголовкам."""
        if section_label is None:
            # Выручка прошлого года — фиксированный offset (row 2 = январь)
            idx = month  # 0-based: январь = 1
            if idx >= len(rows_prev):
                return {}
            row = rows_prev[idx]
            return {region: parse_n(row[col]) if col < len(row) else 0
                    for region, col in REGIONS}
        return fetch_itogi_metric(rows_prev, section_label, header_offset, month)

    # Сравниваем за месяцы до текущего (для завершённых) + текущий нарастающим
    rev_yoy_pct_by_month = []
    nc_yoy_pct_by_month = []
    ord_yoy_pct_by_month = []
    months_to_show = list(range(1, today.month))  # завершённые
    for m in months_to_show:
        rev_cur_m = fetch_itogi_metric(itogi_cur, "Месяц", 1, m)
        rev_prev_m = itogi_prev_block(itogi_prev, None, 0, m)
        nc_cur_m = fetch_itogi_metric(itogi_cur, "Наращивание клиентской базы", 2, m)
        nc_prev_m = fetch_itogi_metric(itogi_prev, "Наращивание клиентской базы", 2, m)
        ord_cur_m = fetch_itogi_metric(itogi_cur, "Общее количество заказов", 2, m)
        ord_prev_m = fetch_itogi_metric(itogi_prev, "Общее количество заказов", 2, m)

        rev_cur_t = sum(rev_cur_m.values())
        rev_prev_t = sum(rev_prev_m.values())
        rev_pct = (rev_cur_t - rev_prev_t) / rev_prev_t * 100 if rev_prev_t > 0 else None

        nc_cur_t = sum(nc_cur_m.values())
        nc_prev_t = sum(nc_prev_m.values())
        nc_pct = (nc_cur_t - nc_prev_t) / nc_prev_t * 100 if nc_prev_t > 0 else None

        ord_cur_t = sum(ord_cur_m.values())
        ord_prev_t = sum(ord_prev_m.values())
        ord_pct = (ord_cur_t - ord_prev_t) / ord_prev_t * 100 if ord_prev_t > 0 else None

        rev_yoy_pct_by_month.append((m, rev_pct))
        nc_yoy_pct_by_month.append((m, nc_pct))
        ord_yoy_pct_by_month.append((m, ord_pct))

    # Текущий — нарастающим
    cur_run_rev_t = sum(v[0] for v in yoy_running_revenue.values())
    prev_run_rev_t = sum(v[1] for v in yoy_running_revenue.values())
    cur_run_rev_pct = (cur_run_rev_t - prev_run_rev_t) / prev_run_rev_t * 100 if prev_run_rev_t > 0 else None
    cur_run_o_t = sum(v[0] for v in yoy_running_orders.values())
    prev_run_o_t = sum(v[1] for v in yoy_running_orders.values())
    cur_run_o_pct = (cur_run_o_t - prev_run_o_t) / prev_run_o_t * 100 if prev_run_o_t > 0 else None
    cur_run_n_t = sum(v[0] for v in yoy_running_new.values())
    prev_run_n_t = sum(v[1] for v in yoy_running_new.values())
    cur_run_n_pct = (cur_run_n_t - prev_run_n_t) / prev_run_n_t * 100 if prev_run_n_t > 0 else None

    def trend_line(label, completed, cur_pct):
        parts = []
        for m, p in completed:
            parts.append(f"{MONTHS_RU[m][:3]}: {fmt_pct(p)}")
        parts.append(f"{MONTHS_RU[today.month][:3]} ({days_so_far}дн): {fmt_pct(cur_pct)}")
        return f"  {label}: " + "  ".join(parts)

    lines.append(trend_line("Выручка", rev_yoy_pct_by_month, cur_run_rev_pct))
    lines.append(trend_line("Сделки ", ord_yoy_pct_by_month, cur_run_o_pct))
    lines.append(trend_line("Нов.кл.", nc_yoy_pct_by_month, cur_run_n_pct))
    lines.append("  (завершённые месяцы — полный vs полный; текущий — нарастающим vs тот же N дней прошлого года)")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
