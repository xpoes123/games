"""Capture key Hearthstone Chess UI states for visual verification.

Drives two browser contexts (white + black) through a short scripted flow
and snapshots the white player's screen at each stage.

Outputs:
  /tmp/chess-phase4-1-lobby.png       — entry page
  /tmp/chess-phase4-2-mulligan.png    — mulligan screen, two cards marked
  /tmp/chess-phase4-3-midgame.png     — a few pieces deployed, mid-turn
  /tmp/chess-phase4-4-targeting.png   — targeting a card (Combine 3 Pawns)
  /tmp/chess-phase4-5-gameover.png    — GAME OVER banner after concede

Server must be running at http://127.0.0.1:7781 already.
"""
from __future__ import annotations

import sys
import time
from playwright.sync_api import sync_playwright


import random, string
BASE = "http://127.0.0.1:7781/chess/"
ROOM = "P" + "".join(random.choices(string.ascii_uppercase, k=3))


def join(page, name: str, room: str = ROOM) -> None:
    page.goto(BASE)
    page.fill("#player-name", name)
    page.fill("#room-code", room)
    page.click("#join-btn")
    page.wait_for_selector("#game:not([hidden])")


def wait_phase(page, phase: str, timeout: int = 10000) -> None:
    page.wait_for_function(
        f"window['lastPhase'] === '{phase}'",
        timeout=timeout,
    )


def inject_phase_tracker(page) -> None:
    # Hook into the global last state via a small monkey-patch using runtime.
    page.evaluate(
        """
        (() => {
          if (window.__phaseHook) return;
          window.__phaseHook = true;
          window.lastPhase = null;
          const oh = WebSocket.prototype.send;
          const orig = window.handleMessage;
          // We can't reach handleMessage from outside; instead, observe via
          // periodic poll of lastState which is module-scoped — fallback:
          // expose via state element on every render.
          // Hack: poll the status-line to infer phase loosely. Better: stash
          // on a global from each state msg.
        })();
        """
    )


def play_piece_card(page, name_prefix: str, square: str) -> None:
    """Click the first hand card whose `.name` starts with name_prefix, then click target."""
    cards = page.query_selector_all(".card")
    target = None
    for c in cards:
        nm = c.query_selector(".name")
        if not nm:
            continue
        if nm.inner_text().lower().startswith(name_prefix.lower()):
            target = c
            break
    if target is None:
        raise RuntimeError(f"no card named {name_prefix} in hand")
    target.click()
    time.sleep(0.15)
    sq = page.query_selector(f'.sq[data-sq="{square}"]')
    if sq is None:
        raise RuntimeError(f"no square {square}")
    sq.click()
    time.sleep(0.25)


def maybe_play_simple(page) -> str | None:
    """Play any playable piece card from the hand. Returns card name played."""
    names = page.evaluate(
        """() => [...document.querySelectorAll('.card.playable')].map(c => ({
            name: c.querySelector('.name')?.innerText || '',
            type: c.querySelector('.tag')?.innerText || ''
        }))"""
    )
    pick = None
    for i, info in enumerate(names):
        if info["name"] in ("Pawn", "Knight", "Bishop", "Rook"):
            pick = (i, info["name"]); break
    if pick is None:
        return None
    i, nm = pick
    page.evaluate(f"document.querySelectorAll('.card.playable')[{i}].click()")
    time.sleep(0.2)
    # find first empty back-2 square
    sq = page.evaluate(
        """() => {
            const seat = window.mySeat || 'white';
            const ranks = seat === 'white' ? [1,2] : [7,8];
            for (const r of ranks) for (const f of 'abcdefgh') {
                const s = f + r;
                const cell = document.querySelector('.sq[data-sq="'+s+'"]');
                if (cell && !cell.querySelector('.piece')) return s;
            }
            return null;
        }"""
    )
    if sq:
        page.evaluate(f"document.querySelector('.sq[data-sq=\"{sq}\"]').click()")
        time.sleep(0.3)
    return nm


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # White & black side-by-side
        ctx_w = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx_b = browser.new_context(viewport={"width": 1280, "height": 900})
        page_w = ctx_w.new_page()
        page_b = ctx_b.new_page()

        # 1) Lobby (just the entry page)
        page_w.goto(BASE)
        page_w.fill("#player-name", "alice")
        page_w.fill("#room-code", ROOM)
        time.sleep(0.3)
        page_w.screenshot(full_page=True, path="/tmp/chess-phase4-1-lobby.png")
        page_w.click("#join-btn")
        page_w.wait_for_selector("#game:not([hidden])")

        # join black (this triggers mulligan)
        join(page_b, "bob")
        time.sleep(0.6)

        # 2) Mulligan — mark two cards
        for idx in (0, 1):
            page_w.evaluate(f"document.querySelectorAll('#hand .card')[{idx}].click()")
            time.sleep(0.1)
        time.sleep(0.3)
        page_w.screenshot(full_page=True, path="/tmp/chess-phase4-2-mulligan.png")

        # Submit both mulligans
        page_w.click("#mulligan-btn")
        page_b.click("#mulligan-btn")
        time.sleep(0.7)

        # 3) Mid-game: play a few pawns, end turn, opponent plays, etc.
        # White turn — play one card.
        maybe_play_simple(page_w)
        time.sleep(0.4)
        page_w.click("#end-turn-btn")
        time.sleep(0.4)

        # Black turn — play one card (use the black page now).
        maybe_play_simple(page_b)
        time.sleep(0.3)
        page_b.click("#end-turn-btn")
        time.sleep(0.4)

        # White turn 2 — play another
        maybe_play_simple(page_w)
        time.sleep(0.5)

        page_w.screenshot(full_page=True, path="/tmp/chess-phase4-3-midgame.png")
        page_w.click("#end-turn-btn")
        time.sleep(0.3)
        page_b.click("#end-turn-btn")
        time.sleep(0.4)

        # 4) Targeting flow: try to enter a multi-step casting.
        # If we have piece_any or any spell with multi targets, click it. We
        # generate the easiest reliable shot: click any spell card if present.
        # Fallback: click a piece card and snapshot mid-placement.
        time.sleep(0.2)
        # Click a card to enter targeting. Prefer Combine 3 Pawns if visible,
        # else just click any piece card.
        target_card = None
        for c in page_w.query_selector_all(".card.playable"):
            nm = c.query_selector(".name").inner_text()
            if nm.lower().startswith("combine"):
                target_card = c
                break
        # Use indexed evaluate to avoid stale handles.
        idx = page_w.evaluate(
            """() => {
                const cards = [...document.querySelectorAll('.card.playable')];
                let ci = cards.findIndex(c => (c.querySelector('.name')?.innerText||'').toLowerCase().startsWith('combine'));
                if (ci >= 0) return ci;
                ci = cards.findIndex(c => ['Knight','Bishop','Pawn','Rook','Queen','Any Piece'].includes(c.querySelector('.name')?.innerText));
                return ci;
            }"""
        )
        if idx is not None and idx >= 0:
            page_w.evaluate(f"document.querySelectorAll('.card.playable')[{idx}].click()")
            time.sleep(0.35)
        page_w.screenshot(full_page=True, path="/tmp/chess-phase4-4-targeting.png")

        # cancel targeting via Escape
        page_w.keyboard.press("Escape")
        time.sleep(0.2)

        # 5) Game over via concede
        page_w.click("#concede-btn")
        time.sleep(0.15)
        page_w.click("#concede-yes")
        time.sleep(0.7)
        page_w.screenshot(full_page=True, path="/tmp/chess-phase4-5-gameover.png")

        ctx_w.close()
        ctx_b.close()
        browser.close()


if __name__ == "__main__":
    main()
    print("ok")
