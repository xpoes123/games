"""Try joining the bridge game and report any console errors."""
import time
from playwright.sync_api import sync_playwright


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 900, "height": 800})
    errors, console = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
    page.goto("http://127.0.0.1:7781/bridge/")
    page.fill("#player-name", "alice")
    page.click("#join-btn")
    time.sleep(2)
    print("pageerrors:", errors)
    print("console:", console)
    print("game hidden:", page.evaluate("() => document.getElementById('game').hidden"))
    print("entry status:", page.evaluate("() => document.getElementById('entry-status').textContent"))
    page.screenshot(path="/tmp/debug-join.png")
    browser.close()
