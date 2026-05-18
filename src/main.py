from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.rooms import MAX_PLAYERS, Player, Room, registry

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


@app.post("/api/rooms")
async def create_room() -> dict:
    room = await registry.create()
    return {"code": room.code}


@app.get("/api/rooms/{code}")
async def get_room(code: str) -> dict:
    room = registry.get(code)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    return room.public_state()


async def _send(player: Player, payload: dict) -> bool:
    try:
        await player.socket.send_text(json.dumps(payload))
        return True
    except Exception:
        return False


async def _broadcast(room: Room, payload: dict) -> None:
    for p in list(room.players):
        if not await _send(p, payload):
            if p in room.players:
                room.players.remove(p)


async def _broadcast_state(room: Room) -> None:
    await _broadcast(room, {"type": "state", "room": room.public_state()})


async def _handle_deal(room: Room) -> None:
    """Deal 13 cards to each seat. Each player sees only their own hand."""
    async with room.lock:
        if len(room.players) != MAX_PLAYERS:
            return
        room.deal()
        snapshot_players = list(room.players)
        hands = [list(h) for h in room.hands]
        dealer = room.dealer

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


@app.websocket("/ws/{code}")
async def room_socket(ws: WebSocket, code: str) -> None:
    room = registry.get(code)
    if room is None:
        await ws.close(code=4404, reason="room not found")
        return

    await ws.accept()
    try:
        hello = json.loads(await ws.receive_text())
        name = (hello.get("name") or "Player").strip()[:24] or "Player"
    except Exception:
        await ws.close(code=4400, reason="bad hello")
        return

    async with room.lock:
        if len(room.players) >= MAX_PLAYERS:
            await ws.close(code=4403, reason="room full")
            return
        player = Player(player_id=secrets.token_hex(4), name=name, socket=ws)
        room.players.append(player)
        seat = len(room.players) - 1

    await _send(player, {"type": "welcome", "your_seat": seat})
    await _broadcast_state(room)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            action = msg.get("type")
            if action == "deal":
                await _handle_deal(room)
    except WebSocketDisconnect:
        pass
    finally:
        async with room.lock:
            if player in room.players:
                room.players.remove(player)
            # Reset deal state if anyone leaves mid-round
            room.hands = []
        await _broadcast_state(room)
        await registry.drop_if_empty(room.code)


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
