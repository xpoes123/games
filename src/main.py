from __future__ import annotations

import json
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.rooms import MAX_PLAYERS, Player, Table, table

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
        if len(t.players) != MAX_PLAYERS:
            return
        t.deal()
        snapshot_players = list(t.players)
        hands = [list(h) for h in t.hands]
        dealer = t.dealer

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
            if msg.get("type") == "deal":
                await _handle_deal(table)
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
