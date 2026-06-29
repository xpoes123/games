"""FastAPI sub-app for Math ERS. Mounted at /ers by src/main.py.

Latency is handled client-side: each client times its own reaction (card paint
→ slap) and sends only that delta, so network lag cancels out. The server just
ranks reactions inside a short window — see record_slap() in rooms.py and
_open_slap_window() below.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.ers.rooms import (
    ALL_RULES, MAX_SPAN, ROOMS, SHOT_CLOCK_S, SLAP_WINDOW_S, Player, Room,
    make_room, min_span, slap_rule,
)

log = logging.getLogger("ers")

app = FastAPI(title="ers — games.djiang.xyz", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


async def _send(p: Player, payload: dict) -> None:
    try:
        await p.socket.send_text(json.dumps(payload))
    except Exception:
        p.connected = False


async def _broadcast(room: Room, payload: dict) -> None:
    for p in list(room.players):
        await _send(p, payload)


async def _broadcast_state(room: Room) -> None:
    await _broadcast(room, {"type": "state", "room": room.public_state()})


async def _open_slap_window(room: Room) -> None:
    await asyncio.sleep(SLAP_WINDOW_S)
    async with room.lock:
        rule = room.pending_rule
        winner = room.resolve_slaps()
        if winner is None:
            return
        seat = room.seat_of(winner)
        end = room.winner()
    await _broadcast(room, {
        "type": "slap_won", "seat": seat, "name": winner.name, "rule": rule,
    })
    if end is not None:
        async with room.lock:
            room.started = False  # game over → stop the shot clock and further flips
        await _broadcast(room, {"type": "game_over", "name": end.name})
    await _broadcast_state(room)
    await _arm_clock(room)


async def _arm_clock(room: Room) -> None:
    """(Re)start the shot clock for whoever's turn it is. Server-authoritative;
    the broadcast is only a hint for the client's countdown bar."""
    if room.clock_task:
        room.clock_task.cancel()
        room.clock_task = None
    if not room.started or room.solo:
        return
    if room.turn >= len(room.players) or not room.players[room.turn].stack:
        return
    room.clock_task = asyncio.create_task(_clock(room))
    await _broadcast(room, {"type": "clock", "seat": room.turn, "seconds": SHOT_CLOCK_S})


async def _clock(room: Room) -> None:
    try:
        await asyncio.sleep(SHOT_CLOCK_S)
    except asyncio.CancelledError:
        return
    room.clock_task = None  # detach self so the re-arm below doesn't cancel us
    async with room.lock:
        cur = None
        if room.started and not room.solo and not room.window_open:
            if room.turn < len(room.players) and room.players[room.turn].stack:
                cur = room.players[room.turn]
    if cur and (await _flip_for(room, cur))[0]:
        return  # auto-flip succeeded; _flip_for re-armed the clock
    await _arm_clock(room)  # couldn't flip (slap window / race) — try again


async def _flip_for(room: Room, p: Player) -> tuple[bool, str | None]:
    async with room.lock:
        # Solo practice: flipping past a live (unslapped) pile is a miss.
        missed = slap_rule(room.pile, room.rule_spans) if room.solo and not room.window_open else None
        seat = room.seat_of(p)
        card, err = room.flip(p)
    if err:
        return False, err
    if missed:
        await _send(p, {"type": "missed", "rule": missed})
    await _broadcast(room, {"type": "flip", "seat": seat, "card": card})
    await _broadcast_state(room)
    await _arm_clock(room)  # turn advanced → restart clock for next player
    return True, None


async def _handle_flip(room: Room, p: Player) -> None:
    ok, err = await _flip_for(room, p)
    if not ok and err:
        await _send(p, {"type": "error", "message": err})


async def _handle_slap(room: Room, p: Player, msg: dict) -> None:
    arrival = time.monotonic()
    reaction = msg.get("reaction")
    reaction = float(reaction) if isinstance(reaction, (int, float)) else None
    async with room.lock:
        result = room.record_slap(p, reaction, arrival)
    if result == "wrong":
        await _send(p, {"type": "burned"})
        await _broadcast_state(room)
    elif result == "open":
        asyncio.create_task(_open_slap_window(room))


@app.websocket("/ws")
async def ws(sock: WebSocket) -> None:
    await sock.accept()
    try:
        hello = json.loads(await sock.receive_text())
        name = (hello.get("name") or "anon").strip()[:24] or "anon"
        code = (hello.get("code") or "").strip().upper()
        solo = bool(hello.get("solo"))
    except Exception:
        await sock.close(code=4400, reason="bad hello")
        return

    if solo:
        room = make_room()
        room.solo = True
    elif code and code in ROOMS:
        room = ROOMS[code]
    else:
        room = make_room()
    if room.started and not room.solo:
        await sock.close(code=4403, reason="game in progress")
        return

    p = room.add(name, sock)
    await _send(p, {"type": "joined", "code": room.code, "seat": room.seat_of(p), "solo": room.solo})
    await _broadcast_state(room)

    try:
        while True:
            msg = json.loads(await sock.receive_text())
            action = msg.get("type")
            if action == "set_mode":
                if msg.get("mode") in ("reflex", "ping") and not room.started:
                    room.mode = msg["mode"]
                    await _broadcast_state(room)
            elif action == "set_spans":
                picked = msg.get("spans")
                if isinstance(picked, dict) and (room.solo or not room.started):
                    spans = {}
                    for r in ALL_RULES:
                        n = picked.get(r)
                        if isinstance(n, int) and n >= min_span(r):
                            spans[r] = min(n, MAX_SPAN)  # >= min keeps it on; clamp to max
                    room.rule_spans = spans
                    await _broadcast_state(room)
            elif action == "deal":
                async with room.lock:
                    # Solo can (re)deal anytime with 1 player; multiplayer needs 2.
                    need = 1 if room.solo else 2
                    if len(room.players) >= need and (room.solo or not room.started):
                        room.deal()
                await _broadcast(room, {"type": "dealt"})
                await _broadcast_state(room)
                await _arm_clock(room)
            elif action == "flip":
                await _handle_flip(room, p)
            elif action == "slap":
                await _handle_slap(room, p, msg)
    except WebSocketDisconnect:
        pass
    finally:
        p.connected = False
        if p in room.players:
            room.players.remove(p)
        if not room.players:
            if room.clock_task:
                room.clock_task.cancel()
            ROOMS.pop(room.code, None)
        else:
            await _broadcast_state(room)
            await _arm_clock(room)  # seats shifted; restart clock for current turn
