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

# Most cards any single pattern is allowed to span (the difficulty dial maxes here).
MAX_SPAN = 6


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


# All slap rules, in label-priority order.
ALL_RULES = ("arithmetic", "geometric", "fibonacci", "square", "cube")
# Sequence rules span >= 3 cards; concat rules (square/cube) span >= 2.
SEQ_RULES = ("arithmetic", "geometric", "fibonacci")

# Per-rule "span" = minimum number of cards the pattern must cover (the difficulty
# dial). 0 = rule off. Default = the current balance.
DEFAULT_SPANS = {"arithmetic": 3, "geometric": 3, "fibonacci": 4, "square": 2, "cube": 2}


def min_span(rule: str) -> int:
    return 3 if rule in SEQ_RULES else 2


def _arith(vs: list[int]) -> bool:  # constant difference across the whole run
    return all(2 * vs[i] == vs[i - 1] + vs[i + 1] for i in range(1, len(vs) - 1))


def _geom(vs: list[int]) -> bool:   # constant ratio (every consecutive triple)
    return all(vs[i] * vs[i] == vs[i - 1] * vs[i + 1] for i in range(1, len(vs) - 1))


def _fib(vs: list[int]) -> bool:    # each card = previous two summed, mod 12
    return all((vs[i - 2] + vs[i - 1]) % 12 == vs[i] % 12 for i in range(2, len(vs)))


def matching_rules(pile: list[int], spans: dict = DEFAULT_SPANS) -> list[str]:
    """Every rule the top of the pile satisfies, given each rule's span (min
    cards). Single source of truth. To add a rule, add a check here."""
    out: list[str] = []
    if len(pile) < 2:
        return out
    v = [value(r) for r in pile]

    # Sequences: the top N cards form the pattern, where N is the rule's span.
    for name, ok in (("arithmetic", _arith), ("geometric", _geom), ("fibonacci", _fib)):
        n = spans.get(name, 0)
        if n >= 3 and len(v) >= n and ok(v[-n:]):
            out.append(name)

    # Squares / cubes: concatenate the digits of any top window of >= N cards.
    for name, test in (("square", _is_square), ("cube", _is_cube)):
        n = spans.get(name, 0)
        if n >= 2:
            for k in range(n, min(MAX_SPAN, len(v)) + 1):
                if test(int("".join(str(x) for x in v[-k:]))):
                    out.append(name)
                    break
    return out


def slap_rule(pile: list[int], spans: dict = DEFAULT_SPANS) -> str | None:
    rules = matching_rules(pile, spans)
    return rules[0] if rules else None


def is_slappable(pile: list[int], spans: dict = DEFAULT_SPANS) -> bool:
    return bool(matching_rules(pile, spans))


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
    # Per-rule difficulty: min cards each pattern must span (0 = off). See settings.
    rule_spans: dict = field(default_factory=lambda: dict(DEFAULT_SPANS))
    shot_clock: float = SHOT_CLOCK_S  # seconds to flip before auto-flip; 0 = off
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
        if not is_slappable(self.pile, self.rule_spans):
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
            self.pending_rule = slap_rule(self.pile, self.rule_spans)
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
            "all_rules": list(ALL_RULES),
            "spans": {r: self.rule_spans.get(r, 0) for r in ALL_RULES},
            "max_span": MAX_SPAN,
            "shot_clock": self.shot_clock,
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
    assert slap_rule([1, 2, 3, 5]) == "fibonacci"  # default fib span 4: 1,2,3,5
    assert slap_rule([5, 8, 1, 9]) == "fibonacci"  # 4-term, mod 12 wrap: 5+8≡1, 8+1≡9
    assert slap_rule([2, 3, 5]) is None            # default fib needs 4 cards
    assert slap_rule([10, 4, 1]) is None           # ace is only 1 now, nothing fires

    # difficulty dial (per-rule span / min cards):
    assert slap_rule([2, 3, 5], {"fibonacci": 3}) == "fibonacci"   # 3-term fib
    assert slap_rule([2, 4, 6], {"arithmetic": 4}) is None         # needs top 4
    assert slap_rule([2, 4, 6, 8], {"arithmetic": 4}) == "arithmetic"
    assert slap_rule([1, 6], {"square": 3}) is None                # "16" too short now
    assert slap_rule([1, 2, 1], {"square": 3}) == "square"         # "121" = 11², 3 cards
    assert slap_rule([1, 2, 3, 5], {"square": 2}) is None          # fib off → not slappable
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
