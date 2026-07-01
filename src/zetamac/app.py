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

_N_PROBLEMS = 600  # plenty for the fastest player in a race
_DURATIONS = (30, 60, 90, 120)
DEFAULT_CFG = {
    "ops": ["add", "sub", "mul", "div"],
    "add_min": 2, "add_max": 100,        # both addends (subtraction is the inverse)
    "mul_a_min": 2, "mul_a_max": 12,     # first factor (division is the inverse)
    "mul_b_min": 2, "mul_b_max": 100,    # second factor
    "duration": 90,
}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def _problem(rng: random.Random, cfg: dict) -> tuple[str, int]:
    kind = rng.choice(cfg["ops"])
    if kind in ("add", "sub"):
        a = rng.randint(cfg["add_min"], cfg["add_max"])
        b = rng.randint(cfg["add_min"], cfg["add_max"])
        if kind == "add":
            return f"{a} + {b}", a + b
        return f"{a + b} − {b}", a                    # inverse of addition
    a = rng.randint(cfg["mul_a_min"], cfg["mul_a_max"])
    b = rng.randint(cfg["mul_b_min"], cfg["mul_b_max"])
    if kind == "mul":
        return f"{a} × {b}", a * b
    return f"{a * b} ÷ {b}", a                        # inverse of multiplication


def make_stream(cfg: dict = DEFAULT_CFG, n: int = _N_PROBLEMS) -> list[tuple[str, int]]:
    rng = random.Random()
    return [_problem(rng, cfg) for _ in range(n)]


def validate_cfg(c: dict) -> dict:
    """Sanitize a client-submitted config into a safe, usable one."""
    ops = [o for o in ("add", "sub", "mul", "div") if o in (c.get("ops") or [])] or ["add"]

    def rng(key: str, dlo: int, dhi: int):
        try:
            lo, hi = int(c.get(key + "_min", dlo)), int(c.get(key + "_max", dhi))
        except (TypeError, ValueError):
            lo, hi = dlo, dhi
        lo, hi = max(1, min(lo, 9999)), max(1, min(hi, 9999))
        return (lo, hi) if lo <= hi else (hi, lo)

    add_lo, add_hi = rng("add", 2, 100)
    ma_lo, ma_hi = rng("mul_a", 2, 12)
    mb_lo, mb_hi = rng("mul_b", 2, 100)
    dur = c.get("duration")
    return {
        "ops": ops,
        "add_min": add_lo, "add_max": add_hi,
        "mul_a_min": ma_lo, "mul_a_max": ma_hi,
        "mul_b_min": mb_lo, "mul_b_max": mb_hi,
        "duration": dur if dur in _DURATIONS else 90,
    }


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
    cfg: dict = field(default_factory=lambda: dict(DEFAULT_CFG))
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
        "cfg": room.cfg,
        "players": sorted(
            [{"name": p.name, "score": p.score} for p in room.players],
            key=lambda x: -x["score"],
        ),
    }


async def _start(room: Room) -> None:
    async with room.lock:
        if room.started:
            return
        room.problems = make_stream(room.cfg)
        room.started = True
        room.ended = False
        for p in room.players:
            p.score = 0
            p.idx = 0
    await _broadcast(room, {"type": "go", "duration": room.cfg["duration"]})
    for p in list(room.players):
        await _send(p, {"type": "problem", "n": 0, "text": room.problems[0][0]})
    await _broadcast(room, _scoreboard(room))
    room.timer = asyncio.create_task(_end_after(room))


async def _end_after(room: Room) -> None:
    try:
        await asyncio.sleep(room.cfg["duration"])
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
    await _broadcast(room, _scoreboard(room))  # ended=True → re-enables settings + play-again
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
            if action == "set_config":
                if not room.started and isinstance(msg.get("cfg"), dict):
                    room.cfg = validate_cfg(msg["cfg"])
                    await _broadcast(room, _scoreboard(room))
            elif action == "start":
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
