"""Math ERS room state + latency-fair slap resolution.

The only thing that's genuinely hard here is deciding who slapped first when
players sit behind different network latencies. See SLAP_WINDOW_S and
record_slap() for how that's handled.
"""
from __future__ import annotations

import asyncio
import random
import string
from dataclasses import dataclass, field
from math import isqrt

# Cards are stored as ranks 1..13. Their MATH VALUE differs (David's variant):
# Ace=1, J=12, Q=11, K=13, rest face. J/Q swapped vs convention on purpose: 11
# triggers too few patterns, so the easier-to-hit value sits on the commoner card.
RANKS = list(range(1, 14))

# Longest run of recent cards a concatenation (square/cube) check will scan.
# ponytail: nobody tracks more than ~4 cards in their head; bump if wanted.
CONCAT_MAX = 4


def value(rank: int) -> int:
    return {11: 12, 12: 11}.get(rank, rank)  # J=12, Q=11 (swapped); rest = rank

# After the first valid slap lands, the server waits this long before awarding
# the pile, collecting every other slap that arrives in the meantime. The
# winner is the one with the lowest *reaction time* (measured on each client as
# slap-moment minus card-paint-moment), NOT the first packet to hit the server.
# 200ms comfortably exceeds the latency spread between two home connections, so
# a slower-pinged player's slap still lands inside the window to be ranked.
SLAP_WINDOW_S = 0.20

# Flip shot clock: you must play a card within this long on your turn, or the
# server auto-flips for you. Stops players tanking to scan the pile. Off in solo.
SHOT_CLOCK_S = 2.0

# Floor on a believable human reaction. Anything faster is an anticipatory mash
# or a tampered client — ignored (no win, no penalty). ponytail: this is the
# only anti-cheat; client reaction times are otherwise trusted, fine for
# friends. Upgrade path if it ever matters: server RTT-compensated timing.
MIN_HUMAN_S = 0.08


def make_deck() -> list[int]:
    return [r for r in RANKS for _ in range(4)]


def _is_square(n: int) -> bool:
    if n < 10:  # squares must be 2+ digits (David's rule)
        return False
    r = isqrt(n)
    return r * r == n


def _is_cube(n: int) -> bool:
    if n < 10:
        return False
    r = round(n ** (1 / 3))
    return any(c >= 0 and c ** 3 == n for c in (r - 1, r, r + 1))


# All slap rules, in label-priority order. A room enables a subset (see settings).
ALL_RULES = ("arithmetic", "geometric", "fibonacci", "square", "cube")


def matching_rules(pile: list[int], enabled=ALL_RULES) -> list[str]:
    """Every enabled Math-ERS rule the top of the pile satisfies (priority order).

    Single source of truth. All checks look at the most recent cards (the
    just-played card must complete the pattern). To add a rule, add a check here.
    """
    out: list[str] = []
    if len(pile) < 2:
        return out
    v = [value(r) for r in pile]

    # Arithmetic / geometric: exactly the top 3 cards. Constant runs count.
    if len(pile) >= 3:
        a, b, c = v[-3], v[-2], v[-1]
        if "arithmetic" in enabled and 2 * b == a + c:     # (d may be 0)
            out.append("arithmetic")
        if "geometric" in enabled and b * b == a * c:      # (ratio may be 1)
            out.append("geometric")

    # Fibonacci: a 4-term Fibonacci run (mod 12) — each card is the previous two
    # summed. Needs the top 4 cards, so you can't pre-load from just the last 2.
    if "fibonacci" in enabled and len(pile) >= 4:
        a, b, c, d = v[-4:]
        if (a + b) % 12 == c % 12 and (b + c) % 12 == d % 12:
            out.append("fibonacci")

    # Squares / cubes: concatenate the digits of any top-window of >= 2 cards.
    if "square" in enabled or "cube" in enabled:
        sq = cu = False
        for k in range(2, min(CONCAT_MAX, len(pile)) + 1):
            num = int("".join(str(x) for x in v[-k:]))
            sq = sq or _is_square(num)
            cu = cu or _is_cube(num)
        if sq and "square" in enabled:
            out.append("square")
        if cu and "cube" in enabled:
            out.append("cube")
    return out


def slap_rule(pile: list[int], enabled=ALL_RULES) -> str | None:
    rules = matching_rules(pile, enabled)
    return rules[0] if rules else None


def is_slappable(pile: list[int], enabled=ALL_RULES) -> bool:
    return bool(matching_rules(pile, enabled))


@dataclass(eq=False)  # identity hash/eq — players are tracked by object, not value
class Player:
    name: str
    socket: object
    stack: list[int] = field(default_factory=list)  # face-down; draw from end
    connected: bool = True


@dataclass
class Room:
    code: str
    players: list[Player] = field(default_factory=list)
    pile: list[int] = field(default_factory=list)
    turn: int = 0
    started: bool = False
    solo: bool = False  # practice room: 1 player, no win condition, re-deal freely
    rules: set = field(default_factory=lambda: set(ALL_RULES))  # enabled slap rules
    # "reflex": rank slaps by client reaction time (ping-independent, trusts
    # client). "ping": rank by server arrival (first packet wins, no trust).
    mode: str = "reflex"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Slap-window state, valid only while a slappable pile is live.
    slaps: dict[Player, float] = field(default_factory=dict)  # player -> reaction (s)
    locked_out: set = field(default_factory=set)  # wrong-slapped this pile
    window_open: bool = False
    pending_rule: str | None = None  # which rule the live slap window is on
    clock_task: object = field(default=None, repr=False, compare=False)  # shot-clock timer

    # --- setup ---------------------------------------------------------
    def add(self, name: str, socket) -> Player:
        p = Player(name=name, socket=socket)
        self.players.append(p)
        return p

    def seat_of(self, player: Player) -> int | None:
        try:
            return self.players.index(player)
        except ValueError:
            return None

    def deal(self) -> None:
        deck = make_deck()
        random.shuffle(deck)
        for p in self.players:
            p.stack = []
        for i, card in enumerate(deck):
            self.players[i % len(self.players)].stack.append(card)
        self.pile = []
        self.turn = 0
        self.started = True
        self._reset_slaps()

    # --- play ----------------------------------------------------------
    def _reset_slaps(self) -> None:
        self.slaps.clear()
        self.locked_out.clear()
        self.window_open = False
        self.pending_rule = None

    def _advance_turn(self) -> None:
        # Skip players who are out of cards (they can still slap back in).
        for _ in range(len(self.players)):
            self.turn = (self.turn + 1) % len(self.players)
            if self.players[self.turn].stack:
                return

    def flip(self, player: Player) -> tuple[int | None, str | None]:
        if not self.started:
            return None, "not started"
        seat = self.seat_of(player)
        if seat != self.turn:
            return None, "not your turn"
        if not player.stack:
            return None, "no cards"
        card = player.stack.pop()
        self.pile.append(card)
        self._advance_turn()
        # New card on top → fresh slap chance for everyone.
        self._reset_slaps()
        return card, None

    def record_slap(self, player: Player, reaction: float | None, arrival: float) -> str:
        """Register a slap. Returns 'wrong' | 'open' | 'add' | 'ignore'.

        Ranking key depends on self.mode: "reflex" uses the client-measured
        reaction (seconds from card-paint to slap, latency-free but trusted);
        "ping" uses the server `arrival` monotonic time (first packet wins).
        Lower wins either way. 'open' means this slap started the resolution
        window (caller schedules resolve() after SLAP_WINDOW_S).
        """
        if player in self.locked_out:
            return "ignore"
        if not is_slappable(self.pile, self.rules):
            # Penalty: burn one card to the bottom of the pile, lock out.
            self.locked_out.add(player)
            if player.stack:
                self.pile.insert(0, player.stack.pop())
            return "wrong"
        if self.mode == "reflex":
            if reaction is None or reaction < MIN_HUMAN_S:
                return "ignore"  # no card seen, or superhuman/tampered
            key = reaction
        else:
            key = arrival
        if player in self.slaps:
            return "ignore"
        self.slaps[player] = key
        if not self.window_open:
            self.window_open = True
            self.pending_rule = slap_rule(self.pile, self.rules)
            return "open"
        return "add"

    def resolve_slaps(self) -> Player | None:
        """Award the live pile to the earliest latency-compensated slapper."""
        if not self.slaps:
            self.window_open = False
            return None
        winner = min(self.slaps, key=self.slaps.get)
        # Pile goes to the bottom of the winner's stack (so it gets played out).
        winner.stack[:0] = self.pile
        self.pile = []
        # Winner leads the next flip.
        self.turn = self.seat_of(winner)
        self._reset_slaps()
        return winner

    def winner(self) -> Player | None:
        """Whole-deck winner, or None if the game is still going."""
        if not self.started or self.solo:  # solo practice never "ends"
            return None
        alive = [p for p in self.players if p.stack]
        return alive[0] if len(alive) == 1 else None

    # --- views ---------------------------------------------------------
    def public_state(self) -> dict:
        return {
            "code": self.code,
            "started": self.started,
            "mode": self.mode,
            "rules": sorted(self.rules),
            "all_rules": list(ALL_RULES),
            "turn": self.turn,
            "pile_count": len(self.pile),
            "pile_top": self.pile[-1] if self.pile else None,
            # No "slappable" flag on purpose — spotting the pattern is the game.
            "players": [
                {"name": p.name, "cards": len(p.stack), "connected": p.connected}
                for p in self.players
            ],
        }


ROOMS: dict[str, Room] = {}


def make_room() -> Room:
    while True:
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in ROOMS:
            room = Room(code=code)
            ROOMS[code] = room
            return room


def demo() -> None:
    # Each of David's five rules (ranks; values: A=1/14, J=12, Q=11, K=13).
    assert slap_rule([1, 6]) == "square"        # "1"+"6" = 16 = 4²
    assert slap_rule([2, 7]) == "cube"          # "2"+"7" = 27 = 3³
    assert slap_rule([2, 4, 6]) == "arithmetic" # d=2
    assert slap_rule([5, 5, 5]) == "arithmetic" # constant run counts (d=0)
    assert slap_rule([2, 4, 8]) == "geometric"  # ratio 2
    assert slap_rule([1, 2, 3, 5]) == "fibonacci"  # 4-term run: 1,2,3,5
    assert slap_rule([5, 8, 1, 9]) == "fibonacci"  # 4-term, mod 12 wrap: 5+8≡1, 8+1≡9
    assert slap_rule([2, 3, 5]) is None            # fibonacci now needs 4 cards
    assert slap_rule([10, 4, 1]) is None           # ace is only 1 now, nothing fires
    # rule toggles: disabling a rule makes its piles non-slappable
    assert slap_rule([1, 2, 3, 5], enabled={"square"}) is None
    assert slap_rule([1, 6], enabled={"arithmetic"}) is None   # square off → not slappable
    assert slap_rule([11, 1]) == "square"        # J(rank11)=12 + ace=1 → "121" = 11²
    assert slap_rule([12, 1]) is None            # Q(rank12)=11 + ace → "111"/"1114", neither
    assert slap_rule([2, 3]) is None             # "23": not square/cube, <3 for seq
    assert slap_rule([4]) is None                # single card never slappable
    assert is_slappable([1, 6]) and not is_slappable([2, 3])

    # reflex mode: lowest reaction wins even though b's packet arrived later.
    r = Room(code="TEST", mode="reflex")
    r.players = [Player("a", None), Player("b", None)]
    r.pile = [1, 6]  # slappable: "16" = 4²
    assert r.record_slap(r.players[0], reaction=0.30, arrival=1.00) == "open"
    assert r.record_slap(r.players[1], reaction=0.22, arrival=1.05) == "add"
    assert r.resolve_slaps() is r.players[1], "reflex: fastest reaction wins"
    # Superhuman / no-card slaps don't count and don't penalize.
    r.pile = [1, 6]
    assert r.record_slap(r.players[0], reaction=0.02, arrival=2.0) == "ignore"
    assert r.record_slap(r.players[0], reaction=None, arrival=2.0) == "ignore"

    # ping mode: earliest server arrival wins, reaction ignored.
    rp = Room(code="PING", mode="ping")
    rp.players = [Player("a", None), Player("b", None)]
    rp.pile = [1, 6]
    assert rp.record_slap(rp.players[0], reaction=0.30, arrival=1.00) == "open"
    assert rp.record_slap(rp.players[1], reaction=0.22, arrival=1.05) == "add"
    assert rp.resolve_slaps() is rp.players[0], "ping: earliest packet wins"

    # Wrong slap burns a card (any mode).
    r2 = Room(code="T2")
    r2.players = [Player("a", None, stack=[5, 5])]
    r2.pile = [2, 3]  # not slappable
    assert r2.record_slap(r2.players[0], reaction=0.30, arrival=1.0) == "wrong"
    assert r2.pile == [5, 2, 3] and r2.players[0].stack == [5]
    print("ok")


if __name__ == "__main__":
    demo()
