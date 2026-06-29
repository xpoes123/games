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
    ALL_RULES, MAX_SPAN, ROOMS, SLAP_WINDOW_S, Player, Room,
    is_slappable, make_room, min_span, slap_rule,
)

CPU_THINK = 0.6  # delay before the CPU flips on its own turn (so play is watchable)

log = logging.getLogger("ers")

app = FastAPI(title="ers — games.djiang.xyz", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


async def _send(p: Player, payload: dict) -> None:
    if p.is_cpu:  # no socket
        return
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
    _cpu_after_change(room)


async def _arm_clock(room: Room) -> None:
    """(Re)start the shot clock for whoever's turn it is. Server-authoritative;
    the broadcast is only a hint for the client's countdown bar."""
    if room.clock_task:
        room.clock_task.cancel()
        room.clock_task = None
    if not room.started or room.shot_clock <= 0:
        return
    if room.turn >= len(room.players) or not room.players[room.turn].stack:
        return
    room.clock_task = asyncio.create_task(_clock(room))
    await _broadcast(room, {"type": "clock", "seat": room.turn, "seconds": room.shot_clock})


async def _clock(room: Room) -> None:
    try:
        await asyncio.sleep(room.shot_clock)
    except asyncio.CancelledError:
        return
    room.clock_task = None  # detach self so the re-arm below doesn't cancel us
    async with room.lock:
        cur = None
        if room.started and room.shot_clock > 0 and not room.window_open:
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
    _cpu_after_change(room)
    return True, None


async def _handle_flip(room: Room, p: Player) -> None:
    ok, err = await _flip_for(room, p)
    if not ok and err:
        await _send(p, {"type": "error", "message": err})


# --- CPU opponent (solo practice) ----------------------------------------
def _cpu_after_change(room: Room) -> None:
    """React to a state change: the CPU slaps a live pile, else flips on its
    turn. Synchronous — only (re)schedules timers, no awaits, so it can't race
    the state it just read. Called after every deal / flip / slap resolution."""
    cpu = next((p for p in room.players if p.is_cpu), None)
    for attr in ("cpu_flip_task", "cpu_slap_task"):
        t = getattr(room, attr)
        if t:
            t.cancel()
            setattr(room, attr, None)
    if not cpu or not room.started:
        return
    if is_slappable(room.pile, room.rule_spans):
        room.cpu_slap_task = asyncio.create_task(_cpu_slap(room, cpu))
    elif room.turn == room.seat_of(cpu) and cpu.stack:
        room.cpu_flip_task = asyncio.create_task(_cpu_flip(room, cpu))


async def _cpu_flip(room: Room, cpu: Player) -> None:
    try:
        await asyncio.sleep(CPU_THINK)
    except asyncio.CancelledError:
        return
    room.cpu_flip_task = None
    await _flip_for(room, cpu)


async def _cpu_slap(room: Room, cpu: Player) -> None:
    try:
        await asyncio.sleep(room.cpu_reaction)  # difficulty = how fast it reacts
    except asyncio.CancelledError:
        return
    room.cpu_slap_task = None
    await _process_slap(room, cpu, room.cpu_reaction, time.monotonic())


async def _process_slap(room: Room, p: Player, reaction: float | None, arrival: float) -> None:
    async with room.lock:
        result = room.record_slap(p, reaction, arrival)
    if result == "wrong":
        await _send(p, {"type": "burned"})  # no-op for CPU
        await _broadcast_state(room)
    elif result == "open":
        asyncio.create_task(_open_slap_window(room))


async def _handle_slap(room: Room, p: Player, msg: dict) -> None:
    reaction = msg.get("reaction")
    reaction = float(reaction) if isinstance(reaction, (int, float)) else None
    await _process_slap(room, p, reaction, time.monotonic())


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
        room.shot_clock = 0.0  # off by default in solo; player can enable for drilling
    elif code and code in ROOMS:
        room = ROOMS[code]
    else:
        room = make_room()
    if room.started and not room.solo:
        await sock.close(code=4403, reason="game in progress")
        return

    p = room.add(name, sock)
    if room.solo and not any(pl.is_cpu for pl in room.players):
        cpu = room.add("CPU", None)  # seat 1: the opponent
        cpu.is_cpu = True
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
            elif action == "set_clock":
                sec = msg.get("seconds")
                if isinstance(sec, (int, float)) and (room.solo or not room.started):
                    room.shot_clock = 0.0 if sec <= 0 else max(0.5, min(float(sec), 10.0))
                    await _broadcast_state(room)
                    await _arm_clock(room)  # apply now (e.g. solo mid-game toggle)
            elif action == "set_cpu":
                r = msg.get("reaction")
                if isinstance(r, (int, float)) and room.solo:
                    had = any(pl.is_cpu for pl in room.players)
                    want = r > 0  # 0 = zen (no opponent)
                    if want:
                        room.cpu_reaction = max(0.1, min(float(r), 5.0))
                    if want != had:  # CPU added/removed → rebuild and re-deal
                        if want:
                            room.add("CPU", None).is_cpu = True
                        else:
                            for t in (room.cpu_flip_task, room.cpu_slap_task):
                                if t:
                                    t.cancel()
                            room.cpu_flip_task = room.cpu_slap_task = None
                            room.players = [pl for pl in room.players if not pl.is_cpu]
                        async with room.lock:
                            room.deal()
                        await _broadcast_state(room)
                        await _arm_clock(room)
                        _cpu_after_change(room)
                    else:
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
                _cpu_after_change(room)
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
        # Drop the room once no humans remain (a lone CPU doesn't keep it alive).
        if not any(pl for pl in room.players if not pl.is_cpu):
            for t in (room.clock_task, room.cpu_flip_task, room.cpu_slap_task):
                if t:
                    t.cancel()
            ROOMS.pop(room.code, None)
        else:
            await _broadcast_state(room)
            await _arm_clock(room)  # seats shifted; restart clock for current turn
            _cpu_after_change(room)
