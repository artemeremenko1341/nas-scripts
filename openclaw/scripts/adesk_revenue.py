#!/usr/bin/env python3
"""Выручка delight.rent из Adesk (кассовый метод), прямой запрос API.

Соответствует Adesk UI «Отчёт о движении денежных средств» с фильтром
«Без учёта плановых». Цифры один-в-один с тем, что показывает UI.

Особенности обработки данных Adesk:
- `isPlanned: true` транзакции (периодические, запланированные платежи на
  годы вперёд) ИСКЛЮЧАЕМ — как делает UI с фильтром «без учёта плановых».
- `isTransfer: true` (внутренние переводы между счетами) ИСКЛЮЧАЕМ.
- `isSplitted: true` раскрываем в parts: каждая часть имеет свою category,
  contractor, дату начисления. Сумма транзакции = сумма частей.
- Фильтр периода по `dateIso` (дата фактической операции, как Adesk UI
  в колонках сверху).
- Группировка расходов:
    1) если есть category.group.name — regex `^(\\d{2})` по нему,
    2) иначе если есть category.name — regex по нему,
    3) иначе «Без категории» + в выводе топ-3 такие транзакции с
       контрагентом и описанием, чтобы Артём знал что заводить.
"""
import os
import sys
sys.path.insert(0, '/volume1/homes/artemere-7601341/scripts')
import _env  # noqa: F401

import urllib.request
import urllib.error
import json
import ssl
import re
from datetime import date, timedelta

ADESK_TOKEN = os.environ['ADESK_TOKEN']
ADESK_BASE = "https://api.adesk.ru/v1"

ACTIVE_CITIES = [
    "Москва", "Санкт-Петербург", "Нижний Новгород",
    "Казань", "Ростов-на-Дону", "Краснодар",
]
CLOSED_CITIES = ["Самара", "Воронеж"]

INCOME_CITIES = [
    (1219391, 1219398, "Москва"),
    (1219392, 1219399, "Санкт-Петербург"),
    (1219393, 1219400, "Нижний Новгород"),
    (1218675, 1219401, "Казань"),
    (1219395, 1219402, "Самара"),
    (1219394, 1219403, "Воронеж"),
    (1219396, 1219404, "Ростов-на-Дону"),
    (1219397, 1219405, "Краснодар"),
]
INCOME_IDS = {cid for p, u, _ in INCOME_CITIES for cid in (p, u)}

EXPENSE_GROUPS = {
    "01": "01. Субаренда",
    "02": "02. Логистика и доставка",
    "03": "03. Обслуживание и оплата офисов",
    "04": "04. Закупка расходных материалов",
    "05": "05. Закупка оборудования",
    "06": "06. Ремонт оборудования",
    "07": "07. Фонд оплаты труда",
    "08": "08. Банковское обслуживание",
    "09": "09. Маркетинг",
    "10": "10. Связь и интернет",
    "11": "11. Транспорт",
    "12": "12. Налоги",
    "13": "13. Возвраты клиентам",
    "14": "14. Прочее",
    "15": "15. Выплаты собственникам (дивиденды)",
}
GROUP_RE = re.compile(r"^(\d{2})")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Rocky/1.0"})
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        return json.loads(r.read())


def expand_transaction(t):
    """Раскрывает транзакцию в список 'частей' для агрегации.

    isPlanned и isTransfer исключаются ДО вызова этой функции.
    Дата каждой части = dateIso родителя (фактическая операция),
    не relatedDateIso, потому что Adesk UI группирует по фактической дате.
    """
    parent_date = t.get("dateIso") or ""
    parent_proj = t.get("project")
    parent_contractor = t.get("contractor")
    if t.get("isSplitted") and t.get("parts"):
        return [{
            "amount": float(p.get("amount", 0)),
            "date": parent_date,
            "category": p.get("category"),
            "project": p.get("project") or parent_proj,
            "contractor": p.get("contractor") or parent_contractor,
            "tx_id": t["id"],
            "description": t.get("description") or "",
        } for p in t["parts"]]
    return [{
        "amount": float(t.get("amount", 0)),
        "date": parent_date,
        "category": t.get("category"),
        "project": t.get("project"),
        "contractor": t.get("contractor"),
        "tx_id": t["id"],
        "description": t.get("description") or "",
    }]


def fetch_all_parts(tx_type, start_date, end_date):
    """Тянет все транзакции типа tx_type, expandит, фильтрует.

    Исключает isPlanned и isTransfer. Фильтр периода по dateIso родителя.
    """
    parts = []
    start = 0
    seen_ids = set()
    sk = start_date.isoformat()
    ek = end_date.isoformat()
    while True:
        url = (f"{ADESK_BASE}/transactions?api_token={ADESK_TOKEN}"
               f"&type={tx_type}&start={start}&length=100")
        try:
            data = fetch(url)
        except urllib.error.HTTPError as e:
            print(f"# WARN fetch {tx_type} start={start}: {e}", file=sys.stderr)
            break
        txns = data.get("transactions", [])
        if not txns:
            break
        for t in txns:
            tid = t.get("id")
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            if t.get("isTransfer"):
                continue
            if t.get("isPlanned"):
                continue  # фильтр «без учёта плановых»
            d = t.get("dateIso") or ""
            if not (sk <= d <= ek):
                continue
            for p in expand_transaction(t):
                parts.append(p)
        start += len(txns)
        if start > 10000:
            break
    return parts


def classify_group(category):
    """Возвращает верхнюю группу (01-15) или None.

    Иерархия Adesk: category может быть листовая (07.02.01) с group
    среднего уровня (07.02), или верхняя (05) без group.
    """
    if not category:
        return None
    grp = category.get("group") or {}
    grp_name = (grp.get("name") or "").strip()
    if grp_name:
        m = GROUP_RE.match(grp_name)
        if m:
            return m.group(1)
    cat_name = (category.get("name") or "").strip()
    if cat_name:
        m = GROUP_RE.match(cat_name)
        if m:
            return m.group(1)
    return None


def aggregate_income(parts):
    cities = {c: 0.0 for c in ACTIVE_CITIES}
    other = {}
    total = 0.0
    for p in parts:
        amt = p["amount"]
        total += amt
        cat = p.get("category") or {}
        cat_id = cat.get("id")
        cat_name = (cat.get("name") or "Прочее").strip()
        matched_city = None
        if cat_id in INCOME_IDS:
            for cp, cu, city in INCOME_CITIES:
                if cat_id in (cp, cu):
                    matched_city = city
                    break
        if matched_city in cities:
            cities[matched_city] += amt
        elif matched_city in CLOSED_CITIES:
            other[matched_city] = other.get(matched_city, 0) + amt
        else:
            other[cat_name] = other.get(cat_name, 0) + amt
    return cities, other, total


def aggregate_outcome(parts):
    by_group = {}
    total = 0.0
    no_cat = []
    for p in parts:
        amt = p["amount"]
        total += amt
        gid = classify_group(p.get("category"))
        if gid:
            label = EXPENSE_GROUPS.get(gid, f"{gid}. (новая группа)")
            by_group[label] = by_group.get(label, 0) + amt
        else:
            no_cat.append(p)
    if no_cat:
        by_group["Без категории"] = sum(x["amount"] for x in no_cat)
    return by_group, total, no_cat


def get_accounts_balance():
    url = f"{ADESK_BASE}/bank-accounts?api_token={ADESK_TOKEN}"
    data = fetch(url)
    accts = data.get("bankAccounts", [])
    total = 0.0
    open_count = 0
    for a in accts:
        if a.get("status") == "open":
            total += float(a.get("convertedAmount", 0))
            open_count += 1
    return total, open_count


def fmt(n):
    return f"{n:,.0f}".replace(",", " ")


def render_section(label, in_parts, out_parts, show_no_cat_top=0):
    lines = [f"{label}:"]
    cities, other, in_total = aggregate_income(in_parts)
    groups, out_total, no_cat = aggregate_outcome(out_parts)

    lines.append("  📥 Доходы по регионам:")
    for c in sorted(cities, key=lambda x: -cities[x]):
        lines.append(f"    {c}: {fmt(cities[c])} ₽")
    lines.append(f"    Итого по 6 регионам: {fmt(sum(cities.values()))} ₽")
    if other:
        lines.append("  📥 Прочие поступления:")
        for k in sorted(other, key=lambda x: -other[x]):
            lines.append(f"    {k}: {fmt(other[k])} ₽")
        lines.append(f"    Итого прочих: {fmt(sum(other.values()))} ₽")
    lines.append(f"  ✅ ВСЕГО доходов: {fmt(in_total)} ₽")

    if groups:
        lines.append("  📤 Расходы по группам:")
        for g in sorted(groups, key=lambda x: -groups[x]):
            lines.append(f"    {g}: {fmt(groups[g])} ₽")
        lines.append(f"  ❌ ВСЕГО расходов: {fmt(out_total)} ₽")
        if show_no_cat_top and no_cat:
            top = sorted(no_cat, key=lambda x: -x["amount"])[:show_no_cat_top]
            lines.append("  ℹ️ Топ непроклассифицированных (нужно завести категорию в Adesk):")
            for p in top:
                cp = (p.get("contractor") or {}).get("name", "") or "—"
                desc = p["description"].replace("\n", " ").strip()[:70]
                lines.append(f"    • {p['date']}  {fmt(p['amount'])} ₽  {cp}")
                if desc:
                    lines.append(f"      {desc}")
    else:
        lines.append("  📤 Расходов не было")

    bal = in_total - out_total
    sign = "+" if bal >= 0 else ""
    lines.append(f"  💰 Баланс: {sign}{fmt(bal)} ₽")
    return lines


def main():
    today = date.today()
    yday = today - timedelta(days=1)
    month_start = today.replace(day=1)

    in_month = fetch_all_parts("income", month_start, today)
    out_month = fetch_all_parts("outcome", month_start, today)

    in_yday = [p for p in in_month if p["date"] == yday.isoformat()]
    out_yday = [p for p in out_month if p["date"] == yday.isoformat()]
    in_to_yday = [p for p in in_month if p["date"] <= yday.isoformat()]
    out_to_yday = [p for p in out_month if p["date"] <= yday.isoformat()]
    in_today = [p for p in in_month if p["date"] == today.isoformat()]
    out_today = [p for p in out_month if p["date"] == today.isoformat()]

    try:
        bal_now, accts_open = get_accounts_balance()
        bal_line = f"💼 На счетах сейчас: {fmt(bal_now)} ₽ ({accts_open} активных счетов)"
    except Exception as e:
        bal_line = f"💼 На счетах: ошибка запроса баланса ({e})"

    lines = ["💼 Выручка и расходы (Adesk, кассовый метод, прямой API)"]
    lines.append("")
    lines.extend(render_section(
        f"Вчера ({yday.strftime('%d.%m.%Y')})", in_yday, out_yday, show_no_cat_top=3))
    lines.append("")
    days_so_far = (yday - month_start).days + 1
    lines.extend(render_section(
        f"С начала месяца ({month_start.strftime('%d.%m')} — {yday.strftime('%d.%m.%Y')}, "
        f"{days_so_far} дн.)",
        in_to_yday, out_to_yday, show_no_cat_top=3))
    lines.append("")

    _, _, in_today_total = aggregate_income(in_today)
    _, out_today_total, _ = aggregate_outcome(out_today)
    lines.append(f"Сегодня ({today.strftime('%d.%m.%Y')}, идёт):")
    lines.append(
        f"  📥 Доходы: {fmt(in_today_total)} ₽   "
        f"📤 Расходы: {fmt(out_today_total)} ₽")
    lines.append("")
    lines.append(bal_line)

    print("\n".join(lines))


if __name__ == "__main__":
    main()
