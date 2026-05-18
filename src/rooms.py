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

RANK_VALUE = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8,
    "9": 9, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14,
}


def evaluate_trick(plays: list[tuple[int, Card]]) -> int:
    """Highest card of the led suit wins. No trump for now."""
    led_suit = plays[0][1].suit
    winner_seat = plays[0][0]
    winner_value = RANK_VALUE[plays[0][1].rank]
    for seat, card in plays[1:]:
        if card.suit == led_suit and RANK_VALUE[card.rank] > winner_value:
            winner_seat = seat
            winner_value = RANK_VALUE[card.rank]
    return winner_seat


@dataclass
class Player:
    player_id: str
    name: str
    socket: "WebSocket"


@dataclass
class Table:
    players: list[Player] = field(default_factory=list)
    hands: list[list[Card]] = field(default_factory=list)
    current_trick: list[tuple[int, Card]] = field(default_factory=list)
    tricks_won: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    dealer: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def public_state(self) -> dict:
        return {
            "players": [{"id": p.player_id, "name": p.name} for p in self.players],
            "capacity": MAX_PLAYERS,
            "dealt": bool(self.hands),
            "tricks_won": list(self.tricks_won),
        }

    def deal(self) -> None:
        self.hands = deal_four()
        self.current_trick = []
        self.tricks_won = [0, 0, 0, 0]

    def reset(self) -> None:
        self.hands = []
        self.current_trick = []
        self.tricks_won = [0, 0, 0, 0]

    def play_card(self, seat: int, rank: str, suit: str) -> Card | None:
        if seat < 0 or seat >= len(self.hands):
            return None
        hand = self.hands[seat]
        for i, c in enumerate(hand):
            if c.rank == rank and c.suit == suit:
                hand.pop(i)
                self.current_trick.append((seat, c))
                return c
        return None

    def resolve_trick_if_complete(self) -> int | None:
        """If 4 cards are in the current trick, evaluate, score it, clear."""
        if len(self.current_trick) != 4:
            return None
        winner = evaluate_trick(self.current_trick)
        self.tricks_won[winner] += 1
        self.current_trick = []
        return winner

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
