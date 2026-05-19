"""FastAPI sub-app for Hearthstone Chess.

Mounted at /chess by src/main.py. WS handler accepts ?room=ABCD&name=...
and routes messages through the Room state machine.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import os

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.chess.board import Piece
from src.chess.cards import CARDS_BY_ID
from src.chess.deck import Card
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
    if not idx.exists():
        return HTMLResponse(_PLACEHOLDER_HTML)
    html = idx.read_text()
    # Cache-bust static asset URLs by the file's mtime — so a deploy that
    # touches app.js/style.css forces clients to fetch the new content
    # rather than serve a stale cached version.
    for asset in ("app.js", "style.css"):
        path = STATIC_DIR / asset
        if path.exists():
            v = int(path.stat().st_mtime)
            html = html.replace(f"/chess/static/{asset}",
                                f"/chess/static/{asset}?v={v}")
    return HTMLResponse(html)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "rooms": len(registry.rooms)})


def _debug_allowed() -> bool:
    return os.getenv("CHESS_DEBUG_SEED", "").lower() in ("1", "true", "yes")


@app.post("/debug/seed")
async def debug_seed(req: Request) -> JSONResponse:
    """Force a room into a specific state for dogfood/testing.

    Gated by CHESS_DEBUG_SEED env var. Body:
    {
      "room": "ABCD",
      "phase": "PLAYING",          // optional, defaults to current
      "active_seat": "white",      // optional
      "board": [{"sq":"e1","color":"white","kind":"king"}, ...],  // replaces board
      "white": {"hand": ["spell_combine_pawns", "piece_pawn"], "gold": 10, "gold_cap": 10},
      "black": {"hand": ["piece_pawn"], "gold": 5, "gold_cap": 5},
      "clear_sickness": true       // strip placed_this_turn on all pieces
    }
    """
    if not _debug_allowed():
        return JSONResponse({"error": "debug disabled"}, status_code=403)
    body = await req.json()
    code = (body.get("room") or "").upper().strip()
    if not code:
        return JSONResponse({"error": "room required"}, status_code=400)
    room = await registry.get_or_create(code)
    async with room.lock:
        if body.get("board") is not None:
            room.board.squares.clear()
            for spec in body["board"]:
                pc = Piece(spec["color"], spec["kind"],
                           has_moved=spec.get("has_moved", False),
                           placed_this_turn=spec.get("placed_this_turn", False))
                room.board.squares[spec["sq"]] = pc
        if body.get("clear_sickness"):
            for pc in room.board.squares.values():
                pc.placed_this_turn = False
        for seat in ("white", "black"):
            spec = body.get(seat) or {}
            p = room.player_by_seat(seat)
            if not p:
                continue
            if "hand" in spec:
                p.hand = [Card(instance_id=f"dbg-{i}", defn=CARDS_BY_ID[cid])
                          for i, cid in enumerate(spec["hand"])]
            if "gold" in spec:
                p.gold = int(spec["gold"])
            if "gold_cap" in spec:
                p.gold_cap = int(spec["gold_cap"])
            if "moves_remaining" in spec:
                p.moves_remaining = int(spec["moves_remaining"])
            if "has_acted_this_turn" in spec:
                p.has_acted_this_turn = bool(spec["has_acted_this_turn"])
        if "active_seat" in body:
            room.active_seat = body["active_seat"]
        if "phase" in body:
            room.phase = Phase(body["phase"])
        room._log(f"DEBUG seed applied")
    # Broadcast fresh state to whoever is listening.
    for pid, p in list(room.players.items()):
        if p.ws is None:
            continue
        try:
            await p.ws.send_text(json.dumps(room.snapshot_for(p)))
        except Exception:
            pass
    return JSONResponse({"ok": True, "room": code})


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

    if action == "new_game":
        async with room.lock:
            err = room.rematch()
        if err:
            await _send(player, {"type": "error", "text": err})
            return
        await _broadcast_state(room)
        return

    # Unknown / unsupported actions silently dropped in Phase 1.
