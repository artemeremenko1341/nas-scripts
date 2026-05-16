"""PoC stage 2 — XHR sniffing + структурный парсинг отзывов Яндекс.Карт."""
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE_ID = "85753359038"
URL = f"https://yandex.ru/profile/{PROFILE_ID}?lang=ru"
OUT_DIR = Path("/data")
OUT_DIR.mkdir(exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

captured = []

def on_response(response):
    url = response.url
    if any(k in url.lower() for k in ["review", "fetchreview"]):
        try:
            body = response.text()
            captured.append({
                "url": url,
                "status": response.status,
                "size": len(body),
                "preview": body[:400],
            })
        except Exception as e:
            captured.append({"url": url, "error": str(e)})

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(locale="ru-RU", user_agent=UA,
                               viewport={"width": 1366, "height": 900})
    page = ctx.new_page()
    page.on("response", on_response)

    print(f"goto {URL}", flush=True)
    page.goto(URL, wait_until="networkidle", timeout=45000)
    print(f"loaded, title={page.title()!r}", flush=True)
    print(f"captured before tab click: {len(captured)} responses", flush=True)

    # Попытка перейти на вкладку Отзывы любым способом
    for attempt, selector in enumerate([
        '[role="tab"]:has-text("Отзыв")',
        'a[href*="reviews"]',
        'div.tabs-select-view__title:has-text("Отзыв")',
        'div.tabs-select-view__label:has-text("Отзыв")',
    ]):
        try:
            page.click(selector, timeout=4000, force=True)
            print(f"clicked via {selector!r}", flush=True)
            break
        except Exception:
            print(f"attempt {attempt+1} ({selector!r}) failed", flush=True)

    page.wait_for_timeout(4000)
    print(f"captured after click: {len(captured)} responses", flush=True)

    # Найти scrollable container в side-panel через JS
    container_info = page.evaluate("""
        () => {
            const review = document.querySelector('.business-review-view');
            if (!review) return null;
            let el = review.parentElement;
            while (el) {
                const cs = getComputedStyle(el);
                if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll') && el.scrollHeight > el.clientHeight) {
                    return {
                        className: el.className,
                        id: el.id,
                        scrollHeight: el.scrollHeight,
                        clientHeight: el.clientHeight,
                    };
                }
                el = el.parentElement;
            }
            return null;
        }
    """)
    print(f"scrollable container: {container_info}", flush=True)

    # Скроллим этот контейнер
    if container_info:
        for i in range(30):
            prev = page.locator('.business-review-view').count()
            page.evaluate("""
                () => {
                    const review = document.querySelector('.business-review-view');
                    if (!review) return;
                    let el = review.parentElement;
                    while (el) {
                        const cs = getComputedStyle(el);
                        if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll') && el.scrollHeight > el.clientHeight) {
                            el.scrollTop = el.scrollHeight;
                            return;
                        }
                        el = el.parentElement;
                    }
                }
            """)
            page.wait_for_timeout(1500)
            now = page.locator('.business-review-view').count()
            if i % 3 == 0 or now != prev:
                print(f"scroll iter {i+1}: {now} reviews (+{now - prev})", flush=True)
            if now == prev and i > 4:
                break

    final = page.locator('.business-review-view').count()
    print(f"\nFINAL: {final} review containers, {len(captured)} XHR captures", flush=True)

    # Сохраняем HTML, скриншот, XHR
    html = page.content()
    (OUT_DIR / "yandex_msk_v5.html").write_text(html, encoding="utf-8")
    page.screenshot(path=str(OUT_DIR / "yandex_msk_v5.png"), full_page=False)
    (OUT_DIR / "yandex_msk_v5_xhr.json").write_text(
        json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"HTML: {(OUT_DIR / 'yandex_msk_v5.html').stat().st_size:,} bytes")
    print(f"XHR log: {(OUT_DIR / 'yandex_msk_v5_xhr.json').stat().st_size:,} bytes")

    browser.close()
