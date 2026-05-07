#!/usr/bin/env python3
"""
Сохраняет ДДС ИП Ерёменко Артём Валерьевич за вчерашний день в JSON файл.
Запускается каждый день в 23:30 МСК.
Файлы: data/adesk_daily/YYYY-MM-DD.json
"""
import os
import sys
sys.path.insert(0, '/volume1/homes/artemere-7601341/scripts')
import _env  # noqa: F401  (loads .env into os.environ)

import urllib.request
import json
import ssl
from datetime import datetime, timedelta, date
from pathlib import Path

ADESK_TOKEN = os.environ['ADESK_TOKEN']
ADESK_BASE = "https://api.adesk.ru/v1"
LEGAL_ENTITY_IDS = {76749, 76746}  # ИП Ерёменко Артём Валерьевич + ООО Эльвиль
DATA_DIR = Path("/volume1/homes/artemere-7601341/scripts/openclaw/data/adesk_daily")

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
INCOME_IDS = {cat_id for p, u, _ in INCOME_CITIES for cat_id in (p, u)}

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Rocky/1.0"})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        return json.loads(r.read())


def get_dds_for_date(target_date: date):
    """ДДС за конкретный день — фильтр по date поля транзакции."""
    ds = target_date.strftime("%d.%m.%Y")
    ds_key = target_date.strftime("%Y-%m-%d")
    income = {}
    income_total = 0.0
    outcome = {}
    outcome_total = 0.0

    def is_ip_artem(t):
        ba = t.get("bankAccount") or {}
        le = ba.get("legalEntity") or {}
        return le.get("id") in LEGAL_ENTITY_IDS

    for tx_type in ["income", "outcome"]:
        start = 0
        seen_ids = set()
        all_done = False
        while not all_done:
            url = (f"{ADESK_BASE}/transactions?api_token={ADESK_TOKEN}"
                   f"&type={tx_type}&start={start}&length=100")
            data = fetch(url)
            txns = data.get("transactions", [])
            if not txns:
                break
            new = False
            for t in txns:
                if t["id"] in seen_ids:
                    continue
                seen_ids.add(t["id"])
                t_date = t.get("dateIso", "")
                # API не сортирует по дате (порядок ~по id desc) - пейджить ВСЕ страницы.
                # Ранний выход по t_date < ds_key даёт ложный 0 для расходов (баг 2026-05-05).
                if t_date != ds_key:
                    continue
                if t.get("isTransfer"):
                    continue
                # Фильтр по legal entity снят (вариант B 2026-05-05) - тянем всё из Adesk-аккаунта.
                # is_ip_artem() оставлен в коде для возможного возврата.
                new = True
                amt = float(t.get("amount", 0))
                cat = t.get("category", {})
                cat_name = cat.get("name", "Прочее")
                cat_id = cat.get("id")
                if tx_type == "income":
                    income_total += amt
                    if cat_id in INCOME_IDS:
                        for p, u, city in INCOME_CITIES:
                            if cat_id in (p, u):
                                income[city] = income.get(city, 0) + amt
                                break
                    else:
                        # Все не-городские категории сохраняем по их cat_name (не сваливаем в одну корзину Прочее)
                        income[cat_name] = income.get(cat_name, 0) + amt
                else:
                    outcome_total += amt
                    outcome[cat_name] = outcome.get(cat_name, 0) + amt
            if all_done:
                break
            start += len(txns)

    return {
        "date": ds_key,
        "income": income,
        "income_total": income_total,
        "outcome": outcome,
        "outcome_total": outcome_total,
    }


def main():
    import sys
    if len(sys.argv) > 1:
        yesterday = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    else:
        yesterday = datetime.now().date() - timedelta(days=1)
    data = get_dds_for_date(yesterday)

    out_file = DATA_DIR / f"{data['date']}.json"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ Сохранено: {out_file}")
    print(f"   Доходы: {data['income_total']:,.0f} ₽")
    print(f"   Расходы: {data['outcome_total']:,.0f} ₽")
    print(f"   Баланс: {data['income_total'] - data['outcome_total']:+,.0f} ₽")


if __name__ == "__main__":
    main()
