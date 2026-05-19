"""Dogfood harness — drive the live games server through specific
scenarios over WebSocket so I can shake out bugs without manual clicks.

Usage (server must already be running with CHESS_DEBUG_SEED=1):

    CHESS_DEBUG_SEED=1 ./venv/bin/python -m src.main &
    ./venv/bin/python scripts/dogfood.py               # run all scenarios
    ./venv/bin/python scripts/dogfood.py combine_pawns # one scenario
    ./venv/bin/python scripts/dogfood.py --list        # list scenarios

Each scenario:
1. Picks a fresh room code, connects 2 WS clients.
2. Posts /chess/debug/seed to force the room into a specific state.
3. Drives WS messages from each client.
4. Asserts state at checkpoints.

Add a new scenario by defining `async def scenario_<name>(rig: Rig)`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

import httpx
import websockets


BASE = "http://127.0.0.1:7781"
WS_BASE = "ws://127.0.0.1:7781/chess/ws"
DEBUG_ENDPOINT = f"{BASE}/chess/debug/seed"


class Client:
    def __init__(self, name: str, room: str):
        self.name = name
        self.room = room
        self.ws: websockets.WebSocketClientProtocol | None = None
        self.seat: str = ""
        self.state: dict[str, Any] = {}
        self.last_error: str | None = None
        self.events: list[dict] = []
        self.prompts: list[dict] = []

    async def connect(self) -> None:
        self.ws = await websockets.connect(f"{WS_BASE}?room={self.room}&name={self.name}")
        await self.drain(400)

    async def drain(self, ms: int = 200) -> None:
        deadline = asyncio.get_event_loop().time() + (ms / 1000.0)
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            except websockets.ConnectionClosed:
                return
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
            elif kind == "prompt":
                self.prompts.append(msg)

    async def send(self, payload: dict, drain_ms: int = 200) -> None:
        self.last_error = None
        await self.ws.send(json.dumps(payload))
        await self.drain(drain_ms)

    def hand_ids(self) -> list[str]:
        return [c["card_id"] for c in self.state.get("hand", [])]

    def slot_of(self, card_id: str) -> int:
        for i, c in enumerate(self.state.get("hand", [])):
            if c["card_id"] == card_id:
                return i
        return -1

    def piece_at(self, sq: str) -> dict | None:
        for p in self.state.get("board", []):
            if p["sq"] == sq:
                return p
        return None

    def board_summary(self) -> str:
        return ", ".join(
            f"{p['sq']}={p['color'][0]}{p['kind'][0]}" for p in self.state.get("board", [])
        )

    async def close(self) -> None:
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass


class Rig:
    def __init__(self, room: str):
        self.room = room
        self.alice = Client("alice", room)   # white
        self.bob = Client("bob", room)       # black

    async def __aenter__(self) -> "Rig":
        await self.alice.connect()
        await self.bob.connect()
        await self.alice.drain(400)
        await self.bob.drain(400)
        assert self.alice.seat == "white", f"alice seat={self.alice.seat}"
        assert self.bob.seat == "black", f"bob seat={self.bob.seat}"
        return self

    async def __aexit__(self, *exc) -> None:
        await self.alice.close()
        await self.bob.close()

    async def seed(self, payload: dict) -> None:
        payload = dict(payload)
        payload["room"] = self.room
        async with httpx.AsyncClient() as c:
            r = await c.post(DEBUG_ENDPOINT, json=payload, timeout=4.0)
            r.raise_for_status()
        await self.alice.drain(250)
        await self.bob.drain(250)

    async def mulligan_both(self) -> None:
        await self.alice.send({"type": "mulligan", "redraw_slots": []})
        await self.bob.send({"type": "mulligan", "redraw_slots": []})
        await self.alice.drain(300)
        await self.bob.drain(300)


def ok(msg: str) -> None:
    print(f"  ok · {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL · {msg}", file=sys.stderr)
    sys.exit(1)


def expect(cond: bool, msg: str) -> None:
    if cond:
        ok(msg)
    else:
        fail(msg)


# ---- Scenarios --------------------------------------------------------------


async def scenario_combine_pawns(rig: Rig) -> None:
    """User reported: pick 3 pawns + modal then nothing happens."""
    await rig.mulligan_both()
    # Seed: 3 white pawns + combine_pawns in hand on white.
    await rig.seed({
        "board": [
            {"sq": "e1", "color": "white", "kind": "king"},
            {"sq": "e8", "color": "black", "kind": "king"},
            {"sq": "a2", "color": "white", "kind": "pawn"},
            {"sq": "b2", "color": "white", "kind": "pawn"},
            {"sq": "c2", "color": "white", "kind": "pawn"},
        ],
        "white": {"hand": ["spell_combine_pawns", "piece_pawn", "piece_pawn"],
                  "gold": 10, "gold_cap": 10},
        "phase": "PLAYING",
        "active_seat": "white",
    })
    slot = rig.alice.slot_of("spell_combine_pawns")
    expect(slot >= 0, f"combine_pawns at slot {slot}")
    # Play it: 3 pawn squares + 1 placement, modal=Knight
    await rig.alice.send({
        "type": "play_card", "slot": slot,
        "targets": [{"square": "a2"}, {"square": "b2"},
                    {"square": "c2"}, {"square": "d1"}],
        "modal": "Knight",
    })
    expect(rig.alice.last_error is None,
           f"server accepted combine_pawns ({rig.alice.last_error or 'no error'})")
    expect(rig.alice.piece_at("d1") is not None and rig.alice.piece_at("d1")["kind"] == "knight",
           "knight placed at d1")
    expect(rig.alice.piece_at("a2") is None, "pawn at a2 removed")


async def scenario_must_move(rig: Rig) -> None:
    """A player can't end turn without moving."""
    await rig.mulligan_both()
    await rig.seed({
        "white": {"hand": ["piece_pawn"], "gold": 10, "gold_cap": 10},
        "phase": "PLAYING",
        "active_seat": "white",
    })
    await rig.alice.send({"type": "end_turn"})
    expect(rig.alice.last_error and "make a move" in rig.alice.last_error,
           f"end_turn rejected ({rig.alice.last_error!r})")
    # Now actually move and end.
    await rig.alice.send({"type": "move", "from": "e1", "to": "e2"})
    expect(rig.alice.last_error is None, "king move accepted")
    await rig.alice.send({"type": "end_turn"})
    expect(rig.alice.last_error is None, "end_turn accepted after move")
    expect(rig.alice.state["active_seat"] == "black", "turn passed to black")


async def scenario_summoning_sickness(rig: Rig) -> None:
    """A card-placed piece can't move the same turn."""
    await rig.mulligan_both()
    await rig.seed({
        "white": {"hand": ["piece_pawn"], "gold": 10, "gold_cap": 10},
        "phase": "PLAYING",
        "active_seat": "white",
    })
    slot = rig.alice.slot_of("piece_pawn")
    await rig.alice.send({
        "type": "play_card", "slot": slot,
        "targets": [{"square": "a2"}],
    })
    expect(rig.alice.last_error is None, "pawn placed")
    # Try to move the freshly placed pawn — should be rejected.
    await rig.alice.send({"type": "move", "from": "a2", "to": "a3"})
    expect(rig.alice.last_error and "just placed" in rig.alice.last_error,
           f"sickness rejection ({rig.alice.last_error!r})")
    # Move the king instead — should work.
    await rig.alice.send({"type": "move", "from": "e1", "to": "e2"})
    expect(rig.alice.last_error is None, "king move accepted")


async def scenario_play_then_move_next_turn(rig: Rig) -> None:
    """Placed pawn can move on the NEXT turn (sickness cleared at end_turn)."""
    await rig.mulligan_both()
    await rig.seed({
        "white": {"hand": ["piece_pawn"], "gold": 10, "gold_cap": 10},
        "black": {"hand": [], "gold": 10, "gold_cap": 10},
        "phase": "PLAYING",
        "active_seat": "white",
    })
    slot = rig.alice.slot_of("piece_pawn")
    await rig.alice.send({"type": "play_card", "slot": slot,
                          "targets": [{"square": "a2"}]})
    await rig.alice.send({"type": "move", "from": "e1", "to": "e2"})
    await rig.alice.send({"type": "end_turn"})
    # Black's turn — must move; pick king.
    await rig.bob.send({"type": "move", "from": "e8", "to": "e7"})
    expect(rig.bob.last_error is None, "black moves king")
    await rig.bob.send({"type": "end_turn"})
    # White's turn again — try to move the previously-placed pawn.
    await rig.alice.drain(200)
    await rig.alice.send({"type": "move", "from": "a2", "to": "a3"})
    expect(rig.alice.last_error is None,
           f"sickness cleared next turn ({rig.alice.last_error!r})")


async def scenario_rematch(rig: Rig) -> None:
    """After GAME OVER, rematch restores fresh state with same seats."""
    await rig.mulligan_both()
    await rig.alice.send({"type": "concede"})
    expect(rig.alice.state["phase"] == "DONE", "DONE after concede")
    expect(rig.alice.state["winner"] == "black", "black wins concede")
    await rig.alice.send({"type": "new_game"})
    expect(rig.alice.state["phase"] == "MULLIGAN",
           f"rematch goes back to MULLIGAN ({rig.alice.state['phase']})")
    expect(rig.alice.state["winner"] is None, "winner cleared")
    expect(len(rig.alice.state["hand"]) == 3, "white redrew 3")


SCENARIOS: dict[str, Callable[[Rig], Awaitable[None]]] = {
    "combine_pawns": scenario_combine_pawns,
    "must_move": scenario_must_move,
    "summoning_sickness": scenario_summoning_sickness,
    "play_then_move_next_turn": scenario_play_then_move_next_turn,
    "rematch": scenario_rematch,
}


async def run(name: str) -> None:
    print(f"== {name} ==")
    room = f"DG{int(time.time()*1000) % 100000:05d}"
    async with Rig(room) as rig:
        await SCENARIOS[name](rig)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenarios", nargs="*", help="scenario names to run; all if empty")
    parser.add_argument("--list", action="store_true", help="list available scenarios")
    args = parser.parse_args()
    if args.list:
        for k in SCENARIOS:
            print(k)
        return 0
    # Health-check the server + debug endpoint.
    async with httpx.AsyncClient() as c:
        try:
            r = await c.get(f"{BASE}/chess/healthz", timeout=2.0)
            r.raise_for_status()
        except Exception as e:
            print(f"server not reachable at {BASE}: {e}", file=sys.stderr)
            return 2
        r2 = await c.post(DEBUG_ENDPOINT, json={"room": "PROBE"}, timeout=2.0)
        if r2.status_code == 403:
            print("debug endpoint disabled — start server with CHESS_DEBUG_SEED=1",
                  file=sys.stderr)
            return 3
    names = args.scenarios or list(SCENARIOS.keys())
    for n in names:
        if n not in SCENARIOS:
            print(f"unknown scenario: {n}", file=sys.stderr)
            return 4
        await run(n)
    print(f"\nALL OK ({len(names)} scenario{'s' if len(names) != 1 else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
