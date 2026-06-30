"""FastAPI sub-app for Math 24 (mounted at /math24).

Single-round racing: everyone in a room sees the same four numbers; first to
submit a valid expression making 24 wins the round. 'next' deals a fresh puzzle.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import string
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src import auth, store
from src.math24 import game

log = logging.getLogger("math24")
app = FastAPI(title="math 24 — games.djiang.xyz", docs_url=None, redoc_url=None)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@dataclass
class Player:
    name: str
    ws: object
    guest_id: str = ""
    score: int = 0


@dataclass
class Room:
    code: str
    players: list[Player] = field(default_factory=list)
    numbers: list[int] = field(default_factory=game.deal)
    round_over: bool = False
    winner: str | None = None
    winning_expr: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


ROOMS: dict[str, Room] = {}


def _make_room() -> Room:
    while True:
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in ROOMS:
            room = Room(code=code)
            ROOMS[code] = room
            return room


async def _send(p: Player, payload: dict) -> None:
    try:
        await p.ws.send_text(json.dumps(payload))
    except Exception:
        pass


async def _broadcast(room: Room, payload: dict) -> None:
    for p in list(room.players):
        await _send(p, payload)


def _state(room: Room) -> dict:
    return {
        "type": "state",
        "code": room.code,
        "numbers": room.numbers,
        "round_over": room.round_over,
        "winner": room.winner,
        "expr": room.winning_expr,
        "players": [{"name": p.name, "score": p.score} for p in room.players],
    }


async def _broadcast_state(room: Room) -> None:
    await _broadcast(room, _state(room))


def _next_puzzle(room: Room) -> None:
    room.numbers = game.deal()
    room.round_over = False
    room.winner = None
    room.winning_expr = None


def _record(room: Room, winner: Player) -> None:
    """Record the round for the shared leaderboard (multiplayer only)."""
    humans = [p for p in room.players if p.guest_id]
    if len(humans) < 2:
        return
    for p in humans:
        others = [q.name for q in humans if q is not p]
        store.record_game("math24", p.guest_id, p.name, p is winner, "pvp",
                          others[0] if others else None)


@app.websocket("/ws")
async def ws(sock: WebSocket) -> None:
    await sock.accept()
    try:
        hello = json.loads(await sock.receive_text())
        name = (hello.get("name") or "anon").strip()[:24] or "anon"
        code = (hello.get("code") or "").strip().upper()
    except Exception:
        await sock.close(code=4400, reason="bad hello")
        return

    room = ROOMS[code] if code and code in ROOMS else _make_room()
    ident = auth.identity(sock.cookies)
    p = Player(name=name, ws=sock, guest_id=ident["guest_id"])
    if ident["discord_id"]:
        store.link_guest(p.guest_id, ident["discord_id"])
    room.players.append(p)
    await _send(p, {"type": "joined", "code": room.code})
    await _broadcast_state(room)

    try:
        while True:
            msg = json.loads(await sock.receive_text())
            action = msg.get("type")
            if action == "submit":
                expr = str(msg.get("expr", ""))
                async with room.lock:
                    if room.round_over:
                        continue
                    ok, reason = game.check(expr, room.numbers)
                    if not ok:
                        await _send(p, {"type": "invalid", "reason": reason})
                        continue
                    room.round_over = True
                    room.winner = p.name
                    room.winning_expr = expr
                    p.score += 1
                    _record(room, p)
                await _broadcast(room, {"type": "round_won", "name": p.name, "expr": expr})
                await _broadcast_state(room)
            elif action == "next":
                async with room.lock:
                    _next_puzzle(room)
                await _broadcast_state(room)
    except WebSocketDisconnect:
        pass
    finally:
        if p in room.players:
            room.players.remove(p)
        if not room.players:
            ROOMS.pop(room.code, None)
        else:
            await _broadcast_state(room)
