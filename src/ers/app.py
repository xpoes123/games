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

from src.ers.rooms import ROOMS, SLAP_WINDOW_S, Player, Room, make_room, slap_rule

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
        await _broadcast(room, {"type": "game_over", "name": end.name})
    await _broadcast_state(room)


async def _handle_flip(room: Room, p: Player) -> None:
    async with room.lock:
        # Solo practice: flipping past a live (unslapped) pile is a miss.
        missed = slap_rule(room.pile) if room.solo and not room.window_open else None
        card, err = room.flip(p)
    if err:
        await _send(p, {"type": "error", "message": err})
        return
    if missed:
        await _send(p, {"type": "missed", "rule": missed})
    await _broadcast(room, {"type": "flip", "seat": room.seat_of(p), "card": card})
    await _broadcast_state(room)


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
            elif action == "deal":
                async with room.lock:
                    # Solo can (re)deal anytime with 1 player; multiplayer needs 2.
                    need = 1 if room.solo else 2
                    if len(room.players) >= need and (room.solo or not room.started):
                        room.deal()
                await _broadcast(room, {"type": "dealt"})
                await _broadcast_state(room)
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
            ROOMS.pop(room.code, None)
        else:
            await _broadcast_state(room)
