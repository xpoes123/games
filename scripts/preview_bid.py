"""Walk through join → deal → enter bidding, screenshot at each step."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright


URL = "http://127.0.0.1:7781"
OUT_DIR = Path("/tmp")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 800})
        page.goto(URL)
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(OUT_DIR / "bid-1-entry.png"))

        page.fill("#player-name", "alice")
        page.click("#join-btn")
        page.wait_for_selector("#game:not([hidden])")
        page.screenshot(path=str(OUT_DIR / "bid-2-joined.png"))

        page.click("#deal-btn")
        # Wait for deal animation to settle and bid area to appear
        page.wait_for_selector("#bid-area:not([hidden])")
        time.sleep(5)  # 52 cards × 65ms stagger + 280ms flight + 300ms sort delay + ~600ms sort
        page.screenshot(path=str(OUT_DIR / "bid-3-bidding.png"))

        # Submit a 2H bid
        page.select_option("#bid-level", "2")
        page.select_option("#bid-strain", "H")
        page.click("#bid-submit")
        time.sleep(0.8)
        page.screenshot(path=str(OUT_DIR / "bid-4-after-bid.png"))

        browser.close()

    print("ok")


if __name__ == "__main__":
    main()
