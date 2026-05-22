"""Rooms, players, turn machine, message dispatch for Hearthstone Chess.

The WS layer (app.py) translates protocol messages into Room method calls
and broadcasts the resulting events + state. The Room is the source of
truth: it owns the board, both players' hands/decks, the turn machine, and
the pending-card-play state.
"""
from __future__ import annotations

import asyncio
import logging
import random
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from src.chess.board import (
    Board,
    Move,
    Piece,
    back_two_ranks,
    pawn_placement_ranks,
    placement_zone_for,
    opponent_2nd_rank,
    sq_to_fr,
    fr_to_sq,
    CENTRAL_4,
)
from src.chess.cards import CARDS_BY_ID, Card, CardDef
from src.chess.deck import build_deck, draw
from src.chess.effects import MATERIAL_POINTS, cost_for, resolve
from src.chess.prompts import (
    PendingPlay,
    Prompt,
    build_choose_opp_move_prompt,
    build_modal_prompt,
    new_prompt_id,
    build_mulligan_prompt,
    build_target_prompt,
)

log = logging.getLogger("chess")

HAND_CAP = 10
GOLD_CAP_MAX = 10
TURN_BUDGET_MS = 90_000
MAX_LOG = 30
MAX_MOVES_PER_TURN = 4


class Phase(str, Enum):
    LOBBY = "LOBBY"
    MULLIGAN = "MULLIGAN"
    PLAYING = "PLAYING"
    DONE = "DONE"


Seat = Literal["white", "black", "spectator"]


@dataclass
class Player:
    pid: str
    name: str
    seat: Seat
    ws: Any = None
    hand: list[Card] = field(default_factory=list)
    deck: list[Card] = field(default_factory=list)
    gold: int = 0
    gold_cap: int = 0
    moves_remaining: int = 0
    pieces_free_this_turn: bool = False
    no_chess_move_this_turn: bool = False
    cannot_capture_king_this_turn: bool = False
    modular_board_this_turn: bool = False
    pawn_two_moves_armed: bool = False
    pawn_back_pawn_sq: str | None = None
    extra_pawn_squares: dict[str, int] = field(default_factory=dict)
    spell_tax: int = 0
    spell_tax_next_turn: int = 0
    extra_turn_queued: bool = False
    extra_turn_no_capture_king: bool = False
    extra_turn_gold_cap: int = 0  # bonus turn's gold cap (10g Extra Turn = 5)
    opp_moves_chosen_by_me_next_turn: bool = False
    mulligan_done: bool = False
    has_acted_this_turn: bool = False  # made a chess move OR a counts-as-move card
    triple_move_active: bool = False
    triple_move_used: set = field(default_factory=set)
    # Echo grants a free immediate cast of the stolen card, identified by
    # instance_id (slot indices shift as cards are played). Consumed on the
    # matching play_card, cleared at end-of-turn.
    echo_free_instance_id: str | None = None

    def hand_to_json(self, can_afford_fn) -> list[dict]:
        return [c.to_json(slot=i, playable=can_afford_fn(c)) for i, c in enumerate(self.hand)]

    def reset_turn_flags(self) -> None:
        self.moves_remaining = 0
        self.pieces_free_this_turn = False
        self.no_chess_move_this_turn = False
        self.cannot_capture_king_this_turn = False
        self.modular_board_this_turn = False
        self.pawn_two_moves_armed = False
        self.pawn_back_pawn_sq = None
        self.extra_pawn_squares.clear()
        self.opp_moves_chosen_by_me_next_turn = False
        self.has_acted_this_turn = False
        self.triple_move_active = False
        self.triple_move_used.clear()
        self.echo_free_instance_id = None


@dataclass
class LogEntry:
    ts: int
    text: str

    def to_json(self) -> dict:
        return {"ts": self.ts, "text": self.text}


class Room:
    def __init__(self, code: str, rng: random.Random | None = None) -> None:
        self.code = code
        self.rng = rng or random.Random()
        self.players: dict[str, Player] = {}      # pid -> Player
        self.seats: dict[str, str] = {}           # "white"/"black" -> pid
        self.phase: Phase = Phase.LOBBY
        self.board: Board = Board.starting()
        self.active_seat: Seat = "white"
        self.turn_number: int = 1
        self.pending_prompts: dict[str, Prompt] = {}
        self.pending_card_play: PendingPlay | None = None
        self.log: deque[LogEntry] = deque(maxlen=MAX_LOG)
        self.turn_started_ms: int = 0
        self.winner: str | None = None
        self.win_reason: str | None = None  # "king_capture" | "concede"
        self.extra_turn_pending: bool = False
        self.annotations: dict[str, set[str]] = {"white": set(), "black": set()}
        # Snapshot of state right before the most recent chess move on the
        # current turn, used by undo_move. Cleared on turn change or any
        # card play.
        self.undo_snapshot: dict | None = None
        self.turn_timer_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()

    # ---- helpers ----

    def player_by_seat(self, seat: str) -> Player | None:
        pid = self.seats.get(seat)
        return self.players.get(pid) if pid else None

    def opponent_of(self, seat: str) -> Player | None:
        other = "black" if seat == "white" else "white"
        return self.player_by_seat(other)

    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def _log(self, text: str) -> None:
        self.log.append(LogEntry(ts=self.now_ms(), text=text))

    # ---- player join/leave ----

    def add_player(self, name: str, ws=None) -> Player:
        # Reconnect path: a disconnected player with the same name reclaims
        # their seat. Without this, refreshing a tab lands you as spectator
        # because the old pid still holds the seat (disconnect only nulls ws).
        for seat_key in ("white", "black"):
            pid = self.seats.get(seat_key)
            if pid is None:
                continue
            held = self.players.get(pid)
            if held is None:
                continue
            if held.ws is None and held.name == name:
                held.ws = ws
                return held
        # Otherwise: claim the first free seat, else spectate.
        pid = secrets.token_hex(8)
        if "white" not in self.seats or self.players.get(self.seats["white"]) is None:
            seat: Seat = "white"
            self.seats["white"] = pid
        elif "black" not in self.seats or self.players.get(self.seats["black"]) is None:
            seat = "black"
            self.seats["black"] = pid
        else:
            seat = "spectator"
        player = Player(pid=pid, name=name, seat=seat, ws=ws)
        self.players[pid] = player
        # If both seats just filled, deal the starting hands and enter MULLIGAN.
        if self.phase == Phase.LOBBY and "white" in self.seats and "black" in self.seats:
            self._begin_mulligan()
        return player

    def remove_player(self, pid: str) -> None:
        # Soft drop: keep the seat but null the ws. (Reconnect by name is a
        # Phase 3 concern.)
        p = self.players.get(pid)
        if p is None:
            return
        p.ws = None

    # ---- mulligan ----

    def _begin_mulligan(self) -> None:
        white = self.player_by_seat("white")
        black = self.player_by_seat("black")
        assert white and black
        white.deck = build_deck(self.rng)
        black.deck = build_deck(self.rng)
        white.hand = draw(white.deck, 3)
        black.hand = draw(black.deck, 4)
        self.phase = Phase.MULLIGAN
        self._log("game started — mulligan")

    def submit_mulligan(self, seat: str, redraw_slots: list[int]) -> str | None:
        if self.phase != Phase.MULLIGAN:
            return "not in mulligan phase"
        p = self.player_by_seat(seat)
        if p is None:
            return "no such seat"
        if p.mulligan_done:
            return "already mulliganed"
        n = len(redraw_slots)
        if any(s < 0 or s >= len(p.hand) for s in redraw_slots):
            return "bad slot"
        # Put redraws back, shuffle, draw the same count.
        returned = [p.hand[s] for s in redraw_slots]
        p.hand = [c for i, c in enumerate(p.hand) if i not in set(redraw_slots)]
        p.deck.extend(returned)
        self.rng.shuffle(p.deck)
        p.hand.extend(draw(p.deck, n))
        p.mulligan_done = True
        white = self.player_by_seat("white")
        black = self.player_by_seat("black")
        if white and black and white.mulligan_done and black.mulligan_done:
            self._begin_playing()
        return None

    def _begin_playing(self) -> None:
        self.phase = Phase.PLAYING
        self.active_seat = "white"
        self.turn_number = 1
        # White's first turn: cap=1, gold=1, draw 1. (Black gets cap=1 +
        # 1 mulligan card extra and starts pre-loaded with 4 — symmetry per
        # the spec.)
        white = self.player_by_seat("white")
        assert white
        white.gold_cap = 1
        white.gold = 1
        white.moves_remaining = 1
        self._log("turn 1 — white")

    # ---- turn machine ----

    def begin_turn(self, seat: str) -> None:
        p = self.player_by_seat(seat)
        opp = self.opponent_of(seat)
        assert p
        # Upkeep — Extra Turn bonus turns get a hard gold cap (e.g. 5).
        if p.extra_turn_gold_cap > 0:
            override_cap = p.extra_turn_gold_cap
            p.extra_turn_gold_cap = 0
            p.gold = min(p.gold_cap, override_cap)
            # gold_cap itself unchanged — next normal turn resumes natural curve.
        else:
            if p.gold_cap < GOLD_CAP_MAX:
                p.gold_cap += 1
            p.gold = p.gold_cap
        p.moves_remaining = 1
        if p.no_chess_move_this_turn:
            p.moves_remaining = 0
        # Activate next-turn-incoming tax, then clear it.
        p.spell_tax = p.spell_tax_next_turn
        p.spell_tax_next_turn = 0
        # Draw
        if p.deck:
            new = draw(p.deck, 1)
            if len(p.hand) < HAND_CAP:
                p.hand.extend(new)
            # else burn (Hearthstone-style); nothing else to do.
        self.turn_started_ms = self.now_ms()
        # Opponent's per-turn flags do NOT auto-reset here — they reset at end
        # of their own turn.

    def end_turn(self, seat: str) -> str | None:
        if self.phase != Phase.PLAYING:
            return "not playing"
        if seat != self.active_seat:
            return "not your turn"
        if self.pending_card_play is not None:
            return "resolve pending card first"
        p = self.player_by_seat(seat)
        assert p
        # Must-move enforcement: a card has either already locked out moves
        # (no_chess_move_this_turn), or the player must have moved at least
        # once (or used a counts-as-move card). Exception: if the player
        # genuinely has zero legal moves (no piece can move anywhere; pieces
        # placed this turn don't count), allow ending.
        if not p.has_acted_this_turn and not p.no_chess_move_this_turn:
            if self._player_has_any_legal_move(p):
                return "you must make a move (or play a card that counts as your move)"
        # Cannot end turn while your king is in check.
        if self.board.is_in_check(seat, modular=p.modular_board_this_turn):  # type: ignore[arg-type]
            return "you are in check — get out of check before ending your turn"
        # Discard any undo snapshot — once you commit to ending, the move stands.
        self.undo_snapshot = None
        self.annotations[seat].clear()
        # Clear summoning-sickness on the player who's ending their turn —
        # next time they're active their pieces will be free to move.
        self.board.clear_sickness_for(seat)
        # Clear caster's per-turn flags.
        p.reset_turn_flags()
        # Queued extra turn?
        if p.extra_turn_queued:
            p.extra_turn_queued = False
            # Same player goes again; their no-king-capture flag from the
            # 10g card applies for the bonus turn.
            p.cannot_capture_king_this_turn = p.extra_turn_no_capture_king
            p.extra_turn_no_capture_king = False
            p.extra_turn_gold_cap = 5  # bonus turn capped at 5 gold
            self.turn_number += 1
            self.begin_turn(seat)
            self._log(f"turn {self.turn_number} — {seat} (extra)")
            return None
        # Normal handoff.
        self.active_seat = "black" if seat == "white" else "white"
        self.turn_number += 1
        self.begin_turn(self.active_seat)
        self._log(f"turn {self.turn_number} — {self.active_seat}")
        return None

    # ---- card play ----

    def can_afford(self, player: Player, card: Card, modal_choice: str | None = None) -> bool:
        if self.phase != Phase.PLAYING:
            return False
        if player.seat != self.active_seat:
            return False
        return player.gold >= cost_for(player, card.defn, modal_choice)

    def play_card(self, seat: str, slot: int, targets: list[dict] | None,
                  modal: str | None, discard_slots: list[int] | None) -> tuple[list[dict], str | None]:
        if self.phase != Phase.PLAYING:
            return [], "not in playing phase"
        if seat != self.active_seat:
            return [], "not your turn"
        if self.pending_card_play is not None:
            return [], "another card is mid-resolution"
        p = self.player_by_seat(seat)
        assert p
        if slot < 0 or slot >= len(p.hand):
            return [], "bad slot"
        card = p.hand[slot]

        # Counts-as-move tag: must have a move available.
        if "counts_as_move" in card.defn.tags and p.moves_remaining <= 0:
            return [], "no chess move available for this card"

        # Validate cost (with tax / Any-Piece modal cost / free-pieces).
        effective_cost = cost_for(p, card.defn, modal)
        # Echo's free-cast: the stolen card (identified by instance_id) is
        # free for its immediate auto-play. Consumed on this play_card.
        echo_free_now = (
            p.echo_free_instance_id is not None
            and card.instance_id == p.echo_free_instance_id
        )
        if echo_free_now:
            effective_cost = 0
        if p.gold < effective_cost:
            return [], "not enough gold"

        # Validate target counts up-front. Most cards have fixed schemas, but
        # spell_eight_or_eight (modal-driven) supplies a variable count and
        # gets bespoke validation below.
        targets = targets or []
        if card.defn.modal and modal is None:
            return [], "modal choice required"

        err = self._validate_card_targets(p, card, targets, modal)
        if err:
            return [], err

        # Snapshot pre-card state so the player can undo this play.
        # Cards that emit a cross-player prompt (Forced Promotion, Echo)
        # opt out below — once the opponent has been given information, an
        # undo would be a free peek-and-back-out.
        self.undo_snapshot = self._snapshot_for_undo(seat)
        # Charge gold, remove card from hand
        p.gold -= effective_cost
        p.hand.pop(slot)
        if echo_free_now:
            p.echo_free_instance_id = None

        # Forced Promotion + Echo emit prompts to the opposing player and
        # are no longer undoable (an undo would let the caster peek-and-back-out).
        # We invalidate the snapshot we just took at the start of these blocks.

        # Forced Promotion: the opponent picks Knight or Bishop (not the
        # caster). Hold the spell mid-resolution and prompt the opponent.
        if card.defn.id == "spell_forced_promotion":
            self.undo_snapshot = None
            opp = self.opponent_of(seat)
            if opp is None:
                p.gold += effective_cost
                p.hand.insert(slot, card)
                return [], "no opponent"
            self.pending_card_play = PendingPlay(
                seat=seat, card=card, slot=slot, paid_gold=effective_cost,
                targets_collected=list(targets), modal_choice=None,
            )
            prompt = build_modal_prompt(opp.seat, ["Knight", "Bishop"])
            prompt.label = "Forced Promotion — pick Knight or Bishop"
            self.pending_prompts[prompt.prompt_id] = prompt
            self._log(f"{seat} plays Forced Promotion — awaiting opponent")
            return [{"kind": "card_played", "by": seat, "card_id": card.defn.id}], None

        # Echo special-cases: stall here, emit a pick_opp_hand prompt that
        # walks the caster through opp's hand. apply_echo_pick finishes the
        # spell once they pick a slot.
        if card.defn.id == "spell_echo":
            self.undo_snapshot = None
            opp = self.opponent_of(seat)
            if opp is None or not opp.hand:
                # Refund — nothing to steal.
                p.gold += effective_cost
                p.hand.insert(slot, card)
                return [], "opponent's hand is empty"
            self.pending_card_play = PendingPlay(
                seat=seat, card=card, slot=slot, paid_gold=effective_cost,
                targets_collected=[], modal_choice=modal,
            )
            prompt = Prompt(
                prompt_id=new_prompt_id(),
                seat=seat,
                kind="pick_opp_hand",
                label="Echo — pick a card from opponent's hand",
                extra={
                    "opp_hand": [
                        {"slot": i, "card_id": c.defn.id, "name": c.defn.name,
                         "cost": c.defn.cost, "type": c.defn.type}
                        for i, c in enumerate(opp.hand)
                    ],
                },
            )
            self.pending_prompts[prompt.prompt_id] = prompt
            self._log(f"{seat} plays Echo")
            return [{"kind": "card_played", "by": seat, "card_id": card.defn.id}], None

        pending = PendingPlay(
            seat=seat, card=card, slot=slot, paid_gold=effective_cost,
            targets_collected=list(targets), modal_choice=modal,
        )
        self.pending_card_play = pending
        try:
            events = resolve(self, p, pending)
        except NotImplementedError as exc:
            # Phase 2 stub. Refund and put card back so the engine remains
            # consistent if a Phase-1 client somehow triggers it.
            p.gold += effective_cost
            p.hand.insert(slot, card)
            self.pending_card_play = None
            return [], f"effect not implemented: {exc}"
        self.pending_card_play = None

        # If the card staged a prompt that reveals private info (currently
        # only choose_opp_move shows the victim's legal moves), undo is no
        # longer safe — would be a free peek.
        if any(pr.kind == "choose_opp_move" for pr in self.pending_prompts.values()):
            self.undo_snapshot = None

        # Apply card tag side-effects after resolution.
        if "counts_as_move" in card.defn.tags:
            p.moves_remaining -= 1
            p.has_acted_this_turn = True

        prefix_events = [{"kind": "card_played", "by": seat, "card_id": card.id}]
        self._log(f"{seat} plays {card.name}")
        return prefix_events + events, None

    def _validate_card_targets(self, player: Player, card: Card,
                                targets: list[dict], modal: str | None) -> str | None:
        defn = card.defn
        key = defn.effect_key

        # piece_any picks a kind via modal — placement zone depends on it.
        if defn.id == "piece_any":
            if len(targets) != 1:
                return "need 1 placement target"
            kind = (modal or "").lower()
            if kind not in ("pawn", "knight", "bishop", "rook", "queen"):
                return "pick a piece kind"
            sq = targets[0].get("square")
            zone = placement_zone_for(player.seat, kind)
            if sq not in zone or not self.board.is_empty(sq or ""):
                return ("target must be empty pawn-zone square"
                        if kind == "pawn" else "target must be empty back-2 square")
            return None

        # Bespoke shapes ---------------------------------------------------
        if key == "spell_eight_or_eight":
            choice = (modal or "").lower()
            if choice.startswith("pawn"):
                if not (1 <= len(targets) <= 8):
                    return "pick 1..8 squares for the pawns"
                claimed: set[str] = set()
                for t in targets:
                    sq = t.get("square")
                    if sq in claimed:
                        return "duplicate placement square"
                    if sq not in pawn_placement_ranks(player.seat) or not self.board.is_empty(sq or ""):
                        return "target must be empty pawn-zone square"
                    claimed.add(sq)
                return None
            # material mode
            total = 0
            claimed = set()
            for t in targets:
                kind = t.get("piece_kind")
                sq = t.get("square")
                if kind not in MATERIAL_POINTS or kind == "pawn":
                    return "material mode is non-pawn pieces only"
                if sq in claimed:
                    return "duplicate placement square"
                if sq not in back_two_ranks(player.seat) or not self.board.is_empty(sq or ""):
                    return "target must be empty back-2 square"
                claimed.add(sq)
                total += MATERIAL_POINTS[kind]
            if total != 8:
                return "material must total exactly 8"
            return None

        if key == "spell_material_to_queen":
            if len(targets) != 1:
                return "need 1 placement target"
            t = targets[0]
            sq = t.get("square")
            sac = t.get("sac_squares") or []
            total = 0
            seen: set[str] = set()
            for s in sac:
                if s in seen:
                    return "duplicate sacrifice square"
                seen.add(s)
                pc = self.board.at(s)
                if pc is None or pc.color != player.seat or pc.kind == "king":
                    return "sacrifice must be your own non-king piece"
                total += MATERIAL_POINTS.get(pc.kind, 0)
            if total != 7:
                return "sacrifice must total exactly 7"
            if sq not in back_two_ranks(player.seat):
                return "placement must be in your back two ranks"
            # Placement is allowed on any back-2 square that is empty OR
            # currently holds one of the to-be-sacrificed pieces.
            occupant = self.board.at(sq or "")
            if occupant is not None and sq not in seen:
                return "placement square must be empty or one of your sacrificed pieces"
            return None

        if key == "spell_queen_and_strip":
            if len(targets) != 1:
                return "need 1 placement target"
            t = targets[0]
            sq = t.get("square")
            if sq not in back_two_ranks(player.seat) or not self.board.is_empty(sq or ""):
                return "target must be empty back-2 square"
            strip = t.get("strip_squares") or []
            total = 0
            opp = self.opponent_of(player.seat)
            seen = set()
            for s in strip:
                if s in seen:
                    return "duplicate strip square"
                seen.add(s)
                pc = self.board.at(s)
                if pc is None or (opp and pc.color != opp.seat) or pc.kind == "king":
                    return "strip target must be enemy non-king"
                total += MATERIAL_POINTS.get(pc.kind, 0)
            if total > 4:
                return "may only strip up to 4 material"
            return None

        if key == "spell_pawns_to_queen":
            pawns = [sq for sq, p in self.board.pieces_of(player.seat) if p.kind == "pawn"]  # type: ignore[arg-type]
            if len(pawns) < 5:
                return "need at least 5 friendly pawns"
            if len(targets) != 1:
                return "need 1 placement target"
            sq = targets[0].get("square")
            # Placement square allowed to be the pawn-cleared square it gets placed on,
            # but the validator checks before pawns are removed. Allow it if it's a
            # back-2 square that is either empty or currently holds one of those
            # sacrificed pawns.
            if sq not in back_two_ranks(player.seat):
                return "placement must be in your back two ranks"
            pc = self.board.at(sq or "")
            if pc is not None and not (pc.color == player.seat and pc.kind == "pawn"):
                return "placement square must be empty (or your own pawn)"
            return None

        if key == "spell_combine_pawns":
            if len(targets) != 4:
                return "need 3 pawns + 1 placement"
            pawn_sqs = [t.get("square") for t in targets[:3]]
            if len(set(pawn_sqs)) != 3:
                return "pick three distinct pawns"
            for sq in pawn_sqs:
                pc = self.board.at(sq or "")
                if pc is None or pc.color != player.seat or pc.kind != "pawn":
                    return "target must be one of your pawns"
            place_sq = targets[3].get("square")
            if place_sq not in back_two_ranks(player.seat):
                return "placement must be in your back two ranks"
            pc = self.board.at(place_sq or "")
            if pc is not None and pc.color != player.seat:
                return "placement square must be empty"
            # Allow placement on a sacrificed pawn's square; otherwise require empty.
            if pc is not None and place_sq not in pawn_sqs:
                return "placement square must be empty"
            return None

        if key == "spell_play_2_pawns" or key == "spell_quad_deploy" or key == "spell_rook_and_minor":
            need = len(defn.targets)
            if len(targets) != need:
                return f"need {need} placement targets"
            seen = set()
            for spec, t in zip(defn.targets, targets):
                sq = t.get("square")
                if sq in seen:
                    return "duplicate placement square"
                zone = (pawn_placement_ranks(player.seat)
                        if spec == "empty_square_pawn_zone"
                        else back_two_ranks(player.seat))
                if sq not in zone or not self.board.is_empty(sq or ""):
                    return ("target must be empty pawn-zone square"
                            if spec == "empty_square_pawn_zone"
                            else "target must be empty back-2 square")
                seen.add(sq)
            return None

        if key == "spell_adjacent_en_passant":
            if len(targets) != 2:
                return "need pawn + target piece"
            pawn_sq = targets[0].get("square")
            victim_sq = targets[1].get("square")
            pc = self.board.at(pawn_sq or "")
            if pc is None or pc.color != player.seat or pc.kind != "pawn":
                return "target must be one of your pawns"
            victim = self.board.at(victim_sq or "")
            if victim is None:
                return "target square is empty"
            if victim.kind == "king":
                return "cannot target a king"
            if victim_sq == pawn_sq:
                return "target must be a different piece"
            f1, r1 = sq_to_fr(pawn_sq); f2, r2 = sq_to_fr(victim_sq)
            if max(abs(f1 - f2), abs(r1 - r2)) != 1:
                return "target must be adjacent to your pawn"
            return None

        if key == "spell_king_to_center":
            if len(targets) != 2:
                return "need king + central square"
            king_sq = targets[0].get("square")
            pc = self.board.at(king_sq or "")
            if pc is None or pc.kind != "king":
                return "first target must be a king"
            dest = targets[1].get("square")
            if dest not in CENTRAL_4 or not self.board.is_empty(dest or ""):
                return "destination must be empty central square"
            return None

        if key == "spell_teleport":
            if len(targets) != 2:
                return "need piece + destination"
            src = targets[0].get("square"); dst = targets[1].get("square")
            pc = self.board.at(src or "")
            if pc is None or pc.color != player.seat:
                return "target must be one of your pieces"
            if not self.board.is_empty(dst or ""):
                return "destination must be empty"
            # Pawns can't be teleported onto rank 1 or 8 — promotion would be
            # bypassed and a back-rank pawn does nothing useful.
            if pc.kind == "pawn" and dst and dst[1] in ("1", "8"):
                return "pawns can't be teleported to rank 1 or 8"
            return None

        if key == "spell_discard_draw":
            if len(targets) != 1:
                return "need discard target"
            slots = targets[0].get("slots") or []
            if any(not isinstance(s, int) or s < 0 or s >= len(player.hand) for s in slots):
                return "bad discard slot"
            if len(set(slots)) != len(slots):
                return "duplicate discard slot"
            return None

        # No-target spells with modal-only or no input.
        if not defn.targets:
            return None

        # Generic path: length must match, then per-target spec check.
        if len(targets) != len(defn.targets):
            return f"need {len(defn.targets)} targets"
        return self._validate_targets(player, defn, targets)

    def _validate_targets(self, player: Player, defn: CardDef, targets: list[dict]) -> str | None:
        for spec, t in zip(defn.targets, targets):
            sq = t.get("square")
            if spec == "empty_square_back2":
                if sq not in back_two_ranks(player.seat):
                    return "target must be your back two ranks"
                if not self.board.is_empty(sq):
                    return "target square must be empty"
            elif spec == "empty_square_pawn_zone":
                if sq not in pawn_placement_ranks(player.seat):
                    return "pawns place on your 2nd or 3rd rank"
                if not self.board.is_empty(sq):
                    return "target square must be empty"
            elif spec == "empty_square_back2_opp":
                if sq not in opponent_2nd_rank(player.seat):
                    return "target must be opponent's 2nd/7th rank"
                if not self.board.is_empty(sq):
                    return "target square must be empty"
            elif spec == "empty_square_central4":
                if sq not in CENTRAL_4:
                    return "target must be central (d4/d5/e4/e5)"
                if not self.board.is_empty(sq):
                    return "target square must be empty"
            elif spec == "any_empty_square":
                if not self.board.is_empty(sq or ""):
                    return "target square must be empty"
            elif spec == "friendly_pawn":
                pc = self.board.at(sq or "")
                if pc is None or pc.color != player.seat or pc.kind != "pawn":
                    return "target must be one of your pawns"
            elif spec == "friendly_piece":
                pc = self.board.at(sq or "")
                if pc is None or pc.color != player.seat:
                    return "target must be one of your pieces"
            elif spec == "enemy_piece":
                pc = self.board.at(sq or "")
                if pc is None or pc.color == player.seat:
                    return "target must be an enemy piece"
            elif spec == "any_adjacent_piece":
                # used by Adjacent En Passant: any non-king piece adjacent to
                # the friendly pawn picked at step 0. Friend or foe.
                pawn_sq = targets[0].get("square")
                pc = self.board.at(sq or "")
                if pc is None:
                    return "target must be a piece adjacent to your pawn"
                if pc.kind == "king":
                    return "cannot target a king"
                if sq == pawn_sq:
                    return "target must be a different piece"
                if not self._is_adjacent(pawn_sq, sq):
                    return "target must be adjacent to your pawn"
            elif spec == "any_knight_or_bishop":
                pc = self.board.at(sq or "")
                if pc is None or pc.kind not in ("knight", "bishop"):
                    return "target must be a knight or bishop"
        return None

    def draw_cards(self, player: Player, n: int) -> int:
        drawn = 0
        for _ in range(n):
            if not player.deck:
                break
            card = player.deck.pop()
            if len(player.hand) < HAND_CAP:
                player.hand.append(card)
            # else burn
            drawn += 1
        return drawn

    # ---- undo / annotations ----

    def _snapshot_for_undo(self, seat: str) -> dict:
        p = self.player_by_seat(seat)
        opp = self.opponent_of(seat)
        assert p
        squares = {
            sq: Piece(pc.color, pc.kind, has_moved=pc.has_moved,
                      placed_this_turn=pc.placed_this_turn)
            for sq, pc in self.board.squares.items()
        }
        return {
            "squares": squares,
            "ep_target": self.board.ep_target,
            "gold": p.gold,
            "hand": list(p.hand),
            "deck": list(p.deck),
            "opp_hand": list(opp.hand) if opp else [],
            "opp_gold": opp.gold if opp else 0,
            "moves_remaining": p.moves_remaining,
            "has_acted_this_turn": p.has_acted_this_turn,
            "triple_move_used": set(p.triple_move_used),
            "triple_move_active": p.triple_move_active,
            "extra_pawn_squares": dict(p.extra_pawn_squares),
            "pawn_back_pawn_sq": p.pawn_back_pawn_sq,
            "pawn_two_moves_armed": p.pawn_two_moves_armed,
            "pieces_free_this_turn": p.pieces_free_this_turn,
            "no_chess_move_this_turn": p.no_chess_move_this_turn,
            "cannot_capture_king_this_turn": p.cannot_capture_king_this_turn,
            "modular_board_this_turn": p.modular_board_this_turn,
            "echo_free_instance_id": p.echo_free_instance_id,
            "spell_tax_next_turn_opp": opp.spell_tax_next_turn if opp else 0,
            "opp_chosen_flag": opp.opp_moves_chosen_by_me_next_turn if opp else False,
            "extra_turn_queued": p.extra_turn_queued,
            "log_len": len(self.log),
        }

    def undo_move(self, seat: str) -> str | None:
        if self.phase != Phase.PLAYING:
            return "not in playing phase"
        if seat != self.active_seat:
            return "not your turn"
        if self.pending_card_play is not None:
            return "can't undo with a card mid-resolution"
        if self.undo_snapshot is None:
            return "nothing to undo"
        snap = self.undo_snapshot
        p = self.player_by_seat(seat)
        opp = self.opponent_of(seat)
        assert p
        self.board.squares = snap["squares"]
        self.board.ep_target = snap["ep_target"]
        # Card-play snapshots also restore hand/deck/gold; move-only snapshots
        # left these unchanged but the snapshot still has consistent values.
        p.gold = snap["gold"]
        p.hand = list(snap["hand"])
        p.deck = list(snap["deck"])
        if opp:
            opp.hand = list(snap["opp_hand"])
            opp.gold = snap["opp_gold"]
            opp.spell_tax_next_turn = snap["spell_tax_next_turn_opp"]
            opp.opp_moves_chosen_by_me_next_turn = snap["opp_chosen_flag"]
        p.moves_remaining = snap["moves_remaining"]
        p.has_acted_this_turn = snap["has_acted_this_turn"]
        p.triple_move_used = snap["triple_move_used"]
        p.triple_move_active = snap["triple_move_active"]
        p.extra_pawn_squares = snap["extra_pawn_squares"]
        p.pawn_back_pawn_sq = snap["pawn_back_pawn_sq"]
        p.pawn_two_moves_armed = snap["pawn_two_moves_armed"]
        p.pieces_free_this_turn = snap["pieces_free_this_turn"]
        p.no_chess_move_this_turn = snap["no_chess_move_this_turn"]
        p.cannot_capture_king_this_turn = snap["cannot_capture_king_this_turn"]
        p.modular_board_this_turn = snap["modular_board_this_turn"]
        p.echo_free_instance_id = snap["echo_free_instance_id"]
        p.extra_turn_queued = snap["extra_turn_queued"]
        while len(self.log) > snap["log_len"]:
            self.log.pop()
        # After-undo, also drop any pending prompts created by the undone
        # action (e.g., an Echo pick_opp_hand that was just staged).
        self.pending_prompts.clear()
        self.pending_card_play = None
        self.undo_snapshot = None
        self._log(f"{seat} undoes last action")
        return None

    def toggle_annotation(self, seat: str, sq: str) -> None:
        if seat not in ("white", "black"):
            return
        ann = self.annotations[seat]
        if sq in ann:
            ann.discard(sq)
        else:
            ann.add(sq)

    # ---- chess move ----

    def make_move(self, seat: str, src: str, dst: str, promote: str | None) -> tuple[list[dict], str | None]:
        if self.phase != Phase.PLAYING:
            return [], "not in playing phase"
        if seat != self.active_seat:
            return [], "not your turn"
        p = self.player_by_seat(seat)
        assert p
        if p.no_chess_move_this_turn:
            return [], "cannot move this turn"
        if p.moves_remaining <= 0:
            return [], "no moves remaining"
        piece = self.board.at(src)
        if piece is None or piece.color != seat:
            return [], "no piece of yours on source"
        if piece.placed_this_turn:
            return [], "piece just placed — can't move it this turn"
        if p.triple_move_active and src in p.triple_move_used:
            return [], "Triple Move requires 3 distinct pieces"
        modular = p.modular_board_this_turn
        # If pawn_two_moves_armed is set, all moves this turn must be pawn moves.
        if p.pawn_two_moves_armed and piece.kind != "pawn":
            return [], "must move a pawn this turn"
        legal = set(self.board.legal_destinations(src, modular=modular))
        # Extra-pawn-step bonus: this pawn can take 1 more forward step than normal.
        if piece.kind == "pawn":
            legal.update(self._extra_pawn_destinations(p, piece, src))
        # Pawn-backward modal (option A of 2g card): allow one square back
        # (empty), and also one diagonal-backward capture against an enemy.
        if piece.kind == "pawn" and self._pawn_back_allowed(p, src):
            back = self._pawn_back_square(piece, src, modular)
            if back is not None and self.board.is_empty(back):
                legal.add(back)
            for diag in self._pawn_back_diag_squares(piece, src, modular):
                target = self.board.at(diag)
                if target is not None and target.color != piece.color and target.kind != "king":
                    legal.add(diag)
        if dst not in legal:
            return [], "illegal move"
        # King-capture restriction
        target_piece = self.board.at(dst)
        if (
            target_piece is not None
            and target_piece.kind == "king"
            and p.cannot_capture_king_this_turn
        ):
            return [], "cannot capture king this turn"
        # Promotion handling
        promote_kind = None
        if promote in ("queen", "rook", "bishop", "knight"):
            promote_kind = promote  # type: ignore[assignment]
        move = Move(src=src, dst=dst, promote=promote_kind)  # type: ignore[arg-type]
        # If a pawn is hitting promo rank and no choice given, default to queen
        # (UI should ask, but server must complete the move).
        if piece.kind == "pawn":
            last_rank = "8" if piece.color == "white" else "1"
            if dst[1] == last_rank and move.promote is None:
                move.promote = "queen"  # type: ignore[assignment]

        # Detect & consume the backward step (replaces a normal pawn move).
        used_back = False
        if piece.kind == "pawn":
            f1, r1 = sq_to_fr(src); f2, r2 = sq_to_fr(dst)
            direction_back = -1 if piece.color == "white" else 1
            if f1 == f2 and (r2 - r1) == direction_back:
                used_back = self._pawn_back_allowed(p, src)
        # Snapshot for undo BEFORE mutating board / counters.
        self.undo_snapshot = self._snapshot_for_undo(seat)
        result = self.board.apply_move(move, modular=modular)
        p.moves_remaining -= 1
        p.has_acted_this_turn = True
        if p.triple_move_active:
            p.triple_move_used.add(dst)  # piece is now at dst; mark it used
        if used_back:
            p.pawn_back_pawn_sq = None
        # Consume the per-pawn extra-step bonus once that pawn moves.
        if src in p.extra_pawn_squares:
            del p.extra_pawn_squares[src]

        events: list[dict] = [{
            "kind": "piece_moved",
            "from": src,
            "to": dst,
            "captured": result["captured_kind"],
        }]
        if result["captured_kind"] is not None:
            events.append({
                "kind": "piece_captured",
                "sq": result["captured_sq"],
                "victim_kind": result["captured_kind"],
                "victim_color": result["captured_color"],
            })
        if result["promoted"]:
            events.append({
                "kind": "piece_promoted", "sq": dst, "to": result["promoted"],
            })
        if result["king_captured"]:
            self.phase = Phase.DONE
            self.winner = seat
            self.win_reason = "king_capture"
            events.append({"kind": "king_captured", "winner": seat})
            self._log(f"{seat} captures the king — game over")
        else:
            self._log(f"{seat} {src}-{dst}")
        return events, None

    def _legal_moves_for_opp_chooser(self, victim: Player, exclude_caster_king: bool) -> list[dict]:
        """Build the legal-move list shown to the caster of spell_choose_opp_move.
        Excludes any move that would capture the caster's king."""
        modular = victim.modular_board_this_turn
        moves = self.board.all_legal_moves(victim.seat, modular=modular)  # type: ignore[arg-type]
        opp = self.opponent_of(victim.seat)
        if exclude_caster_king and opp is not None:
            caster_king = self.board.find_king(opp.seat)  # type: ignore[arg-type]
        else:
            caster_king = None
        out = []
        for m in moves:
            if caster_king is not None and m.dst == caster_king:
                continue
            out.append({"from": m.src, "to": m.dst, "promote": m.promote})
        return out

    def apply_echo_pick(self, seat: str, opp_slot: int) -> tuple[list[dict], str | None]:
        """Caster picked a slot in opp's hand for Echo to steal. The stolen
        card lands in the caster's hand and is flagged for one free immediate
        cast (echo_free_slot)."""
        if self.pending_card_play is None or self.pending_card_play.card.defn.id != "spell_echo":
            return [], "no echo pending"
        if self.pending_card_play.seat != seat:
            return [], "not your echo"
        opp = self.opponent_of(seat)
        p = self.player_by_seat(seat)
        if opp is None or p is None:
            return [], "bad room state"
        if opp_slot < 0 or opp_slot >= len(opp.hand):
            return [], "bad slot"
        stolen = opp.hand.pop(opp_slot)
        events: list[dict] = []
        if len(p.hand) >= HAND_CAP:
            # No room — fall back to old behavior (burn).
            events.append({"kind": "card_burned", "by": seat, "card_id": stolen.defn.id})
        else:
            p.hand.append(stolen)
            p.echo_free_instance_id = stolen.instance_id
            events.append({"kind": "card_stolen", "by": seat, "card_id": stolen.defn.id})
        # Clear prompt + pending state.
        for pid, pr in list(self.pending_prompts.items()):
            if pr.seat == seat and pr.kind == "pick_opp_hand":
                del self.pending_prompts[pid]
        self.pending_card_play = None
        self._log(f"{seat} steals {stolen.defn.name} via Echo")
        return events, None

    def apply_forced_promotion_pick(self, opp_seat: str, choice: str) -> tuple[list[dict], str | None]:
        pending = self.pending_card_play
        if pending is None or pending.card.defn.id != "spell_forced_promotion":
            return [], "no forced promotion pending"
        caster = self.player_by_seat(pending.seat)
        if caster is None:
            return [], "bad state"
        if self.opponent_of(pending.seat) is None or self.opponent_of(pending.seat).seat != opp_seat:  # type: ignore[union-attr]
            return [], "only opponent picks"
        kind = (choice or "").lower()
        if kind not in ("knight", "bishop"):
            return [], "must be Knight or Bishop"
        pawn_sq = pending.targets_collected[0]["square"]
        pc = self.board.at(pawn_sq)
        if pc is None or pc.kind != "pawn" or pc.color != pending.seat:
            # State drifted; refund and bail.
            caster.gold += pending.paid_gold
            self.pending_card_play = None
            self.pending_prompts = {k: v for k, v in self.pending_prompts.items() if v.kind != "select_modal"}
            return [], "target pawn no longer valid"
        self.board.place(pawn_sq, Piece(pending.seat, kind, placed_this_turn=True))  # type: ignore[arg-type]
        events = [
            {"kind": "piece_captured", "sq": pawn_sq, "victim_kind": "pawn",
             "victim_color": pending.seat},
            {"kind": "piece_placed", "color": pending.seat, "piece_kind": kind,
             "sq": pawn_sq},
        ]
        # Clear the modal prompt for the opponent.
        self.pending_prompts = {k: v for k, v in self.pending_prompts.items() if v.kind != "select_modal"}
        self.pending_card_play = None
        self._log(f"{opp_seat} picks {kind.title()} — pawn at {pawn_sq} becomes {kind}")
        return events, None

    def apply_opp_chosen_move(self, caster_seat: str, src: str, dst: str,
                              promote: str | None) -> tuple[list[dict], str | None]:
        """The caster of spell_choose_opp_move resolves the prompt by picking
        a move from the victim's legal-move list. Immediate mode: the spell
        was cast on the caster's own turn and the chosen move applies right
        now (the caster's turn continues afterward)."""
        if self.phase != Phase.PLAYING:
            return [], "not in playing phase"
        # Find the pending choose_opp_move prompt for this caster.
        has_prompt = any(
            pr.seat == caster_seat and pr.kind == "choose_opp_move"
            for pr in self.pending_prompts.values()
        )
        if not has_prompt:
            return [], "no choose-opp-move pending"
        victim = self.opponent_of(caster_seat)
        if victim is None:
            return [], "no victim"
        moves = self._legal_moves_for_opp_chooser(victim, exclude_caster_king=True)
        if not any(m["from"] == src and m["to"] == dst for m in moves):
            return [], "not a legal choice"
        # Drop the prompt and apply the chosen move directly. We don't go
        # through make_move because that enforces turn ownership.
        self.pending_prompts = {
            k: v for k, v in self.pending_prompts.items() if v.kind != "choose_opp_move"
        }
        return self._apply_forced_opp_move(victim.seat, src, dst, promote)

    def _apply_forced_opp_move(self, victim_seat: str, src: str, dst: str,
                                promote: str | None) -> tuple[list[dict], str | None]:
        victim = self.player_by_seat(victim_seat)
        assert victim
        modular = victim.modular_board_this_turn
        piece = self.board.at(src)
        if piece is None or piece.color != victim_seat:
            return [], "no piece of victim's on source"
        legal = set(self.board.legal_destinations(src, modular=modular))
        if dst not in legal:
            return [], "illegal move"
        promote_kind = promote if promote in ("queen", "rook", "bishop", "knight") else None
        move = Move(src=src, dst=dst, promote=promote_kind)  # type: ignore[arg-type]
        if piece.kind == "pawn":
            last_rank = "8" if piece.color == "white" else "1"
            if dst[1] == last_rank and move.promote is None:
                move.promote = "queen"  # type: ignore[assignment]
        result = self.board.apply_move(move, modular=modular)
        events: list[dict] = [{
            "kind": "piece_moved", "from": src, "to": dst,
            "captured": result["captured_kind"],
        }]
        if result["captured_kind"] is not None:
            events.append({
                "kind": "piece_captured", "sq": result["captured_sq"],
                "victim_kind": result["captured_kind"],
                "victim_color": result["captured_color"],
            })
        if result["promoted"]:
            events.append({"kind": "piece_promoted", "sq": dst, "to": result["promoted"]})
        if result["king_captured"]:
            # Should be unreachable — _legal_moves_for_opp_chooser excludes
            # any move that captures the caster's king. Belt-and-braces.
            self.phase = Phase.DONE
            self.winner = victim_seat
            self.win_reason = "king_capture"
            events.append({"kind": "king_captured", "winner": victim_seat})
        self._log(f"{victim_seat} forced: {src}-{dst}")
        return events, None

    def _player_has_any_legal_move(self, player: Player) -> bool:
        modular = player.modular_board_this_turn
        for sq, pc in list(self.board.squares.items()):
            if pc.color != player.seat:
                continue
            if pc.placed_this_turn:
                continue
            if self.board.legal_destinations(sq, modular=modular):
                return True
            if pc.kind == "pawn" and self._pawn_back_allowed(player, sq):
                back = self._pawn_back_square(pc, sq, modular)
                if back is not None and self.board.is_empty(back):
                    return True
            if pc.kind == "pawn" and self._extra_pawn_destinations(player, pc, sq):
                return True
        return False

    def _extra_pawn_destinations(self, player: Player, piece, src: str) -> list[str]:
        bonus = player.extra_pawn_squares.get(src, 0)
        if bonus <= 0:
            return []
        # Card text: "Your pawn can move an extra square this turn." Glossed
        # by David as "it can move twice" — so the pawn's range this turn
        # is capped at 2 squares regardless of how many bonuses stacked.
        # A fresh pawn (already gets 2) gets nothing extra; a moved pawn
        # gets to take its missed 2-step.
        f, r = sq_to_fr(src)
        direction = 1 if piece.color == "white" else -1
        out: list[str] = []
        base_max = 2 if not piece.has_moved else 1
        total = min(base_max + bonus, 2)
        for step in range(1, total + 1):
            nr = r + direction * step
            if not (0 <= nr < 8):
                break
            sq = fr_to_sq(f, nr)
            if not self.board.is_empty(sq):
                break
            out.append(sq)
        return out

    def _is_adjacent(self, a: str | None, b: str | None) -> bool:
        if not a or not b:
            return False
        f1, r1 = sq_to_fr(a); f2, r2 = sq_to_fr(b)
        return abs(f1 - f2) <= 1 and abs(r1 - r2) <= 1 and (a != b)

    def _pawn_back_diag_squares(self, piece, src: str, modular: bool) -> list[str]:
        """Diagonal-backward squares for a pawn (used by Pawn-Back card to
        allow a backward capture in addition to a backward step)."""
        f, r = sq_to_fr(src)
        direction = -1 if piece.color == "white" else 1
        out = []
        for df in (-1, 1):
            nf = f + df
            nr = r + direction
            if modular:
                nf = nf % 8; nr = nr % 8
            elif not (0 <= nf < 8 and 0 <= nr < 8):
                continue
            out.append(fr_to_sq(nf, nr))
        return out

    def _pawn_back_allowed(self, player: Player, src: str) -> bool:
        target = player.pawn_back_pawn_sq
        return target is not None and (target == "*" or target == src)

    def _pawn_back_square(self, piece, src: str, modular: bool) -> str | None:
        f, r = sq_to_fr(src)
        direction = -1 if piece.color == "white" else 1
        nr = r + direction
        if modular:
            nr = nr % 8
        elif not (0 <= nr < 8):
            return None
        return fr_to_sq(f, nr)

    def concede(self, seat: str) -> None:
        if self.phase == Phase.DONE:
            return
        self.phase = Phase.DONE
        self.winner = "black" if seat == "white" else "white"
        self.win_reason = "concede"
        self._log(f"{seat} concedes")

    def rematch(self) -> str | None:
        if self.phase != Phase.DONE:
            return "no game to rematch"
        # Wipe game state, keep seats, redeal as if both players just joined.
        self.board = Board.starting()
        self.active_seat = "white"
        self.turn_number = 1
        self.pending_prompts = {}
        self.pending_card_play = None
        self.log.clear()
        self.turn_started_ms = 0
        self.winner = None
        self.win_reason = None
        self.extra_turn_pending = False
        for p in self.players.values():
            p.hand = []
            p.deck = []
            p.gold = 0
            p.gold_cap = 0
            p.mulligan_done = False
            p.reset_turn_flags()
            p.spell_tax = 0
            p.spell_tax_next_turn = 0
            p.extra_turn_queued = False
            p.extra_turn_no_capture_king = False
            p.opp_moves_chosen_by_me_next_turn = False
        if "white" in self.seats and "black" in self.seats:
            self._begin_mulligan()
        else:
            self.phase = Phase.LOBBY
        return None

    # ---- state snapshot ----

    def player_state(self, p: Player) -> dict:
        return {
            "gold": p.gold,
            "gold_cap": p.gold_cap,
            "deck_size": len(p.deck),
            "hand_size": len(p.hand),
            "extra_moves": p.moves_remaining,
            "has_acted_this_turn": p.has_acted_this_turn,
            "spell_tax": p.spell_tax,
            "pieces_free_this_turn": p.pieces_free_this_turn,
            "no_chess_move_this_turn": p.no_chess_move_this_turn,
            "cannot_capture_king_this_turn": p.cannot_capture_king_this_turn,
            "modular_board_this_turn": p.modular_board_this_turn,
            "must_move_pawn": p.pawn_two_moves_armed,
        }

    def snapshot_for(self, viewer: Player | None) -> dict:
        white = self.player_by_seat("white")
        black = self.player_by_seat("black")
        you = None
        hand: list[dict] = []
        opp_hand_size = 0
        if viewer is not None and viewer.seat in ("white", "black"):
            # Deck tracker — your own remaining cards, grouped by card_id,
            # sorted by cost then name. Only included for the viewer's own
            # deck (opponent's deck stays hidden).
            from collections import Counter
            deck_counter = Counter(c.defn.id for c in viewer.deck)
            deck_cards = [
                {"card_id": cid,
                 "name": CARDS_BY_ID[cid].name,
                 "cost": CARDS_BY_ID[cid].cost,
                 "count": cnt}
                for cid, cnt in deck_counter.items()
            ]
            deck_cards.sort(key=lambda d: (d["cost"], d["name"]))
            you = {"seat": viewer.seat, "name": viewer.name,
                   "echo_free_instance_id": viewer.echo_free_instance_id,
                   "deck_cards": deck_cards}
            # Echo-stolen card is free; reflect that in `playable`.
            def _afford(c, _v=viewer):
                if _v.echo_free_instance_id is not None and c.instance_id == _v.echo_free_instance_id:
                    return self.phase == Phase.PLAYING and _v.seat == self.active_seat
                return self.can_afford(_v, c)
            hand = [
                {**c.to_json(slot=i, playable=_afford(c)),
                 "instance_id": c.instance_id}
                for i, c in enumerate(viewer.hand)
            ]
            opp = self.opponent_of(viewer.seat)
            opp_hand_size = len(opp.hand) if opp else 0
        # Check status: each side flagged if their king is currently attacked.
        # Uses the active player's modular flag only if it's their king being
        # checked — opponent's king on opp's own turn would use opp's flag, but
        # we don't really need that subtlety because check between turns uses
        # the standard board.
        active_player = self.player_by_seat(self.active_seat) if self.phase == Phase.PLAYING else None
        modular = bool(active_player and active_player.modular_board_this_turn)
        white_check = self.board.is_in_check("white", modular=modular)
        black_check = self.board.is_in_check("black", modular=modular)
        snap = {
            "type": "state",
            "phase": self.phase.value,
            "you": you,
            "white_name": white.name if white else None,
            "black_name": black.name if black else None,
            "active_seat": self.active_seat,
            "turn_number": self.turn_number,
            "turn_deadline_ms": (
                self.turn_started_ms + TURN_BUDGET_MS if self.phase == Phase.PLAYING else None
            ),
            "white": self.player_state(white) if white else None,
            "black": self.player_state(black) if black else None,
            "white_in_check": white_check,
            "black_in_check": black_check,
            "board": self.board.to_state(),
            "hand": hand,
            "opp_hand_size": opp_hand_size,
            "log": [e.to_json() for e in self.log],
            "winner": self.winner,
            "win_reason": self.win_reason,
            "pending_prompt": None,
            "can_undo": (
                self.undo_snapshot is not None
                and viewer is not None
                and viewer.seat == self.active_seat
                and self.phase == Phase.PLAYING
            ),
            "annotations": {
                "white": sorted(self.annotations["white"]),
                "black": sorted(self.annotations["black"]),
            },
        }
        # Surface any pending prompt for this viewer (Echo's pick_opp_hand,
        # choose_opp_move, etc.).
        if viewer is not None:
            for pr in self.pending_prompts.values():
                if pr.seat == viewer.seat:
                    snap["pending_prompt"] = pr.to_json()
                    break
        return snap


class RoomRegistry:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}
        self.lock = asyncio.Lock()

    async def get_or_create(self, code: str) -> Room:
        async with self.lock:
            r = self.rooms.get(code)
            if r is None:
                r = Room(code=code)
                self.rooms[code] = r
            return r

    def get(self, code: str) -> Room | None:
        return self.rooms.get(code)


registry = RoomRegistry()
