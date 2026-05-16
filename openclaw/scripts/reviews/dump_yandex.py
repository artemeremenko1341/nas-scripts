"""PoC stage 1 — открыть карточку Яндекс.Карт Delight Rent Москва, скроллить отзывы, сохранить HTML+скриншот."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE_ID = "85753359038"  # Москва
URL = f"https://yandex.ru/profile/{PROFILE_ID}?lang=ru"
OUT_DIR = Path("/data")
OUT_DIR.mkdir(exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(locale="ru-RU", user_agent=UA,
                               viewport={"width": 1366, "height": 900})
    page = ctx.new_page()
    print(f"goto {URL}", flush=True)
    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    print(f"after load: title={page.title()!r} url={page.url}", flush=True)

    clicked = False
    for selector in [
        'a[href*="reviews"]',
        '[role="tab"]:has-text("Отзыв")',
        'div:has-text("Отзывы"):not(:has(*))',
    ]:
        try:
            page.click(selector, timeout=3000)
            print(f"clicked tab via {selector}", flush=True)
            clicked = True
            break
        except Exception as e:
            print(f"selector {selector} failed: {e}", flush=True)
    if not clicked:
        print("could not click reviews tab — continuing anyway", flush=True)

    page.wait_for_timeout(3000)

    for i in range(8):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)
        print(f"scroll {i+1}/8 done", flush=True)

    html = page.content()
    (OUT_DIR / "yandex_msk_dump.html").write_text(html, encoding="utf-8")
    page.screenshot(path=str(OUT_DIR / "yandex_msk_screenshot.png"), full_page=False)

    print(f"\nHTML: {(OUT_DIR / 'yandex_msk_dump.html').stat().st_size:,} bytes")
    print(f"Screenshot: {(OUT_DIR / 'yandex_msk_screenshot.png').stat().st_size:,} bytes")
    print(f"Final URL: {page.url}")
    print(f"Final title: {page.title()!r}")

    browser.close()
