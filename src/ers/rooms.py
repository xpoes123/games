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

# A wrong slap landing within this long of your previous slap is forgiven (no
# burn) — stops an accidental double-tap right after a real slap from costing you.
SLAP_DEBOUNCE_S = 0.4

# CPU difficulty: reaction = slap/flip delay (s); coverage = how many of the 5
# patterns it checks per pile (random subset — misses the rest); max_digits =
# largest square/cube concatenation it can spot. insane ≈ perfect.
CPU_LEVELS = {
    "easy":   {"reaction": 1.4,  "coverage": 2, "max_digits": 2},
    "medium": {"reaction": 0.8,  "coverage": 3, "max_digits": 3},
    "hard":   {"reaction": 0.5,  "coverage": 4, "max_digits": 4},
    "insane": {"reaction": 0.28, "coverage": 5, "max_digits": 99},
}

# Face-card battle (classic ERS, optional): playing a face card forces the next
# player to answer with their own face card within N flips, else the player who
# laid it takes the pile. Chances by RANK: Jack(11)=1, Queen(12)=2, King(13)=3,
# Ace(1)=4 (i.e. shown values 12→1, 11→2, 13→3, 1→4).
FACE_CHANCES = {11: 1, 12: 2, 13: 3, 1: 4}

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
DEFAULT_SPANS = {"arithmetic": 3, "geometric": 3, "fibonacci": 3, "square": 2, "cube": 2}


def min_span(rule: str) -> int:
    return 3 if rule in SEQ_RULES else 2


def _arith(vs: list[int]) -> bool:  # constant difference across the whole run
    return all(2 * vs[i] == vs[i - 1] + vs[i + 1] for i in range(1, len(vs) - 1))


def _geom(vs: list[int]) -> bool:   # constant ratio (every consecutive triple)
    return all(vs[i] * vs[i] == vs[i - 1] * vs[i + 1] for i in range(1, len(vs) - 1))


def _fib(vs: list[int]) -> bool:    # each card = previous two summed, mod 12
    return all((vs[i - 2] + vs[i - 1]) % 12 == vs[i] % 12 for i in range(2, len(vs)))


def matching_rules(pile: list[int], spans: dict = DEFAULT_SPANS,
                   max_digits: int | None = None) -> list[str]:
    """Every rule the top of the pile satisfies, given each rule's span (min
    cards). max_digits caps the size of square/cube concatenations considered
    (used to model a weaker CPU). Single source of truth — add rules here."""
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
                s = "".join(str(x) for x in v[-k:])
                if max_digits is not None and len(s) > max_digits:
                    continue
                if test(int(s)):
                    out.append(name)
                    break
    return out


def slap_rule(pile: list[int], spans: dict = DEFAULT_SPANS) -> str | None:
    rules = matching_rules(pile, spans)
    return rules[0] if rules else None


def is_slappable(pile: list[int], spans: dict = DEFAULT_SPANS,
                 max_digits: int | None = None) -> bool:
    return bool(matching_rules(pile, spans, max_digits))


@dataclass(eq=False)  # identity hash/eq — players are tracked by object, not value
class Player:
    name: str
    socket: object
    stack: list[int] = field(default_factory=list)  # face-down; draw from end
    connected: bool = True
    is_cpu: bool = False
    last_slap: float = 0.0  # monotonic time of this player's last slap (debounce)


@dataclass
class Room:
    code: str
    players: list[Player] = field(default_factory=list)
    pile: list[int] = field(default_factory=list)
    turn: int = 0
    started: bool = False
    solo: bool = False  # practice room: 1 human vs a CPU, re-deal freely
    cpu_level: str = "medium"   # difficulty preset (see CPU_LEVELS)
    cpu_reaction: float = 0.8   # slap/flip delay, derived from cpu_level
    cpu_coverage: int = 3       # patterns the CPU checks per pile
    cpu_max_digits: int = 3     # largest square/cube the CPU can spot
    battle_enabled: bool = False        # face-card battle rule (off by default)
    battle_owner: int | None = None     # seat owed the pile if a challenge fails
    battle_chances: int = 0             # responder's flips left (0 = no battle)
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
    clock_task: object = field(default=None, repr=False, compare=False)   # shot-clock timer
    cpu_flip_task: object = field(default=None, repr=False, compare=False)
    cpu_slap_task: object = field(default=None, repr=False, compare=False)

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
        self.battle_owner = None
        self.battle_chances = 0
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

    def flip(self, player: Player) -> tuple[int | None, str | None, tuple | None]:
        """Flip the top card. Returns (card, error, event). event is None, or
        ("battle_won", owner) when a face-card challenge went unanswered."""
        if not self.started:
            return None, "not started", None
        seat = self.seat_of(player)
        if seat != self.turn:
            return None, "not your turn", None
        if not player.stack:
            return None, "no cards", None
        card = player.stack.pop()
        self.pile.append(card)
        if self.battle_enabled:
            event = self._apply_battle(seat, card)  # handles its own turn logic
        else:
            event = None
            self._advance_turn()
        # New card on top → fresh slap chance for everyone.
        self._reset_slaps()
        return card, None, event

    def _apply_battle(self, seat: int, card: int) -> tuple | None:
        is_face = card in FACE_CHANCES
        if self.battle_chances > 0:
            # `player` is answering a challenge.
            if is_face:                              # reversal: re-challenge opponent
                self.battle_owner = seat
                self.battle_chances = FACE_CHANCES[card]
                self._advance_turn()
            else:
                self.battle_chances -= 1
                if self.battle_chances == 0:         # challenge failed → owner scoops
                    owner = self.players[self.battle_owner]
                    owner.stack[:0] = self.pile
                    self.pile = []
                    self.turn = self.battle_owner
                    self.battle_owner = None
                    return ("battle_won", owner)
                # else: responder keeps flipping — turn stays put
        elif is_face:                                # a face card starts a battle
            self.battle_owner = seat
            self.battle_chances = FACE_CHANCES[card]
            self._advance_turn()
        else:
            self._advance_turn()                     # normal flip
        return None

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
        recent = (arrival - player.last_slap) < SLAP_DEBOUNCE_S
        player.last_slap = arrival
        if not is_slappable(self.pile, self.rule_spans):
            if recent:
                return "ignore"  # debounce: accidental double-tap right after a slap
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
        # Winner leads the next flip; a slap also ends any face-card battle.
        self.turn = self.seat_of(winner)
        self.battle_owner = None
        self.battle_chances = 0
        self._reset_slaps()
        return winner

    def cpu_sees(self, pile: list[int]) -> bool:
        """Whether the CPU spots a slappable pattern: it only checks a random
        subset (cpu_coverage) of the enabled rules each pile, and can't read
        squares/cubes bigger than cpu_max_digits — so a weaker CPU misses."""
        enabled = [r for r in ALL_RULES if self.rule_spans.get(r, 0) >= min_span(r)]
        if not enabled:
            return False
        subset = random.sample(enabled, min(self.cpu_coverage, len(enabled)))
        sub_spans = {r: self.rule_spans[r] for r in subset}
        return is_slappable(pile, sub_spans, max_digits=self.cpu_max_digits)

    def winner(self) -> Player | None:
        """Whole-deck winner, or None if the game is still going."""
        if not self.started or len(self.players) < 2:
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
            "solo": self.solo,
            "cpu": any(p.is_cpu for p in self.players),
            "cpu_level": self.cpu_level,
            "battle_enabled": self.battle_enabled,
            "battle": (
                {"responder": self.turn, "owner": self.battle_owner, "chances": self.battle_chances}
                if self.battle_chances > 0 else None
            ),
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
    assert slap_rule([2, 3, 5]) == "fibonacci"     # default fib span 3: 2+3=5
    assert slap_rule([5, 8, 1, 9]) == "fibonacci"  # mod 12 wrap on top 3: 8+1≡9
    assert slap_rule([10, 4, 1]) is None           # ace is only 1 now, nothing fires
    assert slap_rule([2, 3, 5], {"fibonacci": 4}) is None  # crank fib to 4 → too short

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
    # ...but a second wrong slap right after (within debounce) is forgiven.
    assert r2.record_slap(r2.players[0], reaction=0.30, arrival=1.1) == "ignore"
    assert r2.players[0].stack == [5]  # no extra card burned

    # Face-card battle: Jack(rank 11)=1 chance; unanswered → owner takes pile.
    rb = Room(code="B", battle_enabled=True, started=True)
    rb.players = [Player("a", None, stack=[11]), Player("b", None, stack=[5])]
    card, err, ev = rb.flip(rb.players[0])         # a plays Jack
    assert card == 11 and rb.battle_chances == 1 and rb.battle_owner == 0 and rb.turn == 1
    card, err, ev = rb.flip(rb.players[1])         # b answers with a 5 → fails
    assert ev == ("battle_won", rb.players[0])
    assert rb.players[0].stack == [11, 5] and rb.pile == [] and rb.battle_chances == 0

    # Reversal: responder plays a face card → re-challenges with its own count.
    rr = Room(code="BR", battle_enabled=True, started=True)
    rr.players = [Player("a", None, stack=[3, 11]), Player("b", None, stack=[7, 12])]
    rr.flip(rr.players[0])                          # Jack (top) → chances 1, owner 0, turn 1
    _, _, ev = rr.flip(rr.players[1])              # Queen (top) =2 → reversal back to a
    assert rr.battle_owner == 1 and rr.battle_chances == 2 and rr.turn == 0 and ev is None

    # Battle off (default): a face card is just a normal flip.
    rn = Room(code="BN", started=True)
    rn.players = [Player("a", None, stack=[11]), Player("b", None, stack=[5])]
    _, _, ev = rn.flip(rn.players[0])
    assert rn.battle_chances == 0 and rn.turn == 1 and ev is None

    # Game ends when a player runs out of cards.
    rw = Room(code="W", started=True)
    rw.players = [Player("a", None, stack=[2, 3]), Player("b", None, stack=[])]
    assert rw.winner() is rw.players[0]

    # CPU perception: insane sees all; easy can't read a 3-digit-only square.
    ri = Room(code="I", cpu_coverage=5, cpu_max_digits=99)
    assert ri.cpu_sees([1, 6])                 # "16" = 4², insane sees everything
    re_ = Room(code="E", cpu_coverage=2, cpu_max_digits=2)
    assert is_slappable([1, 2, 1])             # "121" = 11² really is slappable
    assert not re_.cpu_sees([1, 2, 1])         # ...but 3 digits → invisible to easy
    print("ok")


if __name__ == "__main__":
    demo()
