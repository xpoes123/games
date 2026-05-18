"""Single in-process table — rooms removed for now.

If we ever need multiple concurrent games, swap this for a registry keyed by
some join code or game type. For now: one table, one game, share the URL.
"""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.cards import Card, deal_four

if TYPE_CHECKING:
    from fastapi import WebSocket


MAX_PLAYERS = 4


@dataclass
class Player:
    player_id: str
    name: str
    socket: "WebSocket"


@dataclass
class Table:
    players: list[Player] = field(default_factory=list)
    hands: list[list[Card]] = field(default_factory=list)
    played: list[tuple[int, Card]] = field(default_factory=list)
    dealer: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def public_state(self) -> dict:
        return {
            "players": [{"id": p.player_id, "name": p.name} for p in self.players],
            "capacity": MAX_PLAYERS,
            "dealt": bool(self.hands),
        }

    def deal(self) -> None:
        self.hands = deal_four()
        self.played = []

    def reset(self) -> None:
        self.hands = []
        self.played = []

    def play_card(self, seat: int, rank: str, suit: str) -> Card | None:
        """Remove the named card from seat's hand and record it as played."""
        if seat < 0 or seat >= len(self.hands):
            return None
        hand = self.hands[seat]
        for i, c in enumerate(hand):
            if c.rank == rank and c.suit == suit:
                hand.pop(i)
                self.played.append((seat, c))
                return c
        return None

    async def add_player(self, name: str, ws: "WebSocket") -> Player | None:
        async with self.lock:
            if len(self.players) >= MAX_PLAYERS:
                return None
            player = Player(player_id=secrets.token_hex(4), name=name, socket=ws)
            self.players.append(player)
            return player

    async def remove_player(self, player: Player) -> None:
        async with self.lock:
            if player in self.players:
                self.players.remove(player)
            self.reset()

    def seat_of(self, player: Player) -> int | None:
        try:
            return self.players.index(player)
        except ValueError:
            return None


table = Table()
