from __future__ import annotations

import random
from dataclasses import dataclass

SUITS = ("S", "H", "D", "C")
RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A")


@dataclass(frozen=True, slots=True)
class Card:
    rank: str
    suit: str

    def to_json(self) -> dict:
        return {"rank": self.rank, "suit": self.suit}


def new_deck() -> list[Card]:
    return [Card(r, s) for s in SUITS for r in RANKS]


def deal_four(rng: random.Random | None = None) -> list[list[Card]]:
    """Return four 13-card hands, indexed by seat 0..3."""
    deck = new_deck()
    (rng or random).shuffle(deck)
    return [deck[i * 13 : (i + 1) * 13] for i in range(4)]
