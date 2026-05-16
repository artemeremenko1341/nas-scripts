"""PoC stage 1.7 — mouse.wheel в центре side-panel."""
from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE_ID = "85753359038"
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
    page.goto(URL, wait_until="networkidle", timeout=45000)

    # клик на отзывы
    page.click('div.tabs-select-view__title:has-text("Отзыв")', timeout=5000)
    print("clicked reviews tab", flush=True)
    page.wait_for_timeout(3500)

    # Yandex.Maps side-panel — обычно её координаты ~ x=100..400, y=200..800
    # Поставим мышь в центр (200, 500) и крутим колесо вниз
    page.mouse.move(200, 500)

    stalled = 0
    prev_count = 0
    for i in range(60):
        count = page.locator('.business-review-view').count()
        if count == prev_count:
            stalled += 1
            if stalled >= 6:
                print(f"no growth for 6 iter, stop at {count}", flush=True)
                break
        else:
            stalled = 0
            print(f"iter {i+1}: {count} reviews (+{count - prev_count})", flush=True)
        prev_count = count
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(800)

    final = page.locator('.business-review-view').count()
    print(f"\nFINAL: {final} review containers", flush=True)

    html = page.content()
    (OUT_DIR / "yandex_msk_v4.html").write_text(html, encoding="utf-8")
    page.screenshot(path=str(OUT_DIR / "yandex_msk_v4.png"), full_page=False)
    print(f"HTML: {(OUT_DIR / 'yandex_msk_v4.html').stat().st_size:,} bytes")

    browser.close()
