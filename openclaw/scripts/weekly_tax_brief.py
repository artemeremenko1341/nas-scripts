#!/usr/bin/env python3
"""
Еженедельный налоговый брифинг по ИП Ерёменко Артём Валерьевич.
Читает накопленные дневные файлы из data/adesk_daily/.
Выручка за прошлую неделю пн-пт + резерв УСН 11%.
"""
import os
import sys
sys.path.insert(0, '/volume1/homes/artemere-7601341/scripts')
import _env  # noqa: F401  (loads .env into os.environ)

import json
from datetime import datetime, timedelta, date
from pathlib import Path

DATA_DIR = Path("/volume1/homes/artemere-7601341/scripts/openclaw/data/adesk_daily")
BOT_TOKEN = os.environ['TG_BOT_TOKEN']
CHAT_ID = os.environ['TG_CHAT_ID']
TAX_RATE = 0.11

CITIES_ORDER = ["Москва", "Санкт-Петербург", "Нижний Новгород", "Казань",
                "Самара", "Воронеж", "Ростов-на-Дону", "Краснодар"]


def load_week(mon: date, fri: date):
    income_by_city = {c: 0.0 for c in CITIES_ORDER}
    income_other = 0.0
    income_total = 0.0
    outcome_by_cat = {}
    outcome_total = 0.0
    days_found = []

    current = mon
    while current <= fri:
        f = DATA_DIR / f"{current.strftime('%Y-%m-%d')}.json"
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            days_found.append(current.strftime("%d.%m"))
            for city in CITIES_ORDER:
                income_by_city[city] += data["income"].get(city, 0)
            income_other += data["income"].get("Прочее", 0)
            income_total += data["income_total"]
            for cat, amt in data["outcome"].items():
                outcome_by_cat[cat] = outcome_by_cat.get(cat, 0) + amt
            outcome_total += data["outcome_total"]
        current += timedelta(days=1)

    return {
        "income_by_city": income_by_city,
        "income_other": income_other,
        "income_total": income_total,
        "outcome_by_cat": outcome_by_cat,
        "outcome_total": outcome_total,
        "days_found": days_found,
    }


def send_tg(text):
    # api.telegram.org is blocked from this NAS WAN. Two-step fallback:
    #   1) curl --proxy via local v2raya (canonical path, same as notify_brief.sh).
    #   2) curl --resolve to known-good TG IP (emergency, in case v2raya route flaps).
    # Override defaults via env: TG_HTTP_PROXY, TG_API_IPS (comma-sep).
    import subprocess, os
    payload = json.dumps({"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"})
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    errors = []

    proxy = os.environ.get("TG_HTTP_PROXY", "http://127.0.0.1:20171")
    try:
        r = subprocess.run(
            ["curl", "-fsS", "-m", "15", "--proxy", proxy,
             "-X", "POST", url,
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode == 0:
            return json.loads(r.stdout)
        errors.append(f"proxy {proxy}: rc={r.returncode} {r.stderr.strip()[:120]}")
    except Exception as e:
        errors.append(f"proxy {proxy}: {type(e).__name__}: {e}")

    for ip in [s.strip() for s in os.environ.get("TG_API_IPS", "149.154.167.220").split(",") if s.strip()]:
        try:
            r = subprocess.run(
                ["curl", "-fsS", "-m", "10",
                 "--resolve", f"api.telegram.org:443:{ip}",
                 "-X", "POST", url,
                 "-H", "Content-Type: application/json",
                 "-d", payload],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0:
                return json.loads(r.stdout)
            errors.append(f"resolve {ip}: rc={r.returncode} {r.stderr.strip()[:120]}")
        except Exception as e:
            errors.append(f"resolve {ip}: {type(e).__name__}: {e}")

    raise RuntimeError("send_tg failed via all paths: " + " | ".join(errors))

def main():
    today = datetime.now().date()
    last_mon = today - timedelta(days=today.weekday() + 7)
    last_fri = last_mon + timedelta(days=4)
    period = f"{last_mon.strftime('%d.%m')}–{last_fri.strftime('%d.%m.%Y')}"

    data = load_week(last_mon, last_fri)

    if not data["days_found"]:
        send_tg(f"⚠️ Нет данных за неделю {period}. Накопление началось недавно.")
        return

    tax_reserve = data["income_total"] * TAX_RATE
    net = data["income_total"] - data["outcome_total"]
    missing = 5 - len(data["days_found"])

    lines = [
        f"📊 <b>Выручка ИП Артём Ерёменко</b>",
        f"<b>Неделя {period}</b>",
    ]
    if missing > 0:
        lines.append(f"⚠️ Данные за {missing} дн. отсутствуют")
    lines.append("")
    lines.append(f"📈 <b>Доходы: {data['income_total']:,.0f} ₽</b>".replace(",", " "))

    for city in CITIES_ORDER:
        amt = data["income_by_city"][city]
        if amt > 0:
            lines.append(f"  {city}: {amt:,.0f} ₽".replace(",", " "))
    if data["income_other"] > 0:
        lines.append(f"  Прочее: {data['income_other']:,.0f} ₽".replace(",", " "))

    lines.append("")
    if data["outcome_by_cat"]:
        lines.append(f"📉 <b>Расходы: {data['outcome_total']:,.0f} ₽</b>".replace(",", " "))
        for name, amt in sorted(data["outcome_by_cat"].items(), key=lambda x: -x[1])[:5]:
            short = name.split(".")[-1].strip()[:35]
            lines.append(f"  {short}: {amt:,.0f} ₽".replace(",", " "))
    else:
        lines.append("📉 Расходы: 0 ₽")

    lines += [
        "",
        f"💰 Чистый поток: <b>{net:+,.0f} ₽</b>".replace(",", " "),
        "",
        f"🧾 <b>Отложить на налоги (11%):</b>",
        f"   = <b>{tax_reserve:,.0f} ₽</b>".replace(",", " "),
    ]

    zeros = [c for c in CITIES_ORDER if data["income_by_city"][c] == 0]
    if zeros:
        lines += ["", f"⚠️ Нет поступлений: {', '.join(zeros)}"]

    send_tg("\n".join(lines))
    print(f"✅ Отправлено. Период {period}, доходы {data['income_total']:,.0f} ₽")


if __name__ == "__main__":
    main()
