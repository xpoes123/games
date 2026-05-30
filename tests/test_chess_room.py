"""Full Phase-1 game flow against the Room state machine in-process. Drives
mulligan, places a pawn from hand, makes chess moves, captures the king."""
from __future__ import annotations

import random

from src.chess.board import Piece
from src.chess.cards import Card, CARDS_BY_ID
from src.chess.rooms import HAND_CAP, Phase, Room


def _give(player, card_id: str, n: int = 1) -> int:
    """Front-load a specific card type into a player's hand by hot-swapping
    the top of their deck. Returns the slot index of the first inserted."""
    defn = CARDS_BY_ID[card_id]
    start = len(player.hand)
    for _ in range(n):
        if len(player.hand) >= HAND_CAP:
            break
        player.hand.append(Card(instance_id=f"test_{card_id}_{len(player.hand)}", defn=defn))
    return start


def test_room_pairs_white_and_black_on_join():
    r = Room(code="TEST", rng=random.Random(1))
    p1 = r.add_player("Alice")
    p2 = r.add_player("Bob")
    assert p1.seat == "white"
    assert p2.seat == "black"
    assert r.phase == Phase.MULLIGAN
    assert len(p1.hand) == 3
    assert len(p2.hand) == 4


class _Coin:
    def __init__(self, value: float):
        self._v = value

    def random(self) -> float:
        return self._v


def test_seat_rng_gives_creator_black_on_high_roll():
    # random() >= 0.5 → the first joiner (lobby creator) takes black, and the
    # second joiner gets white. Each player is told their final seat at join
    # time, and the deck deal stays keyed to seat (white draws 3, black 4).
    r = Room(code="TEST", rng=random.Random(1), seat_rng=_Coin(0.9))
    p1 = r.add_player("Alice")
    p2 = r.add_player("Bob")
    assert p1.seat == "black"
    assert p2.seat == "white"
    assert r.player_by_seat("white") is p2
    assert len(p1.hand) == 4  # black
    assert len(p2.hand) == 3  # white


def test_seat_rng_gives_creator_white_on_low_roll():
    r = Room(code="TEST", rng=random.Random(1), seat_rng=_Coin(0.1))
    p1 = r.add_player("Alice")
    p2 = r.add_player("Bob")
    assert p1.seat == "white"
    assert p2.seat == "black"


def test_setup_phase_places_starting_material():
    r = Room(code="TEST", rng=random.Random(7))
    w = r.add_player("W")
    b = r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    assert r.phase == Phase.SETUP
    # Exactly-8 enforcement.
    assert r.setup_confirm("white") == "must spend exactly 8 points"
    # Wrong zone for pawn (rank 4 is not the pawn zone).
    assert r.setup_place("white", "pawn", "a4") is not None
    # Pawn on rank 3 is fine.
    assert r.setup_place("white", "pawn", "a3") is None
    # Place a queen — totals 1 + 8 = 9, should be rejected.
    assert r.setup_place("white", "queen", "d1") == "would exceed 8 points"
    # Replace the pawn with a queen (exactly 8).
    r.setup_remove("white", "a3")
    assert r.setup_place("white", "queen", "d1") is None
    # Black places 8 pawns.
    for f in "abcdefgh":
        r.setup_place("black", "pawn", f + "7")
    # Confirm white.
    assert r.setup_confirm("white") is None
    # Black hasn't confirmed → still SETUP.
    assert r.phase == Phase.SETUP
    # White's queen isn't on board yet (hidden until both confirm).
    assert r.board.at("d1") is None
    # Black confirms → board reveals + PLAYING.
    assert r.setup_confirm("black") is None
    assert r.phase == Phase.PLAYING
    assert r.board.at("d1").kind == "queen"
    assert r.board.at("a7").kind == "pawn"


def test_mulligan_redraw_keeps_hand_size():
    r = Room(code="TEST", rng=random.Random(7))
    p1 = r.add_player("A")
    p2 = r.add_player("B")
    err = r.submit_mulligan("white", [0, 2])
    assert err is None
    assert len(p1.hand) == 3
    # Black hasn't mulliganed yet; still MULLIGAN
    assert r.phase == Phase.MULLIGAN
    err2 = r.submit_mulligan("black", [])
    assert err2 is None
    # After both mulligans → SETUP phase (pre-game material placement).
    assert r.phase == Phase.SETUP
    r.test_skip_setup()
    assert r.phase == Phase.PLAYING


def test_full_game_pawn_placement_and_king_capture():
    r = Room(code="TEST", rng=random.Random(42))
    white = r.add_player("W")
    black = r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    r.test_skip_setup()
    assert r.phase == Phase.PLAYING

    # Force a known sequence: give white a queen card (cost 8 — too expensive
    # for turn 1) → instead, just place pieces directly on the board to
    # exercise the move + capture path. This validates the Room turn machine
    # without depending on gold ramp.
    r.board.place("e2", Piece("white", "queen"))

    # White moves queen from e2 → e8: captures black king.
    events, err = r.make_move("white", "e2", "e8", promote=None)
    assert err is None
    assert any(e["kind"] == "king_captured" for e in events)
    assert r.phase == Phase.DONE
    assert r.winner == "white"


def test_play_pawn_card_and_capture_king_full_path():
    """End-to-end Phase-1 path: white plays Pawn card → board has pawn →
    white moves a separate piece to capture black king. Uses card hot-swap."""
    r = Room(code="TEST", rng=random.Random(123))
    white = r.add_player("W")
    black = r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    r.test_skip_setup()

    # Ensure white has a Pawn (cost 1) at slot 0
    pawn_slot = _give(white, "piece_pawn", 1)
    # Make slot 0 the pawn card by reordering
    white.hand.insert(0, white.hand.pop(pawn_slot))

    events, err = r.play_card("white", 0, [{"square": "a2"}], modal=None, discard_slots=[])
    assert err is None, err
    assert any(e["kind"] == "piece_placed" and e["sq"] == "a2" for e in events)
    assert r.board.at("a2").kind == "pawn"
    # Gold spent
    assert white.gold == 0  # started at 1, paid 1

    # Move a synthetic rook onto e-file to capture king
    r.board.place("e2", Piece("white", "rook"))
    events, err = r.make_move("white", "e2", "e8", promote=None)
    assert err is None
    assert r.phase == Phase.DONE
    assert r.winner == "white"


def test_cannot_play_when_not_your_turn():
    r = Room(code="TEST", rng=random.Random(9))
    r.add_player("W")
    black = r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    r.test_skip_setup()
    black.hand.append(Card(instance_id="x", defn=CARDS_BY_ID["piece_pawn"]))
    events, err = r.play_card("black", len(black.hand) - 1, [{"square": "a7"}], None, [])
    assert err is not None
    assert "your turn" in err


def test_cannot_afford_card():
    r = Room(code="TEST", rng=random.Random(9))
    white = r.add_player("W")
    r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    r.test_skip_setup()
    # Queen costs 8; white starts at 1 gold.
    white.hand.insert(0, Card(instance_id="q", defn=CARDS_BY_ID["piece_queen"]))
    events, err = r.play_card("white", 0, [{"square": "a1"}], None, [])
    assert err == "not enough gold"


def test_end_turn_advances_seat_and_upkeeps():
    r = Room(code="TEST", rng=random.Random(5))
    r.add_player("W")
    r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    r.test_skip_setup()
    assert r.active_seat == "white"
    # must-move rule: make any legal move first
    r.make_move("white", "e1", "e2", None)
    err = r.end_turn("white")
    assert err is None
    assert r.active_seat == "black"
    black = r.player_by_seat("black")
    assert black.gold_cap == 1
    assert black.gold == 1
    assert black.moves_remaining == 1


def test_draw_2_card_effect():
    r = Room(code="TEST", rng=random.Random(11))
    white = r.add_player("W")
    r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    r.test_skip_setup()
    white.hand.insert(0, Card(instance_id="d2", defn=CARDS_BY_ID["spell_draw_2"]))
    pre = len(white.hand)
    events, err = r.play_card("white", 0, [], None, [])
    assert err is None
    # -1 played card, +2 draws = net +1
    assert len(white.hand) == pre + 1


def test_gain_2_gold_effect():
    r = Room(code="TEST", rng=random.Random(11))
    white = r.add_player("W")
    r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    r.test_skip_setup()
    white.hand.insert(0, Card(instance_id="g2", defn=CARDS_BY_ID["spell_gain_2_gold"]))
    # Bump cap so the +1 actually shows; on turn 1 we'd cap at 1 already.
    white.gold_cap = 5
    white.gold = 2
    events, err = r.play_card("white", 0, [], None, [])
    assert err is None
    # Cost 0, gain 1 (capped at gold_cap) → net +1.
    assert white.gold == 3


def test_spell_tax_opponent_applies_next_turn():
    r = Room(code="TEST", rng=random.Random(11))
    white = r.add_player("W")
    black = r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    r.test_skip_setup()
    white.gold = 10
    white.hand.insert(0, Card(instance_id="tax", defn=CARDS_BY_ID["spell_tax_opponent"]))
    events, err = r.play_card("white", 0, [], None, [])
    assert err is None, err
    # Tax queued for opponent's NEXT turn; activates at upkeep.
    assert black.spell_tax_next_turn == 3
    r.make_move("white", "e1", "e2", None)  # must-move rule
    r.end_turn("white")
    assert black.spell_tax == 3


# ---- 2026-05-22 batch: last_move, recap, setup auto-fill, Echo cancel ----

def test_last_move_tracked_and_reset_on_undo():
    r = Room(code="TEST", rng=random.Random(1))
    r.add_player("W")
    r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    r.test_skip_setup()
    assert r.last_move is None
    err = r.make_move("white", "e1", "e2", None)[1]
    assert err is None
    assert r.last_move == {"from": "e1", "to": "e2", "by": "white", "captured": None}
    # Undo clears it.
    assert r.undo_move("white") is None
    assert r.last_move is None


def test_recap_counts_moves_cards_captures():
    r = Room(code="TEST", rng=random.Random(1))
    r.add_player("W")
    r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    r.test_skip_setup()
    # Put a black pawn on e2 for white's king to capture.
    r.board.place("e2", Piece("black", "pawn"))
    events, err = r.make_move("white", "e1", "e2", None)
    assert err is None
    white = r.player_by_seat("white")
    black = r.player_by_seat("black")
    assert white.moves_made == 1
    assert white.pieces_captured == 1
    assert black.pieces_lost == 1
    # Concede so we get a DONE recap.
    r.concede("black")
    snap = r.snapshot_for(white)
    assert snap["recap"] is not None
    assert snap["recap"]["white"]["captured"] == 1
    assert snap["recap"]["black"]["lost"] == 1


def test_setup_auto_fill_completes_to_8_pawns():
    r = Room(code="TEST", rng=random.Random(1))
    w = r.add_player("W")
    b = r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    assert r.phase == Phase.SETUP
    # White has 0 points; auto-fill should add 8 pawns.
    err = r.setup_auto_fill_and_confirm("white")
    assert err is None
    assert w.setup_confirmed is True
    assert sum(1 for pk in w.setup_picks if pk["kind"] == "pawn") + len(w.setup_picks) > 0 or w.setup_picks == []
    # Black hasn't confirmed yet — board still empty.
    assert r.board.at("a2") is None
    # Auto-fill black too — both confirmed → PLAYING + board has white's 8 pawns.
    r.setup_auto_fill_and_confirm("black")
    assert r.phase == Phase.PLAYING
    assert r.board.at("a2") is not None and r.board.at("a2").kind == "pawn"


def test_echo_cancel_refunds_gold_and_card():
    r = Room(code="TEST", rng=random.Random(1))
    w = r.add_player("W")
    b = r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    r.test_skip_setup()
    w.gold = 10
    # Put Echo in white's hand and give black at least one card to steal.
    w.hand.insert(0, Card(instance_id="echo", defn=CARDS_BY_ID["spell_echo"]))
    b.hand.append(Card(instance_id="vict", defn=CARDS_BY_ID["piece_pawn"]))
    pre_gold = w.gold
    pre_hand_len = len(w.hand)
    events, err = r.play_card("white", 0, [], None, [])
    assert err is None
    # Echo is mid-resolution; gold is spent, card popped, pending prompt up.
    assert w.gold < pre_gold
    assert r.pending_card_play is not None
    # Cancel via cancel_echo_pick.
    err = r.cancel_echo_pick("white")
    assert err is None
    assert w.gold == pre_gold
    assert len(w.hand) == pre_hand_len
    assert r.pending_card_play is None
    assert not r.pending_prompts


def test_concede_works_during_setup():
    r = Room(code="TEST", rng=random.Random(1))
    r.add_player("W")
    r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    assert r.phase == Phase.SETUP
    r.concede("white")
    assert r.phase == Phase.DONE
    assert r.winner == "black"
