"""Deck assembly. Each player gets their own copy of the 102-card list,
shuffled per game."""
from __future__ import annotations

import random
import secrets

from src.chess.cards import CARD_DEFS, Card


def build_deck(rng: random.Random | None = None) -> list[Card]:
    cards: list[Card] = []
    for defn in CARD_DEFS:
        for _ in range(defn.copies):
            cards.append(Card(instance_id=secrets.token_hex(4), defn=defn))
    (rng or random).shuffle(cards)
    return cards


def draw(deck: list[Card], n: int) -> list[Card]:
    """Pop up to n cards off the top. Fewer if deck runs out."""
    drawn: list[Card] = []
    for _ in range(n):
        if not deck:
            break
        drawn.append(deck.pop())
    return drawn
