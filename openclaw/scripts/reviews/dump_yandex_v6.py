"""PoC stage 2.1 — XHR sniffing (metadata only), load instead of networkidle."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE_ID = "85753359038"
URL = f"https://yandex.ru/profile/{PROFILE_ID}?lang=ru"
OUT_DIR = Path("/data")
OUT_DIR.mkdir(exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# Полные тексты XHR с отзывами — собираем по фильтру URL
review_xhr_bodies = []
all_xhr_urls = []

def on_response(response):
    url = response.url
    all_xhr_urls.append({"url": url, "status": response.status, "method": response.request.method})
    if any(k in url.lower() for k in ["review", "fetchreview", "ugc", "feedback"]):
        try:
            body = response.text()
            review_xhr_bodies.append({
                "url": url,
                "status": response.status,
                "method": response.request.method,
                "size": len(body),
                "body": body,
            })
        except Exception as e:
            review_xhr_bodies.append({"url": url, "error": str(e)})

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(locale="ru-RU", user_agent=UA,
                               viewport={"width": 1366, "height": 900})
    page = ctx.new_page()
    page.on("response", on_response)

    print(f"goto {URL}", flush=True)
    page.goto(URL, wait_until="load", timeout=45000)
    page.wait_for_timeout(4000)
    print(f"loaded. title={page.title()!r}", flush=True)
    print(f"all XHR captured so far: {len(all_xhr_urls)}", flush=True)
    print(f"review-like XHR: {len(review_xhr_bodies)}", flush=True)

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
            pass

    page.wait_for_timeout(4500)
    print(f"after click — total XHR: {len(all_xhr_urls)}, review-like: {len(review_xhr_bodies)}", flush=True)

    # Найти scrollable container
    container_info = page.evaluate("""
        () => {
            const review = document.querySelector('.business-review-view');
            if (!review) return null;
            let el = review.parentElement;
            while (el) {
                const cs = getComputedStyle(el);
                if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 50) {
                    return {
                        className: el.className,
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
                        if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 50) {
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
                print(f"scroll iter {i+1}: {now} reviews (+{now - prev}), XHR total={len(all_xhr_urls)}", flush=True)
            if now == prev and i > 4:
                break

    final = page.locator('.business-review-view').count()
    print(f"\nFINAL: {final} review containers, {len(review_xhr_bodies)} review-like XHR", flush=True)

    # Дамп URL-листа всех XHR
    review_url_patterns = sorted(set(
        x["url"].split("?")[0] for x in all_xhr_urls
        if any(k in x["url"].lower() for k in ["review", "ugc", "feedback", "fetch"])
    ))
    print("\n=== Distinct review-like URL prefixes ===")
    for url in review_url_patterns[:20]:
        print(f"  {url}")

    html = page.content()
    (OUT_DIR / "yandex_msk_v6.html").write_text(html, encoding="utf-8")
    page.screenshot(path=str(OUT_DIR / "yandex_msk_v6.png"), full_page=False)
    (OUT_DIR / "yandex_msk_v6_xhr.json").write_text(
        json.dumps(review_xhr_bodies, ensure_ascii=False, indent=2)[:500_000], encoding="utf-8")
    (OUT_DIR / "yandex_msk_v6_all_urls.json").write_text(
        json.dumps(all_xhr_urls, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nFiles: HTML, screenshot, xhr.json ({len(review_xhr_bodies)} review-XHR), all_urls.json ({len(all_xhr_urls)} total)")

    browser.close()
