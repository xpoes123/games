"""Deal locally and dump the sorted hand to verify ordering."""
from __future__ import annotations

import time
from playwright.sync_api import sync_playwright


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        page.goto("http://127.0.0.1:7781")
        page.fill("#player-name", "alice")
        page.click("#join-btn")
        page.wait_for_selector("#game:not([hidden])")
        page.click("#deal-btn")
        page.wait_for_selector("#bid-area:not([hidden])")
        time.sleep(5)
        # Read the dataset attributes of all visible cards in left→right DOM
        # order in the bottom seat.
        cards = page.eval_on_selector_all(
            ".seat.bottom .hand .card",
            "els => els.map(e => ({rank: e.dataset.rank, suit: e.dataset.suit, left: parseFloat(e.style.left)}))",
        )
        # Sort by left so we see the visual L→R order
        cards.sort(key=lambda c: c["left"])
        print("visual order:")
        for c in cards:
            print(f"  {c['rank']:>2}{c['suit']}  left={c['left']}")
        page.screenshot(path="/tmp/bid-debug.png")
        browser.close()


if __name__ == "__main__":
    main()
