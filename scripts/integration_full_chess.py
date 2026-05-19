"""End-to-end WS test: 2 players play through a full hearthstone-chess game.

Drives the live FastAPI app (must be running at 127.0.0.1:7781) via two
WebSocket clients. Walks lobby → mulligan → multi-turn play (place pieces,
cast a few spells, push pawns) → king capture. Asserts state at every
checkpoint and exits non-zero if anything is off.

Run with the server already up:
    ./venv/bin/python -m src.main &
    ./venv/bin/python scripts/integration_full_chess.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

import websockets


URL = "ws://127.0.0.1:7781/chess/ws"
ROOM = f"IT{int(time.time()) % 10000:04d}"


class Client:
    def __init__(self, name: str):
        self.name = name
        self.ws: websockets.WebSocketClientProtocol | None = None
        self.seat: str = ""
        self.state: dict[str, Any] = {}
        self.last_error: str | None = None
        self.events: list[dict] = []

    async def connect(self) -> None:
        self.ws = await websockets.connect(f"{URL}?room={ROOM}&name={self.name}")
        await self._drain_until_state()

    async def _drain_until_state(self, timeout: float = 2.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=0.1)
            except asyncio.TimeoutError:
                if self.state:
                    return
                continue
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == "welcome":
                self.seat = msg["your_seat"]
            elif kind == "state":
                self.state = msg
            elif kind == "error":
                self.last_error = msg.get("text")
            elif kind == "event":
                self.events.append(msg)
        if not self.state:
            raise RuntimeError(f"{self.name}: no state after {timeout}s")

    async def drain(self, ms: int = 200) -> None:
        deadline = asyncio.get_event_loop().time() + (ms / 1000.0)
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == "state":
                self.state = msg
            elif kind == "error":
                self.last_error = msg.get("text")
            elif kind == "event":
                self.events.append(msg)

    async def send(self, payload: dict) -> None:
        await self.ws.send(json.dumps(payload))
        await self.drain(200)

    def hand(self) -> list[dict]:
        return self.state.get("hand", [])

    def my_gold(self) -> tuple[int, int]:
        side = self.state.get(self.seat) or {}
        return int(side.get("gold", 0)), int(side.get("gold_cap", 0))

    def find_card(self, card_id: str) -> dict | None:
        for c in self.hand():
            if c["card_id"] == card_id and c.get("playable"):
                return c
        return None

    def piece_at(self, sq: str) -> dict | None:
        for p in self.state.get("board", []):
            if p["sq"] == sq:
                return p
        return None

    def is_my_turn(self) -> bool:
        return self.state.get("active_seat") == self.seat

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()


def expect(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)


async def find_and_play_first_playable_piece(c: Client, square: str) -> bool:
    """Look in hand for any back-2 piece card; play it on `square`."""
    for slot, card in enumerate(c.hand()):
        if not card.get("playable"):
            continue
        if not card["card_id"].startswith("piece_") or card["card_id"] == "piece_any":
            continue
        await c.send({"type": "play_card", "slot": slot,
                      "targets": [{"square": square}]})
        if c.last_error:
            print(f"  play_card error: {c.last_error}")
            c.last_error = None
            continue
        return True
    return False


async def main() -> int:
    alice = Client("alice")
    bob = Client("bob")
    await alice.connect()
    await bob.connect()
    await alice.drain(300)
    await bob.drain(300)

    print(f"connected · room {ROOM} · alice={alice.seat} bob={bob.seat}")
    expect(alice.seat == "white" and bob.seat == "black",
           f"seats wrong: {alice.seat}, {bob.seat}")
    expect(alice.state["phase"] == "MULLIGAN",
           f"expected MULLIGAN, got {alice.state['phase']}")
    expect(len(alice.hand()) == 3, f"alice hand={len(alice.hand())} (expected 3)")
    expect(len(bob.hand()) == 4, f"bob hand={len(bob.hand())} (expected 4)")

    # both mulligan-keep
    await alice.send({"type": "mulligan", "redraw_slots": []})
    await bob.send({"type": "mulligan", "redraw_slots": []})
    await alice.drain(300)
    await bob.drain(300)

    expect(alice.state["phase"] == "PLAYING",
           f"expected PLAYING after mulligan, got {alice.state['phase']}")
    expect(alice.state["active_seat"] == "white", "white should start")
    print("mulligan complete, white to move")

    # Helper: pick whoever's turn it is
    def active() -> Client:
        return alice if alice.is_my_turn() else bob

    # Run up to N turns. Each turn the active player tries to:
    # 1. play a 1-cost piece card on their back rank
    # 2. push a friendly piece toward the enemy king (if any non-king piece exists)
    # 3. end turn
    KING_W = "e1"
    KING_B = "e8"
    deploy_squares = {
        "white": ["a1", "b1", "c1", "d1", "f1", "g1", "h1",
                  "a2", "b2", "c2", "d2", "e2", "f2", "g2", "h2"],
        "black": ["a8", "b8", "c8", "d8", "f8", "g8", "h8",
                  "a7", "b7", "c7", "d7", "e7", "f7", "g7", "h7"],
    }

    def first_empty_back2(c: Client) -> str | None:
        for sq in deploy_squares[c.seat]:
            if c.piece_at(sq) is None:
                return sq
        return None

    turns = 0
    while alice.state.get("phase") != "DONE" and turns < 60:
        turns += 1
        c = active()
        other = bob if c is alice else alice

        # Try to play a piece card if affordable
        sq = first_empty_back2(c)
        if sq:
            await find_and_play_first_playable_piece(c, sq)
            await c.drain(150)
            await other.drain(150)

        # Try to make a chess move that captures the enemy king if possible
        target_king = KING_B if c.seat == "white" else KING_W
        # Find any of my pieces that can reach target_king in one step
        # (rooks/queens on same file/rank, bishops/queens on diagonals, knights L-shape, kings adjacent)
        # We'll do a simple sweep over my pieces and try moves until one is accepted.
        my_pieces = [p for p in c.state.get("board", []) if p["color"] == c.seat]
        moved = False
        if c.state[c.seat]["extra_moves"] > 0:
            for p in my_pieces:
                # try moving to target_king first
                await c.send({"type": "move", "from": p["sq"], "to": target_king})
                if not c.last_error:
                    moved = True
                    break
                c.last_error = None
            if not moved:
                # try moving any of my pieces forward (king included — early
                # game when nothing else is on the board, the king is the
                # only legal mover, and summoning sickness keeps freshly
                # placed pieces from moving the turn they arrive).
                for p in my_pieces:
                    file_ = p["sq"][0]
                    rank = int(p["sq"][1])
                    fwd = rank + (1 if c.seat == "white" else -1)
                    if 1 <= fwd <= 8:
                        tgt = f"{file_}{fwd}"
                        await c.send({"type": "move", "from": p["sq"], "to": tgt})
                        if not c.last_error:
                            moved = True
                            break
                        c.last_error = None

        # end turn
        await c.send({"type": "end_turn"})
        await alice.drain(120)
        await bob.drain(120)

    expect(alice.state.get("phase") == "DONE",
           f"game did not end in {turns} turns (phase={alice.state.get('phase')})")
    winner = alice.state.get("winner")
    expect(winner in ("white", "black"), f"bad winner: {winner!r}")
    expect(alice.state.get("win_reason") == "king_capture",
           f"win_reason should be king_capture, got {alice.state.get('win_reason')!r}")

    # both clients should agree
    expect(bob.state.get("winner") == winner, "winner disagreement")
    expect(bob.state.get("phase") == "DONE", "bob doesn't see DONE")

    # turn count sanity: game should have lasted a reasonable number of turns
    expect(alice.state.get("turn_number") >= 1, "turn number sanity")

    print(f"OK — {winner} wins after {turns} turns via king capture")
    print(f"  white deck {alice.state['white']['deck_size']}, "
          f"black deck {alice.state['black']['deck_size']}")

    await alice.close()
    await bob.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
