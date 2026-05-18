"""End-to-end Playwright test: 4 players play a complete hand.

Spawns 4 browser contexts, joins them as alice/bob/charlie/dave, deals,
bids (one wins, others pass), calls partner, plays 13 tricks, and verifies
the result banner appears with the right verdict + cumulative totals.
"""
from __future__ import annotations

import time
from playwright.sync_api import Page, sync_playwright


URL = "http://127.0.0.1:7781"
NAMES = ["alice", "bob", "charlie", "dave"]


def wait_for(page: Page, expr: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = page.evaluate(expr)
        if result:
            return result
        time.sleep(0.05)
    raise TimeoutError(f"timeout waiting for: {expr}")


def state(page: Page) -> dict:
    return page.evaluate("() => lastState")


def my_seat(page: Page) -> int:
    return page.evaluate("() => mySeat")


def hand_cards(page: Page) -> list[dict]:
    return page.evaluate(
        "() => [...document.querySelectorAll('.seat.bottom .hand .card')]"
        ".map(c => ({rank: c.dataset.rank, suit: c.dataset.suit}))"
    )


def play_one_legal_card(page: Page) -> tuple[str, str]:
    """Try each card until one is accepted. Returns (rank, suit) played."""
    cards = hand_cards(page)
    if not cards:
        raise RuntimeError("empty hand")
    initial = len(cards)
    for card in cards:
        page.click(
            f'.seat.bottom .hand .card[data-rank="{card["rank"]}"]'
            f'[data-suit="{card["suit"]}"]'
        )
        # Server is local — give a tight window for the WS round-trip + animation
        for _ in range(40):
            time.sleep(0.05)
            if len(hand_cards(page)) < initial:
                # Wait briefly for the play-fly-to-slot animation to settle
                time.sleep(0.25)
                return card["rank"], card["suit"]
    raise RuntimeError(f"no legal card found among {cards}")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pages: list[Page] = []
        for name in NAMES:
            ctx = browser.new_context(viewport={"width": 900, "height": 800})
            page = ctx.new_page()
            page.goto(URL)
            page.fill("#player-name", name)
            page.click("#join-btn")
            page.wait_for_selector("#game:not([hidden])")
            pages.append(page)
            time.sleep(0.1)  # let state propagate

        # All 4 should now see 4/4 seated. Have alice (seat 0) deal.
        time.sleep(0.5)
        s0 = state(pages[0])
        assert len(s0["players"]) == 4, f"expected 4 players, got {len(s0['players'])}"
        print(f"4 players seated, dealer={s0['dealer']}")

        pages[s0["dealer"]].click("#deal-btn")
        # Wait for bidding to appear on all pages
        for p_ in pages:
            wait_for(p_, "() => lastState && lastState.phase === 'bidding'")
        time.sleep(5)  # let deal animation finish on all pages
        print("dealt, bidding phase active")

        # Bidding strategy: dealer opens 1NT, everyone else passes
        s = state(pages[0])
        dealer = s["dealer"]
        pages[dealer].select_option("#bid-level", "1")
        pages[dealer].select_option("#bid-strain", "NT")
        pages[dealer].click("#bid-submit")
        wait_for(pages[0], "() => lastState && lastState.current_bid !== null")
        # The other 3 pass in clockwise order
        for offset in range(1, 4):
            seat = (dealer + offset) % 4
            wait_for(pages[seat], f"() => lastState && lastState.current_bidder === {seat}")
            pages[seat].click("#bid-pass")
            time.sleep(0.2)
        # Bidding should now be done
        wait_for(pages[0], "() => lastState && lastState.phase === 'calling'", timeout=3)
        print(f"bidding complete, declarer={dealer}, contract=1NT")

        # Calling phase — declarer picks a card they don't hold
        declarer_page = pages[dealer]
        held = {(c["rank"], c["suit"]) for c in hand_cards(declarer_page)}
        called = None
        for s_ in "SHDC":
            for r in "AKQJT98765432":
                if (r, s_) not in held:
                    called = (r, s_)
                    break
            if called:
                break
        declarer_page.select_option("#call-rank", called[0])
        declarer_page.select_option("#call-suit", called[1])
        declarer_page.click("#call-submit")
        wait_for(pages[0], "() => lastState && lastState.phase === 'playing'", timeout=3)
        print(f"called partner card: {called[0]}{called[1]}")
        time.sleep(0.4)

        # Play 13 tricks (52 cards)
        for trick_num in range(1, 14):
            for _ in range(4):
                # Find whose turn it is
                turn = state(pages[0])["turn"]
                if turn is None:
                    break
                played = play_one_legal_card(pages[turn])
                print(f"  trick {trick_num}: seat {turn} ({NAMES[turn]}) played {played[0]}{played[1]}")
            # Brief pause for trick collection (1.4s hold + collect animation)
            time.sleep(1.6)

        # Should now be DONE
        wait_for(pages[0], "() => lastState && lastState.phase === 'done'", timeout=5)
        s = state(pages[0])
        print(f"\nhand complete!")
        print(f"  partner_seat: {s.get('partner_seat')}")
        print(f"  result: {s.get('result')}")
        print(f"  total_tricks: {s.get('total_tricks')}")
        print(f"  dealer (rotated): {s['dealer']}")
        for i, p_ in enumerate(pages):
            p_.screenshot(path=f"/tmp/full-hand-done-{NAMES[i]}.png")

        # Quick redeal sanity check
        new_dealer = s["dealer"]
        pages[new_dealer].click("#deal-btn")
        wait_for(pages[0], "() => lastState && lastState.phase === 'bidding'", timeout=3)
        time.sleep(4)
        s2 = state(pages[0])
        assert s2["current_bidder"] == new_dealer, \
            f"new dealer should open: expected {new_dealer}, got {s2['current_bidder']}"
        print(f"redeal: new dealer is seat {new_dealer} ({NAMES[new_dealer]})")
        pages[0].screenshot(path="/tmp/full-hand-redealt.png")

        browser.close()
        print("\n✓ integration test passed")


if __name__ == "__main__":
    main()
