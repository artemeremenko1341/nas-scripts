"""Smoke test for Playwright on Synology DSM."""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
    page = browser.new_page()
    page.goto("https://example.com", timeout=30000)
    print("title:", page.title())
    print("h1:", page.text_content("h1"))
    browser.close()
