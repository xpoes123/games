"""In-memory room registry. Replace with persistent store once we spec the game."""
from __future__ import annotations

import asyncio
import random
import string
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import WebSocket


ROOM_CODE_ALPHABET = string.ascii_uppercase
ROOM_CODE_LEN = 4
MAX_PLAYERS = 4


@dataclass
class Player:
    player_id: str
    name: str
    socket: "WebSocket"


@dataclass
class Room:
    code: str
    players: list[Player] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def public_state(self) -> dict:
        return {
            "code": self.code,
            "players": [{"id": p.player_id, "name": p.name} for p in self.players],
            "capacity": MAX_PLAYERS,
        }


class RoomRegistry:
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._lock = asyncio.Lock()

    async def create(self) -> Room:
        async with self._lock:
            for _ in range(50):
                code = "".join(random.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LEN))
                if code not in self._rooms:
                    room = Room(code=code)
                    self._rooms[code] = room
                    return room
            raise RuntimeError("could not allocate unique room code")

    def get(self, code: str) -> Room | None:
        return self._rooms.get(code.upper())

    async def drop_if_empty(self, code: str) -> None:
        async with self._lock:
            room = self._rooms.get(code)
            if room is not None and not room.players:
                self._rooms.pop(code, None)


registry = RoomRegistry()
