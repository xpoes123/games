from __future__ import annotations

import json
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.rooms import MAX_PLAYERS, Phase, Player, Table, table

log = logging.getLogger("games")

app = FastAPI(title="games.djiang.xyz", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


async def _send(player: Player, payload: dict) -> bool:
    try:
        await player.socket.send_text(json.dumps(payload))
        return True
    except Exception:
        return False


async def _broadcast(t: Table, payload: dict) -> None:
    for p in list(t.players):
        if not await _send(p, payload):
            if p in t.players:
                t.players.remove(p)


async def _broadcast_state(t: Table) -> None:
    await _broadcast(t, {"type": "state", "table": t.public_state()})


async def _handle_deal(t: Table) -> None:
    async with t.lock:
        if len(t.players) < 1:
            return
        t.deal()
        snapshot_players = list(t.players)
        hands = [list(h) for h in t.hands]
        dealer = t.dealer
        # Note: if dealer seat is empty (no player there), we can't start
        # bidding properly. For now require dealer to be a connected player.
        if dealer >= len(snapshot_players):
            # Fall back: rotate dealer to the first connected player.
            t.dealer = 0
            t.current_bidder = 0
            dealer = 0
        # If we landed mid-deal with empty seats, the next-bidder auto-pass
        # logic runs once a real bid/pass arrives.

    for seat, player in enumerate(snapshot_players):
        view = []
        for s in range(MAX_PLAYERS):
            if s == seat:
                view.append([c.to_json() for c in hands[s]])
            else:
                view.append([{"hidden": True} for _ in hands[s]])
        await _send(player, {
            "type": "deal",
            "dealer": dealer,
            "your_seat": seat,
            "hands": view,
        })
    await _broadcast_state(t)


async def _handle_bid(t: Table, player: Player, msg: dict) -> None:
    level = msg.get("level")
    strain = msg.get("strain")
    if not isinstance(level, int) or not isinstance(strain, str):
        return
    async with t.lock:
        seat = t.seat_of(player)
        if seat is None:
            return
        err = t.submit_bid(seat, level, strain)
    if err is not None:
        await _send(player, {"type": "error", "message": err})
        return
    await _broadcast_state(t)


async def _handle_pass(t: Table, player: Player) -> None:
    async with t.lock:
        seat = t.seat_of(player)
        if seat is None:
            return
        err = t.submit_pass(seat)
    if err is not None:
        await _send(player, {"type": "error", "message": err})
        return
    await _broadcast_state(t)


async def _handle_call_partner(t: Table, player: Player, msg: dict) -> None:
    rank = msg.get("rank")
    suit = msg.get("suit")
    if not isinstance(rank, str) or not isinstance(suit, str):
        return
    private_partner_seat = None
    async with t.lock:
        seat = t.seat_of(player)
        if seat is None:
            return
        err = t.submit_call_partner(seat, rank, suit)
        if err is None:
            private_partner_seat = t.partner_seat
            partner_player = (
                t.players[private_partner_seat]
                if private_partner_seat is not None
                and private_partner_seat < len(t.players)
                else None
            )
            card_json = t.partner_card.to_json() if t.partner_card else None
    if err is not None:
        await _send(player, {"type": "error", "message": err})
        return
    # Private message to the partner (only)
    if partner_player is not None and partner_player is not player:
        await _send(partner_player, {
            "type": "you_are_partner",
            "card": card_json,
            "declarer": seat,
        })
    # Public: declarer called a card; everyone learns what but not who has it
    await _broadcast(t, {"type": "partner_called", "card": card_json})
    await _broadcast_state(t)


async def _handle_play(t: Table, player: Player, msg: dict) -> None:
    rank = msg.get("rank")
    suit = msg.get("suit")
    if not isinstance(rank, str) or not isinstance(suit, str):
        return
    async with t.lock:
        seat = t.seat_of(player)
        if seat is None:
            return
        card, err = t.play_card(seat, rank, suit)
        if card is None:
            await _send(player, {"type": "error", "message": err or "illegal play"})
            return
        winner = t.resolve_trick_if_complete()
        tricks_snapshot = list(t.tricks_won)

    await _broadcast(t, {
        "type": "play",
        "seat": seat,
        "card": card.to_json(),
    })

    if winner is not None:
        await _broadcast(t, {
            "type": "trick_won",
            "winner": winner,
            "tricks_won": tricks_snapshot,
        })
    await _broadcast_state(t)


@app.websocket("/ws")
async def table_socket(ws: WebSocket) -> None:
    await ws.accept()
    try:
        hello = json.loads(await ws.receive_text())
        name = (hello.get("name") or "anon").strip()[:24] or "anon"
    except Exception:
        await ws.close(code=4400, reason="bad hello")
        return

    player = await table.add_player(name, ws)
    if player is None:
        await ws.close(code=4403, reason="table full")
        return

    seat = table.seat_of(player)
    await _send(player, {"type": "welcome", "your_seat": seat})
    await _broadcast_state(table)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            action = msg.get("type")
            if action == "deal":
                await _handle_deal(table)
            elif action == "bid":
                await _handle_bid(table, player, msg)
            elif action == "pass":
                await _handle_pass(table, player)
            elif action == "call_partner":
                await _handle_call_partner(table, player, msg)
            elif action == "play":
                await _handle_play(table, player, msg)
    except WebSocketDisconnect:
        pass
    finally:
        await table.remove_player(player)
        await _broadcast_state(table)


def main() -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
