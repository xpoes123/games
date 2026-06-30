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
import random
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src import auth, store
from src.ers.rooms import (
    ALL_RULES, CPU_LEVELS, MAX_SPAN, ROOMS, SLAP_WINDOW_S, Player, Room,
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


def _record_result(room: Room, winner: Player) -> None:
    """Save the finished game for each human player (guests included)."""
    cpu = any(p.is_cpu for p in room.players)
    dur = (time.time() - room.started_at) if room.started_at else None
    for p in room.players:
        if p.is_cpu or not p.guest_id:
            continue
        if cpu:
            mode, opp = "cpu", room.cpu_level
        else:
            others = [q.name for q in room.players if q is not p]
            mode, opp = "pvp", (others[0] if others else None)
        store.record_game("ers", p.guest_id, p.name, p is winner, mode, opp, dur)


async def _open_slap_window(room: Room) -> None:
    await asyncio.sleep(SLAP_WINDOW_S)
    async with room.lock:
        rule = room.pending_rule
        winner = room.resolve_slaps()
        if winner is None:
            return
        room.last_resolve = time.monotonic()  # grace window for late racers
        seat = room.seat_of(winner)
        end = room.winner()
    await _broadcast(room, {
        "type": "slap_won", "seat": seat, "name": winner.name, "rule": rule,
    })
    if end is not None:
        async with room.lock:
            room.started = False  # game over → stop the shot clock and further flips
        _record_result(room, end)
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
        card, err, event = room.flip(p)
        end = room.winner() if err is None else None  # one hand holds everything
        draw = end is None and err is None and room.exhausted()
    if err:
        return False, err
    if missed:
        await _send(p, {"type": "missed", "rule": missed})
    await _broadcast(room, {"type": "flip", "seat": seat, "card": card})
    if event and event[0] == "battle_won":
        w = event[1]
        await _broadcast(room, {"type": "battle_won", "seat": room.seat_of(w), "name": w.name})
    if end is not None or draw:
        async with room.lock:
            room.started = False  # stop the shot clock and further flips
        if end is not None:
            _record_result(room, end)
            await _broadcast(room, {"type": "game_over", "name": end.name})
        else:
            await _broadcast(room, {"type": "game_over", "name": None, "draw": True})
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
    """React to a state change: the CPU races its human-like scan (cpu_plan)
    against when it'll flip — slaps if it computes a pattern in time, else flips
    (and misses it). Synchronous — only (re)schedules timers, no awaits. Called
    after every deal/flip/slap."""
    cpu = next((p for p in room.players if p.is_cpu), None)
    for attr in ("cpu_flip_task", "cpu_slap_task"):
        t = getattr(room, attr)
        if t:
            t.cancel()
            setattr(room, attr, None)
    if not cpu or not room.started:
        return
    delay = room.cpu_plan(room.pile)  # human-like scan time, or None if it can't see it
    my_turn = room.turn == room.seat_of(cpu) and bool(cpu.stack)
    if my_turn:
        # Race its scan against when it'll play its card: if it doesn't finish
        # computing before it flips, it just flips (and misses the slap).
        flip_at = room.cpu_flip_delay()
        if delay is not None and delay < flip_at:
            room.cpu_slap_task = asyncio.create_task(_cpu_slap(room, cpu, delay))
        else:
            room.cpu_flip_task = asyncio.create_task(_cpu_flip(room, cpu, flip_at))
    elif delay is not None:
        # Opponent's turn: slap if/when it spots the pattern (before the pile moves on).
        room.cpu_slap_task = asyncio.create_task(_cpu_slap(room, cpu, delay))


async def _cpu_flip(room: Room, cpu: Player, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    room.cpu_flip_task = None
    await _flip_for(room, cpu)


async def _cpu_slap(room: Room, cpu: Player, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    room.cpu_slap_task = None
    # Its scan time is its "reaction" — competes with the human's in reflex mode.
    await _process_slap(room, cpu, delay, time.monotonic())


async def _process_slap(room: Room, p: Player, reaction: float | None, arrival: float) -> None:
    async with room.lock:
        result = room.record_slap(p, reaction, arrival)
        burned = p.wrong_streak
    if result == "wrong":
        await _send(p, {"type": "burned", "count": burned})  # no-op for CPU
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
        room.solo = True  # shot clock defaults on (3s); switch to zen + off to study
    elif code and code in ROOMS:
        room = ROOMS[code]
    else:
        room = make_room()
    if room.started and not room.solo:
        await sock.close(code=4403, reason="game in progress")
        return

    p = room.add(name, sock)
    # Identity from cookies: guest id always, discord id if logged in. Logging in
    # links this guest's saved games (past + future) to the account.
    ident = auth.identity(sock.cookies)
    p.guest_id = ident["guest_id"]
    if ident["discord_id"]:
        p.discord_id = ident["discord_id"]
        store.link_guest(p.guest_id, p.discord_id)
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
            elif action == "set_battle":
                on = msg.get("on")
                if isinstance(on, bool) and (room.solo or not room.started):
                    room.battle_enabled = on
                    await _broadcast_state(room)
            elif action == "set_cpu":
                level = msg.get("level")
                if isinstance(level, str) and room.solo:
                    had = any(pl.is_cpu for pl in room.players)
                    want = level in CPU_LEVELS  # "zen"/unknown → no opponent
                    if want:
                        room.cpu_level = level  # cfg derived from level in cpu_plan()
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
                        room.started_at = time.time()
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
