"""FastAPI sub-app for Hearthstone Chess.

Mounted at /chess by src/main.py. WS handler accepts ?room=ABCD&name=...
and routes messages through the Room state machine.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.chess.rooms import Phase, Player, Room, registry

log = logging.getLogger("chess")

app = FastAPI(title="chess — games.djiang.xyz", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


_PLACEHOLDER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>chess</title>
<style>body{background:#1a1b26;color:#c0caf5;font-family:ui-monospace,monospace;
padding:3rem;line-height:1.6;}</style></head>
<body><h1>hearthstone chess</h1>
<p>backend up. UI lands in phase 3.</p>
<p><a href="/" style="color:#7aa2f7">back</a></p>
</body></html>
"""


@app.get("/")
async def index() -> HTMLResponse:
    idx = STATIC_DIR / "index.html"
    if idx.exists():
        return FileResponse(idx)  # type: ignore[return-value]
    return HTMLResponse(_PLACEHOLDER_HTML)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "rooms": len(registry.rooms)})


async def _send(player: Player, payload: dict) -> bool:
    if player.ws is None:
        return False
    try:
        await player.ws.send_text(json.dumps(payload))
        return True
    except Exception:
        return False


async def _broadcast_state(room: Room) -> None:
    for p in list(room.players.values()):
        if p.ws is None:
            continue
        snap = room.snapshot_for(p)
        try:
            await p.ws.send_text(json.dumps(snap))
        except Exception:
            p.ws = None


async def _broadcast_events(room: Room, events: list[dict]) -> None:
    for evt in events:
        for p in list(room.players.values()):
            if p.ws is None:
                continue
            try:
                await p.ws.send_text(json.dumps({"type": "event", **evt}))
            except Exception:
                p.ws = None


@app.websocket("/ws")
async def chess_socket(ws: WebSocket) -> None:
    await ws.accept()
    params = ws.query_params
    code = (params.get("room") or "").upper().strip()
    name = (params.get("name") or "anon").strip()[:24] or "anon"
    if not code or len(code) > 8:
        await ws.close(code=4400, reason="room code required")
        return

    room = await registry.get_or_create(code)
    async with room.lock:
        player = room.add_player(name=name, ws=ws)
    await _send(player, {"type": "welcome", "your_seat": player.seat, "player_id": player.pid})
    await _broadcast_state(room)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await _dispatch(room, player, msg)
    except WebSocketDisconnect:
        pass
    finally:
        async with room.lock:
            room.remove_player(player.pid)
        await _broadcast_state(room)


async def _dispatch(room: Room, player: Player, msg: dict) -> None:
    action = msg.get("type")
    if action == "mulligan":
        slots = msg.get("redraw_slots") or []
        async with room.lock:
            err = room.submit_mulligan(player.seat, list(slots))
        if err:
            await _send(player, {"type": "error", "text": err})
            return
        await _broadcast_state(room)
        return

    if action == "play_card":
        slot = msg.get("slot")
        targets = msg.get("targets") or []
        modal = msg.get("modal")
        discard_slots = msg.get("discard_slots") or []
        if not isinstance(slot, int):
            await _send(player, {"type": "error", "text": "missing slot"})
            return
        async with room.lock:
            events, err = room.play_card(player.seat, slot, list(targets), modal, list(discard_slots))
        if err:
            await _send(player, {"type": "error", "text": err})
            return
        await _broadcast_events(room, events)
        await _broadcast_state(room)
        return

    if action == "move":
        src = msg.get("from")
        dst = msg.get("to")
        promote = msg.get("promote")
        if not isinstance(src, str) or not isinstance(dst, str):
            await _send(player, {"type": "error", "text": "bad move"})
            return
        async with room.lock:
            events, err = room.make_move(player.seat, src, dst, promote)
        if err:
            await _send(player, {"type": "error", "text": err})
            return
        await _broadcast_events(room, events)
        await _broadcast_state(room)
        return

    if action == "end_turn":
        async with room.lock:
            err = room.end_turn(player.seat)
        if err:
            await _send(player, {"type": "error", "text": err})
            return
        await _broadcast_events(room, [{"kind": "turn_end",
                                        "next": room.active_seat,
                                        "turn_number": room.turn_number}])
        await _broadcast_state(room)
        return

    if action == "concede":
        async with room.lock:
            room.concede(player.seat)
        await _broadcast_state(room)
        return

    # Unknown / unsupported actions silently dropped in Phase 1.
