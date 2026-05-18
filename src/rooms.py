"""Single in-process table with phase-based game state.

Phases: LOBBY → BIDDING → CALLING → PLAYING → DONE.
"""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from src.cards import Card, deal_four

if TYPE_CHECKING:
    from fastapi import WebSocket


MAX_PLAYERS = 4

RANK_VALUE = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8,
    "9": 9, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14,
}

# Strain order: NT is the LEAST-prioritized strain in this variant, then clubs
# up to spades. Within a level, 1NT < 1C < 1D < 1H < 1S < 2NT < ...
STRAIN_RANK = {"NT": 0, "C": 1, "D": 2, "H": 3, "S": 4}
STRAINS = list(STRAIN_RANK.keys())


def bid_value(level: int, strain: str) -> int:
    return level * 5 + STRAIN_RANK[strain]


class Phase(str, Enum):
    LOBBY = "lobby"
    BIDDING = "bidding"
    CALLING = "calling"
    PLAYING = "playing"
    DONE = "done"


@dataclass
class Bid:
    seat: int
    level: int
    strain: str

    @property
    def value(self) -> int:
        return bid_value(self.level, self.strain)

    def to_json(self) -> dict:
        return {"seat": self.seat, "level": self.level, "strain": self.strain}


def evaluate_trick(plays: list[tuple[int, Card]], trump: str | None) -> int:
    """Trump beats off-suit. Among same trump-status, highest of led suit wins."""
    led_suit = plays[0][1].suit
    winner_seat = plays[0][0]
    winner_card = plays[0][1]
    for seat, card in plays[1:]:
        if _beats(card, winner_card, led_suit, trump):
            winner_seat = seat
            winner_card = card
    return winner_seat


def _beats(card: Card, current: Card, led: str, trump: str | None) -> bool:
    card_trump = trump is not None and card.suit == trump
    cur_trump = trump is not None and current.suit == trump
    if card_trump and not cur_trump:
        return True
    if cur_trump and not card_trump:
        return False
    if card_trump and cur_trump:
        return RANK_VALUE[card.rank] > RANK_VALUE[current.rank]
    # Both non-trump
    if card.suit != led:
        return False
    if current.suit != led:
        return True
    return RANK_VALUE[card.rank] > RANK_VALUE[current.rank]


@dataclass
class Player:
    player_id: str
    name: str
    socket: "WebSocket"


@dataclass
class Table:
    players: list[Player] = field(default_factory=list)
    hands: list[list[Card]] = field(default_factory=list)

    phase: Phase = Phase.LOBBY
    dealer: int = 0

    # Bidding
    current_bidder: int | None = None
    passed: set[int] = field(default_factory=set)
    current_bid: Bid | None = None
    bid_history: list[dict] = field(default_factory=list)

    # Contract / partnership
    declarer: int | None = None
    contract: Bid | None = None
    partner_card: Card | None = None
    partner_seat: int | None = None

    # Play
    current_trick: list[tuple[int, Card]] = field(default_factory=list)
    tricks_won: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    turn: int | None = None
    trump_broken: bool = False

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ---- state ----

    def public_state(self) -> dict:
        state = {
            "players": [{"id": p.player_id, "name": p.name} for p in self.players],
            "capacity": MAX_PLAYERS,
            "phase": self.phase.value,
            "dealer": self.dealer,
            "current_bidder": self.current_bidder,
            "current_bid": self.current_bid.to_json() if self.current_bid else None,
            "bid_history": list(self.bid_history),
            "passed": sorted(self.passed),
            "declarer": self.declarer,
            "contract": self.contract.to_json() if self.contract else None,
            "partner_card": self.partner_card.to_json() if self.partner_card else None,
            "tricks_won": list(self.tricks_won),
            "turn": self.turn,
            "trump_broken": self.trump_broken,
        }
        # Partnership becomes public at end-of-hand per the variant spec.
        if self.phase == Phase.DONE:
            state["partner_seat"] = self.partner_seat
            state["result"] = self.compute_result()
        return state

    def compute_result(self) -> dict | None:
        if self.contract is None or self.declarer is None or self.partner_seat is None:
            return None
        target = self.contract.level + 6
        team_tricks = self.tricks_won[self.declarer] + self.tricks_won[self.partner_seat]
        delta = team_tricks - target
        return {
            "target": target,
            "team_tricks": team_tricks,
            "made": team_tricks >= target,
            "delta": delta,
        }

    # ---- transitions ----

    def deal(self) -> None:
        self.hands = deal_four()
        self.phase = Phase.BIDDING
        self.passed = set()
        self.current_bid = None
        self.bid_history = []
        self.current_bidder = self.dealer
        self.declarer = None
        self.contract = None
        self.partner_card = None
        self.partner_seat = None
        self.current_trick = []
        self.tricks_won = [0, 0, 0, 0]
        self.turn = None
        self.trump_broken = False
        # Auto-pass any empty seats before the dealer slot would even start.
        # If the dealer's seat itself is empty, that's a misconfiguration —
        # callers should ensure dealer is a real seat. We don't auto-pass the
        # dealer because the dealer is forced to open.

    def reset(self) -> None:
        self.hands = []
        self.phase = Phase.LOBBY
        self.passed = set()
        self.current_bid = None
        self.bid_history = []
        self.current_bidder = None
        self.declarer = None
        self.contract = None
        self.partner_card = None
        self.partner_seat = None
        self.current_trick = []
        self.tricks_won = [0, 0, 0, 0]
        self.turn = None
        self.trump_broken = False

    # ---- bidding ----

    def submit_pass(self, seat: int) -> str | None:
        if self.phase != Phase.BIDDING:
            return "not in bidding phase"
        if seat != self.current_bidder:
            return "not your turn"
        # No dealer-must-open rule — if all 4 pass with no bid, the dealer
        # gets "stuck" with 1NT in _end_bidding_if_done.
        self.passed.add(seat)
        self.bid_history.append({"kind": "pass", "seat": seat})
        self._advance_bidder()
        return None

    def submit_bid(self, seat: int, level: int, strain: str) -> str | None:
        if self.phase != Phase.BIDDING:
            return "not in bidding phase"
        if seat != self.current_bidder:
            return "not your turn"
        if strain not in STRAIN_RANK:
            return "invalid strain"
        if not (1 <= level <= 7):
            return "invalid level"
        new_bid = Bid(seat=seat, level=level, strain=strain)
        if self.current_bid is not None and new_bid.value <= self.current_bid.value:
            return "must outbid current"
        self.current_bid = new_bid
        self.bid_history.append({"kind": "bid", "seat": seat, "level": level, "strain": strain})
        self._advance_bidder()
        return None

    def _advance_bidder(self) -> None:
        # Auto-pass for empty seats so solo testing works. Empty = seat index
        # outside the players list. Loop until we land on a live human bidder
        # or bidding ends.
        while True:
            if self._end_bidding_if_done():
                return
            cur = self.current_bidder
            for _ in range(4):
                cur = (cur + 1) % 4
                if cur not in self.passed:
                    self.current_bidder = cur
                    break
            else:
                return  # all seats passed (shouldn't happen given dealer-open rule)
            # If this seat has no player, auto-pass it
            if self.current_bidder >= len(self.players):
                self.passed.add(self.current_bidder)
                self.bid_history.append({
                    "kind": "pass", "seat": self.current_bidder, "auto": True,
                })
                continue
            return  # human bidder's turn

    def _end_bidding_if_done(self) -> bool:
        # Normal end: 3 players have passed AND someone made a bid. The single
        # remaining live seat wins with the current bid.
        if len(self.passed) >= 3 and self.current_bid is not None:
            live = [s for s in range(4) if s not in self.passed]
            if len(live) == 1:
                self.declarer = live[0]
                self.contract = self.current_bid
                self.phase = Phase.CALLING
                self.current_bidder = None
                return True
        # Stick-the-dealer: all 4 passed without any bid → dealer forced
        # into the minimum contract (1NT, the least-priority strain).
        if len(self.passed) == 4 and self.current_bid is None:
            self.declarer = self.dealer
            self.contract = Bid(seat=self.dealer, level=1, strain="NT")
            self.bid_history.append({
                "kind": "stuck", "seat": self.dealer, "level": 1, "strain": "NT",
            })
            self.phase = Phase.CALLING
            self.current_bidder = None
            return True
        return False

    # ---- calling ----

    def submit_call_partner(self, seat: int, rank: str, suit: str) -> str | None:
        if self.phase != Phase.CALLING:
            return "not in calling phase"
        if seat != self.declarer:
            return "only declarer can call"
        if rank not in RANK_VALUE or suit not in {"S", "H", "D", "C"}:
            return "invalid card"
        # Can't call a card you already hold
        if any(c.rank == rank and c.suit == suit for c in self.hands[seat]):
            return "you hold that card"
        # Find the holder
        holder = None
        for s, hand in enumerate(self.hands):
            if any(c.rank == rank and c.suit == suit for c in hand):
                holder = s
                break
        if holder is None:
            return "no one holds that card"
        self.partner_card = Card(rank, suit)
        self.partner_seat = holder
        self.phase = Phase.PLAYING
        # Opening lead: NT → right of declarer; else declarer leads
        if self.contract is not None and self.contract.strain == "NT":
            self.turn = (self.declarer - 1) % 4
        else:
            self.turn = self.declarer
        return None

    # ---- play ----

    def play_card(self, seat: int, rank: str, suit: str) -> tuple[Card | None, str | None]:
        """Attempt to play a card. Returns (card, None) on success or
        (None, error_string) on rule violation."""
        if self.phase != Phase.PLAYING:
            return None, "not in playing phase"
        if seat != self.turn:
            return None, "not your turn"
        if seat < 0 or seat >= len(self.hands):
            return None, "invalid seat"
        hand = self.hands[seat]
        target = None
        for c in hand:
            if c.rank == rank and c.suit == suit:
                target = c
                break
        if target is None:
            return None, "you don't hold that card"

        trump = self.contract.strain if self.contract and self.contract.strain != "NT" else None

        if not self.current_trick:
            # Leading: can't lead trump until it's been broken, unless your
            # hand contains nothing but trump.
            if trump is not None and target.suit == trump and not self.trump_broken:
                if any(c.suit != trump for c in hand):
                    return None, "trump not broken — can't lead trump yet"
        else:
            # Following: must play led suit if you have any.
            led_suit = self.current_trick[0][1].suit
            if target.suit != led_suit and any(c.suit == led_suit for c in hand):
                suit_name = {"S": "spades", "H": "hearts", "D": "diamonds", "C": "clubs"}[led_suit]
                return None, f"must follow {suit_name}"

        # Legal — commit
        hand.remove(target)
        self.current_trick.append((seat, target))
        # Trump break detection: trump played on a non-trump-led trick.
        if trump is not None and len(self.current_trick) > 1:
            led_suit = self.current_trick[0][1].suit
            if led_suit != trump and target.suit == trump:
                self.trump_broken = True
        # Advance turn (overwritten by resolve_trick_if_complete when 4th lands)
        if len(self.current_trick) < 4:
            self.turn = (seat + 1) % 4
        return target, None

    def resolve_trick_if_complete(self) -> int | None:
        if len(self.current_trick) != 4:
            return None
        trump = self.contract.strain if self.contract and self.contract.strain != "NT" else None
        winner = evaluate_trick(self.current_trick, trump)
        self.tricks_won[winner] += 1
        self.current_trick = []
        self.turn = winner
        if sum(self.tricks_won) >= 13:
            self.phase = Phase.DONE
            self.turn = None
        return winner

    # ---- player management ----

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
