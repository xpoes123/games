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
from src.rooms import MAX_PLAYERS, Player, registry

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


async def _broadcast(room, payload: dict) -> None:
    dead: list[Player] = []
    msg = json.dumps(payload)
    for p in room.players:
        try:
            await p.socket.send_text(msg)
        except Exception:
            dead.append(p)
    for p in dead:
        if p in room.players:
            room.players.remove(p)


@app.websocket("/ws/{code}")
async def room_socket(ws: WebSocket, code: str) -> None:
    room = registry.get(code)
    if room is None:
        await ws.close(code=4404, reason="room not found")
        return

    await ws.accept()
    try:
        hello_raw = await ws.receive_text()
        hello = json.loads(hello_raw)
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

    await _broadcast(room, {"type": "state", "room": room.public_state()})

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # Echo-relay for now. Game logic gets added once we spec it.
            await _broadcast(room, {
                "type": "chat",
                "from": player.name,
                "body": msg.get("body", ""),
            })
    except WebSocketDisconnect:
        pass
    finally:
        async with room.lock:
            if player in room.players:
                room.players.remove(player)
        await _broadcast(room, {"type": "state", "room": room.public_state()})
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
