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
    assert r.phase == Phase.PLAYING


def test_full_game_pawn_placement_and_king_capture():
    r = Room(code="TEST", rng=random.Random(42))
    white = r.add_player("W")
    black = r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
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
    white.gold = 10
    white.hand.insert(0, Card(instance_id="tax", defn=CARDS_BY_ID["spell_tax_opponent"]))
    events, err = r.play_card("white", 0, [], None, [])
    assert err is None, err
    # Tax queued for opponent's NEXT turn; activates at upkeep.
    assert black.spell_tax_next_turn == 3
    r.make_move("white", "e1", "e2", None)  # must-move rule
    r.end_turn("white")
    assert black.spell_tax == 3
