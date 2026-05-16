"""PoC 2ГИС stage 1 — Delight Rent Москва, разведка DOM + XHR sniffing."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

CITY = "moscow"
FIRM_ID = "70000001067116654"
URL = f"https://2gis.ru/{CITY}/firm/{FIRM_ID}/tab/reviews"  # сразу на вкладку отзывов
OUT_DIR = Path("/data")
OUT_DIR.mkdir(exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# Метаданные всех XHR, полный body — только для review-like
all_xhr = []
review_xhr = []

def on_response(response):
    url = response.url
    all_xhr.append({"url": url, "status": response.status, "method": response.request.method})
    if "review" in url.lower() or "branches" in url.lower():
        try:
            body = response.text()
            review_xhr.append({
                "url": url,
                "status": response.status,
                "method": response.request.method,
                "size": len(body),
                "body": body[:30_000],
            })
        except Exception as e:
            review_xhr.append({"url": url, "error": str(e)})

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(locale="ru-RU", user_agent=UA,
                               viewport={"width": 1366, "height": 900})
    page = ctx.new_page()
    page.on("response", on_response)

    print(f"goto {URL}", flush=True)
    page.goto(URL, wait_until="load", timeout=45000)
    page.wait_for_timeout(5000)
    print(f"loaded. title={page.title()!r}", flush=True)
    print(f"final URL: {page.url}", flush=True)
    print(f"all XHR captured: {len(all_xhr)}", flush=True)
    print(f"review-related XHR: {len(review_xhr)}", flush=True)

    # Распечатать distinct URL paths с упоминанием review/branches
    distinct = sorted(set(
        x["url"].split("?")[0] for x in all_xhr
        if "review" in x["url"].lower() or "branches" in x["url"].lower() or "ratings" in x["url"].lower()
    ))
    print("\n=== Review-like URL prefixes ===")
    for u in distinct[:15]:
        print(f"  {u}")

    # Поискать review containers по нескольким вариантам селекторов
    selectors_to_count = [
        '[itemprop="review"]',
        '[data-review-id]',
        '.review',
        '[class*="review-item"]',
        '[class*="ReviewItem"]',
        '[class*="comment"]',
        'article',
    ]
    print("\n=== DOM element counts ===")
    for sel in selectors_to_count:
        try:
            count = page.locator(sel).count()
            if count > 0:
                print(f"  {sel}: {count}")
        except Exception as e:
            print(f"  {sel}: error {e}")

    # Dump
    html = page.content()
    (OUT_DIR / "2gis_msk_v1.html").write_text(html, encoding="utf-8")
    page.screenshot(path=str(OUT_DIR / "2gis_msk_v1.png"), full_page=False)
    (OUT_DIR / "2gis_msk_v1_xhr.json").write_text(
        json.dumps(review_xhr, ensure_ascii=False, indent=2)[:400_000],
        encoding="utf-8")
    (OUT_DIR / "2gis_msk_v1_all_urls.json").write_text(
        json.dumps(all_xhr, ensure_ascii=False, indent=2)[:200_000],
        encoding="utf-8")

    print(f"\nFiles saved: HTML, screenshot, xhr.json, all_urls.json")
    print(f"HTML size: {(OUT_DIR / '2gis_msk_v1.html').stat().st_size:,} bytes")
    browser.close()
