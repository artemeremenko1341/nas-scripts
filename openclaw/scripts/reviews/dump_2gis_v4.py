"""PoC 2ГИС stage 3 — обход museum guard через pre-set cookie."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

CITY = "moscow"
FIRM_ID = "70000001067116654"
TARGET = f"https://2gis.ru/{CITY}/firm/{FIRM_ID}/tab/reviews"
OUT_DIR = Path("/data")
OUT_DIR.mkdir(exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

all_xhr = []
review_xhr = []

def on_response(response):
    url = response.url
    all_xhr.append({"url": url, "status": response.status, "method": response.request.method})
    if "review" in url.lower() or "branches" in url.lower():
        try:
            body = response.text()
            review_xhr.append({
                "url": url, "status": response.status, "size": len(body),
                "body": body[:30_000],
            })
        except Exception as e:
            review_xhr.append({"url": url, "error": str(e)})

stealth = Stealth()

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    ctx = browser.new_context(
        locale="ru-RU", user_agent=UA,
        viewport={"width": 1366, "height": 900},
        timezone_id="Europe/Moscow",
        extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
    )
    # ⭐ Pre-set museum bypass cookie
    ctx.add_cookies([{
        "name": "dg5_museum_accept",
        "value": "true",
        "domain": ".2gis.ru",
        "path": "/",
    }])

    page = ctx.new_page()
    stealth.apply_stealth_sync(page)
    page.on("response", on_response)

    print(f"goto {TARGET}", flush=True)
    page.goto(TARGET, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(6000)
    print(f"final URL: {page.url}", flush=True)
    print(f"title: {page.title()!r}", flush=True)

    if "museum" in page.url.lower():
        print("⚠️ STILL ON MUSEUM", flush=True)
    else:
        print("✅ MUSEUM BYPASSED", flush=True)

    print(f"\nall XHR: {len(all_xhr)}, review-like: {len(review_xhr)}", flush=True)

    print("\n=== DOM element counts ===")
    for sel in [
        '[itemprop="review"]',
        '[data-review-id]',
        '[class*="ReviewItem"]',
        '[class*="review-item"]',
        'div[class*="card"]:has-text("отзыв")',
        'article',
        'h1',
    ]:
        try:
            c = page.locator(sel).count()
            if c > 0:
                print(f"  {sel}: {c}")
        except Exception:
            pass

    # Если бypass прошёл — попробуем найти XHR на reviews API
    review_urls = sorted(set(
        x["url"].split("?")[0] for x in all_xhr
        if "review" in x["url"].lower() or "branches" in x["url"].lower()
    ))
    print("\n=== Review-related URLs captured ===")
    for u in review_urls[:15]:
        print(f"  {u}")

    html = page.content()
    (OUT_DIR / "2gis_msk_v4.html").write_text(html, encoding="utf-8")
    page.screenshot(path=str(OUT_DIR / "2gis_msk_v4.png"), full_page=False)
    (OUT_DIR / "2gis_msk_v4_xhr.json").write_text(
        json.dumps(review_xhr, ensure_ascii=False, indent=2)[:400_000], encoding="utf-8")

    print(f"\nHTML: {(OUT_DIR / '2gis_msk_v4.html').stat().st_size:,} bytes")
    browser.close()
