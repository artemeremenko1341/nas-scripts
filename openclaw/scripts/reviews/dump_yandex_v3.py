"""PoC stage 1.6 — устойчивые ожидания + scrollIntoView."""
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
    page.goto(URL, wait_until="networkidle", timeout=45000)
    print(f"loaded: title={page.title()!r}", flush=True)

    # Ждём появления любого review-tab варианта, retry до 20 сек
    tab_clicked = False
    for selector in [
        '[role="tab"]:has-text("Отзыв")',
        'a[href*="reviews"]',
        'div.tabs-select-view__title:has-text("Отзыв")',
    ]:
        try:
            page.wait_for_selector(selector, timeout=8000, state="visible")
            page.click(selector, timeout=5000)
            print(f"clicked tab via {selector!r}", flush=True)
            tab_clicked = True
            break
        except Exception as e:
            print(f"selector {selector!r} failed: {type(e).__name__}", flush=True)
    if not tab_clicked:
        print("WARN: review tab not clicked", flush=True)

    page.wait_for_timeout(3000)

    # ждём появления хотя бы одного business-review-view
    try:
        page.wait_for_selector('.business-review-view', timeout=10000)
    except Exception as e:
        print(f"WARN: no business-review-view in 10s: {e}", flush=True)

    # итеративный scrollIntoView
    stalled = 0
    prev_count = 0
    for i in range(50):
        count = page.locator('.business-review-view').count()
        if count == prev_count and i > 2:
            stalled += 1
            if stalled >= 4:
                print(f"no growth for 4 iterations, stop at {count}", flush=True)
                break
        else:
            stalled = 0
        prev_count = count

        page.evaluate("""
            const items = document.querySelectorAll('.business-review-view');
            if (items.length) items[items.length - 1].scrollIntoView({behavior: 'instant', block: 'end'});
        """)
        page.wait_for_timeout(1200)
        if i % 5 == 0 or count != prev_count:
            print(f"iter {i+1}: {count} reviews", flush=True)

    final = page.locator('.business-review-view').count()
    print(f"\nFINAL: {final} review containers", flush=True)

    html = page.content()
    (OUT_DIR / "yandex_msk_v3.html").write_text(html, encoding="utf-8")
    page.screenshot(path=str(OUT_DIR / "yandex_msk_v3.png"), full_page=False)
    print(f"HTML: {(OUT_DIR / 'yandex_msk_v3.html').stat().st_size:,} bytes")

    browser.close()
