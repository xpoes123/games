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

from src.chess import persistence
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


@app.get("/api/games")
async def api_games(limit: int = 50, player_id: str | None = None) -> JSONResponse:
    rows = persistence.list_games(limit=limit, player_id=player_id)
    return JSONResponse({"games": rows})


@app.get("/api/games/{slug}")
async def api_game(slug: str) -> JSONResponse:
    row = persistence.get_game(slug)
    if row is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(row)


@app.get("/api/leaderboard")
async def api_leaderboard(limit: int = 50, min_games: int = 3) -> JSONResponse:
    rows = persistence.leaderboard(limit=limit, min_games=min_games)
    return JSONResponse({"players": rows})


@app.get("/api/players/{client_id}")
async def api_player(client_id: str) -> JSONResponse:
    row = persistence.get_player(client_id)
    if row is None:
        # 200 with null so the lobby can show "unrated" without exception-handling.
        return JSONResponse({"player": None})
    return JSONResponse({"player": row})


def _render_static(filename: str) -> HTMLResponse:
    path = STATIC_DIR / filename
    if not path.exists():
        return HTMLResponse(_PLACEHOLDER_HTML, status_code=404)
    html = path.read_text()
    # Same cache-busting strategy as the lobby page.
    for asset in ("app.js", "style.css", "games.js", "replay.js", "leaderboard.js"):
        ap = STATIC_DIR / asset
        if ap.exists():
            v = int(ap.stat().st_mtime)
            html = html.replace(f"/chess/static/{asset}",
                                f"/chess/static/{asset}?v={v}")
    return HTMLResponse(html)


@app.get("/games")
async def games_index() -> HTMLResponse:
    return _render_static("games.html")


@app.get("/leaderboard")
async def leaderboard_page() -> HTMLResponse:
    return _render_static("leaderboard.html")


@app.get("/replay/{slug}")
async def replay_page(slug: str) -> HTMLResponse:
    return _render_static("replay.html")


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
    _persist_tick(room)
    for p in list(room.players.values()):
        if p.ws is None:
            continue
        snap = room.snapshot_for(p)
        try:
            await p.ws.send_text(json.dumps(snap))
        except Exception:
            p.ws = None


async def _broadcast_pending_prompts(room: Room) -> None:
    """Send a separate `prompt` message to each player who has a pending
    prompt addressed to them. The snapshot also carries it, but the client
    listens for the dedicated message to drive the right-rail UI."""
    for pr in list(room.pending_prompts.values()):
        target = room.player_by_seat(pr.seat)
        if target is None or target.ws is None:
            continue
        try:
            await target.ws.send_text(json.dumps(pr.to_json()))
        except Exception:
            target.ws = None


async def _arm_setup_timer(room: Room) -> None:
    """Auto-fill + force-confirm any seat that hasn't confirmed within
    SETUP_BUDGET_MS. Prevents stalled games when one side walks away."""
    import asyncio
    from src.chess.rooms import Phase, SETUP_BUDGET_MS

    if room.setup_timer_task is not None and not room.setup_timer_task.done():
        room.setup_timer_task.cancel()
    room.setup_timer_task = None
    if room.phase != Phase.SETUP:
        return
    started = room.setup_started_ms

    async def _fire() -> None:
        delay = SETUP_BUDGET_MS / 1000
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        async with room.lock:
            # Stale-timer guard.
            if room.phase != Phase.SETUP or room.setup_started_ms != started:
                return
            for seat in ("white", "black"):
                p = room.player_by_seat(seat)
                if p is None or p.setup_confirmed:
                    continue
                room.setup_auto_fill_and_confirm(seat)
                room._log(f"{seat} timed out — auto-confirmed setup")
        await _broadcast_state(room)
        await _arm_turn_timer(room)

    room.setup_timer_task = asyncio.create_task(_fire())


async def _arm_opp_prompt_timer(room: Room) -> None:
    """If a Forced Promotion prompt is pending for the opponent, schedule an
    auto-resolve with the default ('Knight') after OPP_DECISION_BUDGET_MS."""
    import asyncio
    from src.chess.rooms import OPP_DECISION_BUDGET_MS

    if room.opp_prompt_task is not None and not room.opp_prompt_task.done():
        room.opp_prompt_task.cancel()
    room.opp_prompt_task = None
    if room.pending_card_play is None:
        return
    if room.pending_card_play.card.defn.id != "spell_forced_promotion":
        return
    deadline = room.opp_prompt_deadline_ms
    if deadline is None:
        return
    caster_seat = room.pending_card_play.seat
    opp_seat = "black" if caster_seat == "white" else "white"

    async def _fire() -> None:
        delay = max(0.0, (deadline - room.now_ms()) / 1000)
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        async with room.lock:
            if room.pending_card_play is None:
                return
            if room.pending_card_play.card.defn.id != "spell_forced_promotion":
                return
            events, err = room.apply_forced_promotion_pick(opp_seat, "Knight")
        if err:
            return
        await _broadcast_events(room, events)
        await _broadcast_state(room)
        await _arm_turn_timer(room)

    room.opp_prompt_task = asyncio.create_task(_fire())


async def _arm_turn_timer(room: Room) -> None:
    """Cancel any existing per-turn timer; if a turn is active, arm a new one
    that auto-ends the turn after TURN_BUDGET_MS. Safety net against
    unresponsive clients / mid-prompt deadlocks."""
    import asyncio

    if room.turn_timer_task is not None and not room.turn_timer_task.done():
        room.turn_timer_task.cancel()
    room.turn_timer_task = None
    if room.phase != Phase.PLAYING:
        return
    # If we're currently paused waiting on an opp prompt, don't arm a turn
    # timer yet — it'll be re-armed when the opp resolves.
    if room.pause_start_ms is not None:
        return
    seat_at_arm = room.active_seat
    turn_at_arm = room.turn_number
    started_at_arm = room.turn_started_ms

    async def _fire() -> None:
        from src.chess.rooms import TURN_BUDGET_MS
        try:
            await asyncio.sleep(TURN_BUDGET_MS / 1000)
        except asyncio.CancelledError:
            return
        async with room.lock:
            # Stale-timer guard: turn may have already advanced naturally, or
            # turn_started_ms may have been extended by an opp-prompt pause.
            if room.phase != Phase.PLAYING:
                return
            if room.active_seat != seat_at_arm or room.turn_number != turn_at_arm:
                return
            if room.turn_started_ms != started_at_arm:
                # Deadline shifted (paused during this turn) — bail; the
                # resume path will arm a fresh timer.
                return
            # Force the turn to end. Bypass the "must move" and in-check rules
            # so a stuck player never deadlocks the game; if they're in check
            # at timeout, that's a loss-equivalent state but we don't make it
            # game-ending here — the opponent can capture on their turn.
            p = room.player_by_seat(seat_at_arm)
            if p:
                p.has_acted_this_turn = True  # bypass must-move
            room._log(f"{seat_at_arm} timed out — auto end turn")
            room.active_seat = "black" if seat_at_arm == "white" else "white"
            room.turn_number += 1
            if p:
                p.reset_turn_flags()
            room.board.clear_sickness_for(seat_at_arm)  # type: ignore[arg-type]
            room.undo_snapshot = None
            room.begin_turn(room.active_seat)
        await _broadcast_events(room, [{"kind": "turn_end",
                                        "next": room.active_seat,
                                        "turn_number": room.turn_number}])
        await _broadcast_pending_prompts(room)
        await _broadcast_state(room)
        await _arm_turn_timer(room)

    room.turn_timer_task = asyncio.create_task(_fire())


async def _broadcast_events(room: Room, events: list[dict]) -> None:
    # Persist before broadcast — slug may not exist yet if the game just
    # transitioned to PLAYING this dispatch, so _persist_tick first.
    _persist_tick(room)
    persistence.append_events(room.persistence_slug, events)
    for evt in events:
        for p in list(room.players.values()):
            if p.ws is None:
                continue
            try:
                await p.ws.send_text(json.dumps({"type": "event", **evt}))
            except Exception:
                p.ws = None


def _persist_tick(room: Room) -> None:
    """Called after every state mutation. Creates a DB row the first time the
    room enters PLAYING; finalizes it when a winner appears. Idempotent."""
    if not persistence.enabled():
        return
    if room.persistence_slug is None and room.phase == Phase.PLAYING:
        white = room.player_by_seat("white")
        black = room.player_by_seat("black")
        if white and black:
            room.persistence_slug = persistence.create_game(
                room_code=room.code,
                white_name=white.name,
                black_name=black.name,
                white_player_id=white.client_id,
                black_player_id=black.client_id,
                white_setup=room.starting_setup_white,
                black_setup=room.starting_setup_black,
            )
    if (room.persistence_slug
            and not room.persistence_finalized
            and room.winner is not None):
        persistence.finalize_game(
            room.persistence_slug, room.winner, room.win_reason,
        )
        room.persistence_finalized = True


@app.websocket("/ws")
async def chess_socket(ws: WebSocket) -> None:
    await ws.accept()
    params = ws.query_params
    code = (params.get("room") or "").upper().strip()
    name = (params.get("name") or "anon").strip()[:24] or "anon"
    client_id = (params.get("client_id") or "").strip()[:64] or None
    if not code or len(code) > 8:
        await ws.close(code=4400, reason="room code required")
        return

    room = await registry.get_or_create(code)
    async with room.lock:
        player = room.add_player(name=name, ws=ws, client_id=client_id)
    await _send(player, {"type": "welcome", "your_seat": player.seat, "player_id": player.pid})
    await _broadcast_state(room)
    # Re-deliver any pending prompt addressed to the rejoining player so
    # their right-rail UI rehydrates correctly after refresh.
    for pr in list(room.pending_prompts.values()):
        if pr.seat == player.seat:
            await _send(player, pr.to_json())
    await _arm_turn_timer(room)

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
        # If both mulligans landed → we just entered SETUP. Arm its timer.
        from src.chess.rooms import Phase as _Phase
        if room.phase == _Phase.SETUP:
            await _arm_setup_timer(room)
        return

    if action == "setup_place":
        kind = msg.get("kind")
        sq = msg.get("square")
        if not isinstance(kind, str) or not isinstance(sq, str):
            return
        async with room.lock:
            err = room.setup_place(player.seat, kind, sq)
        if err:
            await _send(player, {"type": "error", "text": err})
            return
        await _broadcast_state(room)
        return

    if action == "setup_remove":
        sq = msg.get("square")
        if not isinstance(sq, str):
            return
        async with room.lock:
            err = room.setup_remove(player.seat, sq)
        if err:
            await _send(player, {"type": "error", "text": err})
            return
        await _broadcast_state(room)
        return

    if action == "setup_confirm":
        async with room.lock:
            err = room.setup_confirm(player.seat)
        if err:
            await _send(player, {"type": "error", "text": err})
            return
        await _broadcast_state(room)
        # Cancel setup timer if we're now in PLAYING.
        from src.chess.rooms import Phase as _Phase
        if room.phase == _Phase.PLAYING:
            if room.setup_timer_task is not None and not room.setup_timer_task.done():
                room.setup_timer_task.cancel()
            room.setup_timer_task = None
        await _arm_turn_timer(room)
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
        await _broadcast_pending_prompts(room)
        # If Forced Promotion paused the turn, cancel turn timer + arm opp's.
        if room.pause_start_ms is not None:
            if room.turn_timer_task is not None and not room.turn_timer_task.done():
                room.turn_timer_task.cancel()
            await _arm_opp_prompt_timer(room)
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
        await _broadcast_pending_prompts(room)
        await _broadcast_state(room)
        await _arm_turn_timer(room)
        return

    if action == "undo_move":
        async with room.lock:
            err = room.undo_move(player.seat)
        if err:
            await _send(player, {"type": "error", "text": err})
            return
        await _broadcast_state(room)
        return

    if action == "decline_echo":
        async with room.lock:
            room.decline_echo(player.seat)
        await _broadcast_state(room)
        return

    if action == "cancel_prompt":
        prompt_id = msg.get("prompt_id")
        async with room.lock:
            prompt = room.pending_prompts.get(prompt_id) if prompt_id else None
            if prompt is None:
                return
            if prompt.seat != player.seat:
                return
            err = None
            if prompt.kind == "pick_opp_hand":
                err = room.cancel_echo_pick(player.seat)
            # Other prompt kinds (choose_opp_move, select_modal for opp's
            # forced-promotion) don't support cancel — caster/opp must
            # answer, and the server has its own auto-resolve timer.
        if err:
            await _send(player, {"type": "error", "text": err})
            return
        await _broadcast_state(room)
        return

    if action == "toggle_annotation":
        sq = msg.get("square")
        if not isinstance(sq, str):
            return
        async with room.lock:
            room.toggle_annotation(player.seat, sq)
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
        await _arm_turn_timer(room)
        return

    if action == "prompt_response":
        prompt_id = msg.get("prompt_id")
        async with room.lock:
            prompt = room.pending_prompts.get(prompt_id) if prompt_id else None
            if prompt is None:
                await _send(player, {"type": "error", "text": "no pending prompt"})
                return
            if prompt.seat != player.seat:
                await _send(player, {"type": "error", "text": "prompt not for you"})
                return
            if prompt.kind == "choose_opp_move":
                move = msg.get("move") or {}
                events, err = room.apply_opp_chosen_move(
                    player.seat, move.get("from"), move.get("to"), move.get("promote"))
            elif prompt.kind == "pick_opp_hand":
                events, err = room.apply_echo_pick(player.seat, int(msg.get("opp_slot", -1)))
            elif prompt.kind == "select_modal":
                # Currently only used by Forced Promotion (opponent picks).
                option = msg.get("option")
                events, err = room.apply_forced_promotion_pick(player.seat, option or "")
            else:
                events, err = [], f"unknown prompt kind {prompt.kind}"
        if err:
            await _send(player, {"type": "error", "text": err})
            return
        await _broadcast_events(room, events)
        await _broadcast_pending_prompts(room)
        # If this prompt_response resolved a Forced Promotion, re-arm the
        # caster's turn timer (which was paused while the opp decided).
        if room.pause_start_ms is None and room.opp_prompt_task is not None:
            if not room.opp_prompt_task.done():
                room.opp_prompt_task.cancel()
            room.opp_prompt_task = None
            await _arm_turn_timer(room)
        await _broadcast_state(room)
        return

    # Unknown / unsupported actions silently dropped in Phase 1.
