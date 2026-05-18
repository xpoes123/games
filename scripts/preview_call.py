"""Walk through join → deal → bid → call partner, screenshot each step."""
from __future__ import annotations

import time
from playwright.sync_api import sync_playwright


URL = "http://127.0.0.1:7781"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.goto(URL)
        page.fill("#player-name", "alice")
        page.click("#join-btn")
        page.wait_for_selector("#game:not([hidden])")
        page.click("#deal-btn")
        page.wait_for_selector("#bid-area:not([hidden])")
        time.sleep(5)

        # Bid 2H
        page.select_option("#bid-level", "2")
        page.select_option("#bid-strain", "H")
        page.click("#bid-submit")
        # Empty seats auto-pass; we should land in CALLING
        page.wait_for_function("document.getElementById('call-panel') && !document.getElementById('call-panel').hidden")
        time.sleep(0.4)
        page.screenshot(path="/tmp/call-1-panel.png")

        # Read the hand to pick a card we don't hold
        hand = page.eval_on_selector_all(
            ".seat.bottom .hand .card",
            "els => els.map(e => ({rank: e.dataset.rank, suit: e.dataset.suit}))",
        )
        held = {(c["rank"], c["suit"]) for c in hand}
        # Pick the highest spade we don't hold
        target = None
        for s in ["S", "H", "D", "C"]:
            for r in ["A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"]:
                if (r, s) not in held:
                    target = (r, s)
                    break
            if target:
                break
        print(f"calling {target}")
        page.select_option("#call-rank", target[0])
        page.select_option("#call-suit", target[1])
        page.click("#call-submit")
        time.sleep(0.8)
        page.screenshot(path="/tmp/call-2-after.png")

        browser.close()


if __name__ == "__main__":
    main()
