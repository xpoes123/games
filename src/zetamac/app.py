"""FastAPI sub-app for Zetamac arithmetic racing (mounted at /zetamac).

Everyone in a room gets the IDENTICAL stream of problems and answers as many as
they can in DURATION seconds; most correct wins. Classic zetamac mix: addition
and multiplication, with subtraction and division as their inverses.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import string
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src import auth, store

log = logging.getLogger("zetamac")
app = FastAPI(title="zetamac — games.djiang.xyz", docs_url=None, redoc_url=None)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

DURATION_S = 90
_N_PROBLEMS = 600  # plenty for the fastest player in DURATION_S


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def _problem(rng: random.Random) -> tuple[str, int]:
    kind = rng.choice(("add", "sub", "mul", "div"))
    if kind == "add":
        a, b = rng.randint(2, 100), rng.randint(2, 100)
        return f"{a} + {b}", a + b
    if kind == "sub":
        a, b = rng.randint(2, 100), rng.randint(2, 100)
        return f"{a + b} − {b}", a            # inverse of addition
    if kind == "mul":
        a, b = rng.randint(2, 12), rng.randint(2, 100)
        return f"{a} × {b}", a * b
    a, b = rng.randint(2, 12), rng.randint(2, 100)
    return f"{a * b} ÷ {b}", a                 # inverse of multiplication


def make_stream(n: int = _N_PROBLEMS) -> list[tuple[str, int]]:
    rng = random.Random()
    return [_problem(rng) for _ in range(n)]


@dataclass
class Player:
    name: str
    ws: object
    guest_id: str = ""
    score: int = 0
    idx: int = 0


@dataclass
class Room:
    code: str
    players: list[Player] = field(default_factory=list)
    problems: list = field(default_factory=list)
    started: bool = False
    ended: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    timer: object = field(default=None, repr=False, compare=False)


ROOMS: dict[str, Room] = {}


def _make_room() -> Room:
    while True:
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in ROOMS:
            room = Room(code=code)
            ROOMS[code] = room
            return room


async def _send(p: Player, payload: dict) -> None:
    try:
        await p.ws.send_text(json.dumps(payload))
    except Exception:
        pass


async def _broadcast(room: Room, payload: dict) -> None:
    for p in list(room.players):
        await _send(p, payload)


def _scoreboard(room: Room) -> dict:
    return {
        "type": "scores",
        "started": room.started,
        "ended": room.ended,
        "players": sorted(
            [{"name": p.name, "score": p.score} for p in room.players],
            key=lambda x: -x["score"],
        ),
    }


async def _start(room: Room) -> None:
    async with room.lock:
        if room.started:
            return
        room.problems = make_stream()
        room.started = True
        room.ended = False
        for p in room.players:
            p.score = 0
            p.idx = 0
    await _broadcast(room, {"type": "go", "duration": DURATION_S})
    for p in list(room.players):
        await _send(p, {"type": "problem", "n": 0, "text": room.problems[0][0]})
    await _broadcast(room, _scoreboard(room))
    room.timer = asyncio.create_task(_end_after(room))


async def _end_after(room: Room) -> None:
    try:
        await asyncio.sleep(DURATION_S)
    except asyncio.CancelledError:
        return
    async with room.lock:
        if room.ended:
            return
        room.ended = True
        room.started = False
        ranked = sorted(room.players, key=lambda p: -p.score)
        top = ranked[0].score if ranked else 0
        winners = [p for p in ranked if p.score == top]
        solo = len([p for p in room.players if p.guest_id]) < 2
        for p in room.players:
            if not p.guest_id:
                continue
            others = [q.name for q in room.players if q is not p]
            store.record_game("zetamac", p.guest_id, p.name, won=(p in winners and not solo),
                              mode="race", opponent=others[0] if others else None, score=p.score)
    await _broadcast(room, {"type": "over", "winner": ranked[0].name if ranked else None,
                            "scores": _scoreboard(room)["players"]})


@app.websocket("/ws")
async def ws(sock: WebSocket) -> None:
    await sock.accept()
    try:
        hello = json.loads(await sock.receive_text())
        name = (hello.get("name") or "anon").strip()[:24] or "anon"
        code = (hello.get("code") or "").strip().upper()
    except Exception:
        await sock.close(code=4400, reason="bad hello")
        return

    room = ROOMS[code] if code and code in ROOMS else _make_room()
    ident = auth.identity(sock.cookies)
    p = Player(name=name, ws=sock, guest_id=ident["guest_id"])
    if ident["discord_id"]:
        store.link_guest(p.guest_id, ident["discord_id"])
    room.players.append(p)
    await _send(p, {"type": "joined", "code": room.code})
    await _broadcast(room, _scoreboard(room))

    try:
        while True:
            msg = json.loads(await sock.receive_text())
            action = msg.get("type")
            if action == "start":
                await _start(room)
            elif action == "answer":
                if not room.started or room.ended or p.idx >= len(room.problems):
                    continue
                try:
                    val = int(str(msg.get("value", "")).strip())
                except ValueError:
                    continue
                if val == room.problems[p.idx][1]:
                    p.score += 1
                    p.idx += 1
                    nxt = room.problems[p.idx][0] if p.idx < len(room.problems) else ""
                    await _send(p, {"type": "problem", "n": p.idx, "text": nxt})
                    await _broadcast(room, _scoreboard(room))
    except WebSocketDisconnect:
        pass
    finally:
        if p in room.players:
            room.players.remove(p)
        if not room.players:
            if room.timer:
                room.timer.cancel()
            ROOMS.pop(room.code, None)
        else:
            await _broadcast(room, _scoreboard(room))
