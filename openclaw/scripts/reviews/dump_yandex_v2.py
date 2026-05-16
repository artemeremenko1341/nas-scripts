"""PoC stage 1.5 — Яндекс.Карты Москва, scrollIntoView на последний review для lazy-load."""
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

    # клик на вкладку отзывы
    page.click('[role="tab"]:has-text("Отзыв")', timeout=5000)
    print("clicked reviews tab", flush=True)
    page.wait_for_timeout(3000)

    # итеративный scrollIntoView последнего отзыва — триггерит lazy-load
    prev_count = 0
    for i in range(40):
        count = page.locator('.business-review-view').count()
        if count == prev_count and i > 3:
            # три попытки подряд без прироста — выход
            stalled = stalled + 1 if 'stalled' in dir() else 1
            if stalled >= 3:
                print(f"no growth for 3 iterations, stopping at {count} reviews", flush=True)
                break
        else:
            stalled = 0
        prev_count = count

        # scrollIntoView на последний контейнер
        page.evaluate("""
            const items = document.querySelectorAll('.business-review-view');
            if (items.length) items[items.length - 1].scrollIntoView({behavior: 'instant', block: 'end'});
        """)
        page.wait_for_timeout(1200)
        print(f"iter {i+1}: {count} reviews loaded", flush=True)

    final_count = page.locator('.business-review-view').count()
    print(f"\nFINAL: {final_count} review containers in DOM", flush=True)

    html = page.content()
    (OUT_DIR / "yandex_msk_v2.html").write_text(html, encoding="utf-8")
    page.screenshot(path=str(OUT_DIR / "yandex_msk_v2_screenshot.png"), full_page=False)
    print(f"HTML: {(OUT_DIR / 'yandex_msk_v2.html').stat().st_size:,} bytes")

    browser.close()
