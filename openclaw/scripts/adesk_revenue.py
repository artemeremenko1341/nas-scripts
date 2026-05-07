#!/usr/bin/env python3
"""Выручка delight.rent из Adesk (кассовый метод). Показывает всё:
доходы по 6 регионам + другие категории, расходы по категориям, балансы.
Источник: data/adesk_daily/*.json (формирует adesk_daily_save.py).
"""
import json
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path("/volume1/homes/artemere-7601341/scripts/openclaw/data/adesk_daily")
ACTIVE_CITIES = ["Москва", "Санкт-Петербург", "Нижний Новгород", "Казань",
                 "Ростов-на-Дону", "Краснодар"]
CLOSED_CITIES = ["Самара", "Воронеж"]


def fmt(n):
    return f"{n:,.0f}".replace(",", " ")


def read_day(d):
    f = DATA_DIR / f"{d.isoformat()}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def split_income(income):
    cities = {c: float(income.get(c, 0)) for c in ACTIVE_CITIES}
    cities_total = sum(cities.values())
    other = {k: float(v) for k, v in income.items()
             if k not in ACTIVE_CITIES and k not in CLOSED_CITIES and float(v) != 0}
    other_total = sum(other.values())
    return cities, cities_total, other, other_total


def main():
    today = date.today()
    yday = today - timedelta(days=1)

    lines = ["💼 Выручка и расходы (Adesk, кассовый метод)"]
    lines.append("")

    # === Вчера ===
    yd = read_day(yday)
    lines.append(f"Вчера ({yday.strftime('%d.%m.%Y')}):")
    if yd:
        cities, cities_total, other, other_total = split_income(yd.get("income", {}))
        lines.append("  📥 Доходы по регионам:")
        for c in sorted(cities, key=lambda x: -cities[x]):
            mark = "" if cities[c] > 0 else " —"
            lines.append(f"    {c}: {fmt(cities[c])} ₽{mark}")
        lines.append(f"    Итого по 6 регионам: {fmt(cities_total)} ₽")
        if other:
            lines.append("  📥 Прочие поступления:")
            for cat in sorted(other, key=lambda x: -other[x]):
                lines.append(f"    {cat}: {fmt(other[cat])} ₽")
            lines.append(f"    Итого прочих: {fmt(other_total)} ₽")
        income_total = float(yd.get("income_total", cities_total + other_total))
        lines.append(f"  ✅ ВСЕГО доходов: {fmt(income_total)} ₽")

        outcome = yd.get("outcome", {})
        outcome_total = float(yd.get("outcome_total", 0))
        if outcome:
            lines.append("  📤 Расходы по категориям:")
            for cat in sorted(outcome, key=lambda x: -float(outcome[x])):
                lines.append(f"    {cat}: {fmt(float(outcome[cat]))} ₽")
            lines.append(f"  ❌ ВСЕГО расходов: {fmt(outcome_total)} ₽")
        else:
            lines.append("  📤 Расходов не было")
        bal = income_total - outcome_total
        lines.append(f"  💰 Баланс дня: {'+' if bal >= 0 else ''}{fmt(bal)} ₽")
    else:
        lines.append(f"  ⚠️ нет данных за {yday.isoformat()} (adesk_daily_save не запускался?)")

    lines.append("")

    # === С начала месяца ===
    month_start = today.replace(day=1)
    m_cities = {c: 0.0 for c in ACTIVE_CITIES}
    m_other = {}
    m_outcome = {}
    m_income_total = 0.0
    m_outcome_total = 0.0
    days_found = 0
    days_missing = []
    cur = month_start
    while cur <= today:
        d = read_day(cur)
        if d:
            cities, _, other, _ = split_income(d.get("income", {}))
            for c, v in cities.items():
                m_cities[c] += v
            for cat, v in other.items():
                m_other[cat] = m_other.get(cat, 0) + v
            for cat, v in d.get("outcome", {}).items():
                m_outcome[cat] = m_outcome.get(cat, 0) + float(v)
            m_income_total += float(d.get("income_total", 0))
            m_outcome_total += float(d.get("outcome_total", 0))
            days_found += 1
        elif cur < today:
            days_missing.append(cur.isoformat())
        cur += timedelta(days=1)

    days_so_far = (today - month_start).days + 1
    lines.append(f"С начала месяца ({month_start.strftime('%d.%m')} — {today.strftime('%d.%m.%Y')}):")
    lines.append("  📥 Доходы по регионам:")
    for c in sorted(m_cities, key=lambda x: -m_cities[x]):
        lines.append(f"    {c}: {fmt(m_cities[c])} ₽")
    cities_sum = sum(m_cities.values())
    lines.append(f"    Итого по 6 регионам: {fmt(cities_sum)} ₽")
    if m_other:
        lines.append("  📥 Прочие поступления:")
        for cat in sorted(m_other, key=lambda x: -m_other[x]):
            lines.append(f"    {cat}: {fmt(m_other[cat])} ₽")
        lines.append(f"    Итого прочих: {fmt(sum(m_other.values()))} ₽")
    lines.append(f"  ✅ ВСЕГО доходов: {fmt(m_income_total)} ₽")
    if m_outcome:
        lines.append("  📤 Расходы по категориям (топ-10):")
        for cat in sorted(m_outcome, key=lambda x: -m_outcome[x])[:10]:
            lines.append(f"    {cat}: {fmt(m_outcome[cat])} ₽")
        lines.append(f"  ❌ ВСЕГО расходов: {fmt(m_outcome_total)} ₽")
    bal_m = m_income_total - m_outcome_total
    lines.append(f"  💰 Баланс месяца: {'+' if bal_m >= 0 else ''}{fmt(bal_m)} ₽")
    lines.append(f"  📅 Дней в архиве: {days_found}/{days_so_far}")
    if days_missing:
        lines.append(f"  ⚠️ нет данных: {', '.join(days_missing[:3])}{'...' if len(days_missing)>3 else ''}")

    print(chr(10).join(lines))


if __name__ == "__main__":
    main()
