"""Phase 2: every effect_key gets at least one happy-path test, and the
flag-driven cross-turn cards (modular board, extra turn, choose opp move)
have a cross-turn assertion."""
from __future__ import annotations

import random

from src.chess.board import Piece
from src.chess.cards import Card, CARDS_BY_ID
from src.chess.rooms import HAND_CAP, Phase, Room


def _setup() -> tuple[Room, "Player", "Player"]:  # type: ignore[name-defined]
    r = Room(code="T", rng=random.Random(1))
    w = r.add_player("W")
    b = r.add_player("B")
    r.submit_mulligan("white", [])
    r.submit_mulligan("black", [])
    return r, w, b


def _give(p, card_id: str) -> int:
    defn = CARDS_BY_ID[card_id]
    p.hand.insert(0, Card(instance_id=f"t_{card_id}", defn=defn))
    return 0


def _play(r: Room, seat: str, card_id: str, targets=None, modal=None,
          discard_slots=None, gold: int | None = None):
    p = r.player_by_seat(seat)
    _give(p, card_id)
    if gold is not None:
        p.gold = gold
    else:
        p.gold = max(p.gold, 10)
    return r.play_card(seat, 0, targets or [], modal, discard_slots or [])


# ---- 1g ----

def test_spell_extra_pawn_move_lets_moved_pawn_take_two_step():
    r, w, b = _setup()
    # Already-moved pawn (normally locked to 1 square) gets to step 2 with
    # the bonus, since the card "lets it move twice."
    r.board.place("a4", Piece("white", "pawn", has_moved=True))
    events, err = _play(r, "white", "spell_extra_pawn_move", [{"square": "a4"}])
    assert err is None, err
    _, mv_err = r.make_move("white", "a4", "a6", promote=None)
    assert mv_err is None, mv_err
    assert r.board.at("a6").kind == "pawn"


def test_spell_extra_pawn_move_does_not_grant_triple_step():
    r, w, b = _setup()
    r.board.place("a2", Piece("white", "pawn"))  # starting rank, can already 2-step
    _, err = _play(r, "white", "spell_extra_pawn_move", [{"square": "a2"}])
    assert err is None
    # a5 would be a 3-step — disallowed under the cap.
    _, mv_err = r.make_move("white", "a2", "a5", promote=None)
    assert mv_err is not None


# ---- 2g ----

def test_spell_pawn_back_or_two_moves_backward():
    r, w, b = _setup()
    r.board.place("a4", Piece("white", "pawn", has_moved=True))
    events, err = _play(r, "white", "spell_pawn_back_or_two_moves",
                        targets=[], modal="Backward")
    assert err is None, err
    # White pawn going back from a4 → a3
    _, err = r.make_move("white", "a4", "a3", None)
    assert err is None


def test_spell_pawn_back_or_two_moves_two_pawns_mode():
    r, w, b = _setup()
    r.board.place("a2", Piece("white", "pawn"))
    r.board.place("b2", Piece("white", "pawn"))
    events, err = _play(r, "white", "spell_pawn_back_or_two_moves",
                        targets=[], modal="Two pawns")
    assert err is None
    assert w.pawn_two_moves_armed is True
    assert w.moves_remaining >= 2
    # Place a knight on white's side to confirm non-pawn moves are blocked.
    r.board.place("c1", Piece("white", "knight"))
    _, err2 = r.make_move("white", "c1", "b3", None)
    assert err2 == "must move a pawn this turn"
    # Pawn moves still work
    _, err3 = r.make_move("white", "a2", "a3", None)
    assert err3 is None


def test_spell_play_2_pawns_places_two():
    r, w, b = _setup()
    events, err = _play(r, "white", "spell_play_2_pawns",
                        [{"square": "a2"}, {"square": "b2"}])
    assert err is None
    assert r.board.at("a2").kind == "pawn"
    assert r.board.at("b2").kind == "pawn"


def test_spell_play_2_pawns_rejects_duplicate_square():
    r, w, b = _setup()
    _, err = _play(r, "white", "spell_play_2_pawns",
                   [{"square": "a2"}, {"square": "a2"}])
    assert err is not None


def test_spell_combine_pawns_to_knight():
    r, w, b = _setup()
    for sq in ("a2", "b2", "c2"):
        r.board.place(sq, Piece("white", "pawn"))
    _, err = _play(r, "white", "spell_combine_pawns",
                   [{"square": "a2"}, {"square": "b2"}, {"square": "c2"},
                    {"square": "d2"}],
                   modal="Knight")
    assert err is None
    assert r.board.at("a2") is None
    assert r.board.at("d2").kind == "knight"


# ---- 3g ----

def test_spell_adjacent_en_passant_captures_neighbor():
    r, w, b = _setup()
    r.board.place("e4", Piece("white", "pawn", has_moved=True))
    r.board.place("f4", Piece("black", "knight"))
    pre_moves = w.moves_remaining
    _, err = _play(r, "white", "spell_adjacent_en_passant",
                   [{"square": "e4"}, {"square": "f4"}])
    assert err is None
    assert r.board.at("f4") is None
    assert w.moves_remaining == pre_moves - 1


def test_spell_remove_pawn_draw_3():
    r, w, b = _setup()
    r.board.place("a2", Piece("white", "pawn"))
    pre = len(w.hand)
    _, err = _play(r, "white", "spell_remove_pawn_draw_3", [{"square": "a2"}])
    assert err is None
    assert r.board.at("a2") is None
    # Net: pre + 1 (give) - 1 (play) + 3 (draw) = pre + 3.
    assert len(w.hand) - pre == 3


def test_spell_modular_board_sets_flag_and_blocks_king_capture():
    r, w, b = _setup()
    _, err = _play(r, "white", "spell_modular_board")
    assert err is None
    assert w.modular_board_this_turn is True
    assert w.cannot_capture_king_this_turn is True
    # Place an aligned attacker; verify king capture is rejected.
    r.board.place("e2", Piece("white", "queen"))
    _, err2 = r.make_move("white", "e2", "e8", None)
    assert err2 == "cannot capture king this turn"


# ---- 4g ----

def test_spell_tax_opponent():
    r, w, b = _setup()
    _, err = _play(r, "white", "spell_tax_opponent")
    assert err is None
    assert b.spell_tax_next_turn == 3
    r.make_move("white", "e1", "e2", None)
    r.end_turn("white")
    assert b.spell_tax == 3


def test_spell_pawn_to_rook():
    r, w, b = _setup()
    r.board.place("a2", Piece("white", "pawn"))
    _, err = _play(r, "white", "spell_pawn_to_rook",
                   [{"square": "a2"}, {"square": "b1"}])
    assert err is None
    assert r.board.at("a2") is None
    assert r.board.at("b1").kind == "rook"


def test_spell_remove_minor_either_side():
    r, w, b = _setup()
    r.board.place("e4", Piece("black", "bishop"))
    _, err = _play(r, "white", "spell_remove_minor", [{"square": "e4"}])
    assert err is None
    assert r.board.at("e4") is None


# ---- 5g ----

def test_spell_forced_promotion_replaces_pawn():
    r, w, b = _setup()
    r.board.place("a2", Piece("white", "pawn"))
    # Caster plays the card; resolution stalls until opponent picks.
    _, err = _play(r, "white", "spell_forced_promotion",
                   [{"square": "a2"}], modal=None)
    assert err is None
    assert r.board.at("a2").kind == "pawn"  # not yet resolved
    assert r.pending_card_play is not None
    # Opponent picks Knight.
    events, err = r.apply_forced_promotion_pick("black", "Knight")
    assert err is None
    assert r.board.at("a2").kind == "knight"
    assert r.board.at("a2").color == "white"


def test_spell_discard_draw_discards_then_draws():
    r, w, b = _setup()
    # Pad hand with three identifiable cards.
    for cid in ("piece_pawn", "piece_pawn", "piece_pawn"):
        w.hand.append(Card(instance_id=f"x_{cid}_{len(w.hand)}", defn=CARDS_BY_ID[cid]))
    pre_hand = len(w.hand)
    pre_deck = len(w.deck)
    _, err = _play(r, "white", "spell_discard_draw",
                   [{"slots": [0, 1]}])
    assert err is None
    # Net: +1 (give) - 1 (play) - 2 (discard) + 2 (draw) = 0
    assert len(w.hand) == pre_hand
    assert len(w.deck) == pre_deck - 2


def test_spell_teleport_moves_piece_and_counts_as_move():
    r, w, b = _setup()
    r.board.place("a1", Piece("white", "rook"))
    pre_moves = w.moves_remaining
    _, err = _play(r, "white", "spell_teleport",
                   [{"square": "a1"}, {"square": "d4"}])
    assert err is None
    assert r.board.at("a1") is None
    assert r.board.at("d4").kind == "rook"
    assert w.moves_remaining == pre_moves - 1


# ---- 6g ----

def test_spell_king_to_center_moves_king():
    r, w, b = _setup()
    _, err = _play(r, "white", "spell_king_to_center",
                   [{"square": "e1"}, {"square": "e4"}])
    assert err is None
    assert r.board.at("e1") is None
    assert r.board.at("e4").kind == "king"


def test_spell_draw_double_doubles_hand():
    r, w, b = _setup()
    # Pad to a known hand size.
    while len(w.hand) > 0:
        w.hand.pop()
    w.hand.append(Card(instance_id="z", defn=CARDS_BY_ID["piece_pawn"]))
    pre_deck = len(w.deck)
    _, err = _play(r, "white", "spell_draw_double")
    assert err is None
    # _give adds the spell card; play_card pops it; then hand_size=1 ("z") so
    # draw 1 more.
    assert len(w.hand) == 2
    assert len(w.deck) == pre_deck - 1


def test_spell_pawns_to_queen_requires_five():
    r, w, b = _setup()
    for sq in ("a2", "b2", "c2", "d2"):
        r.board.place(sq, Piece("white", "pawn"))
    _, err = _play(r, "white", "spell_pawns_to_queen", [{"square": "f1"}])
    assert err is not None  # only 4 pawns
    r.board.place("e2", Piece("white", "pawn"))
    _, err = _play(r, "white", "spell_pawns_to_queen", [{"square": "f1"}])
    assert err is None
    for sq in ("a2", "b2", "c2", "d2", "e2"):
        assert r.board.at(sq) is None
    assert r.board.at("f1").kind == "queen"


# ---- 7g ----

def test_spell_material_to_queen_sums_exactly_seven():
    r, w, b = _setup()
    # 2 knights (2+2) + 1 bishop (3) = 7
    r.board.place("a1", Piece("white", "knight"))
    r.board.place("b1", Piece("white", "knight"))
    r.board.place("c1", Piece("white", "bishop"))
    _, err = _play(r, "white", "spell_material_to_queen",
                   [{"square": "d1", "sac_squares": ["a1", "b1", "c1"]}])
    assert err is None
    assert r.board.at("a1") is None
    assert r.board.at("d1").kind == "queen"


def test_spell_material_to_queen_wrong_sum_rejected():
    r, w, b = _setup()
    r.board.place("a1", Piece("white", "knight"))
    _, err = _play(r, "white", "spell_material_to_queen",
                   [{"square": "d1", "sac_squares": ["a1"]}])
    assert err is not None


def test_spell_convert_piece_flips_color():
    r, w, b = _setup()
    r.board.place("e4", Piece("black", "knight"))
    pre_moves = w.moves_remaining
    _, err = _play(r, "white", "spell_convert_piece", [{"square": "e4"}])
    assert err is None
    assert r.board.at("e4").color == "white"
    assert r.board.at("e4").kind == "knight"
    assert w.moves_remaining == pre_moves - 1


def test_spell_triple_move_sets_three_moves_no_king_cap():
    r, w, b = _setup()
    _, err = _play(r, "white", "spell_triple_move")
    assert err is None
    assert w.moves_remaining == 3
    assert w.cannot_capture_king_this_turn is True


# ---- 8g ----

def test_spell_rook_and_minor_places_two():
    r, w, b = _setup()
    _, err = _play(r, "white", "spell_rook_and_minor",
                   [{"square": "a1"}, {"square": "b1"}], modal="Bishop")
    assert err is None
    assert r.board.at("a1").kind == "rook"
    assert r.board.at("b1").kind == "bishop"


def test_spell_deploy_enemy_rank_pawn_on_rank7():
    r, w, b = _setup()
    _, err = _play(r, "white", "spell_deploy_enemy_rank",
                   [{"square": "a7"}], modal="Pawn")
    assert err is None
    assert r.board.at("a7").kind == "pawn"


def test_spell_eight_or_eight_pawn_mode():
    r, w, b = _setup()
    # Pawns deploy on ranks 2-3 for white.
    targets = [{"square": s} for s in ("a2", "b2", "c2", "d2")]
    _, err = _play(r, "white", "spell_eight_or_eight", targets, modal="Pawns")
    assert err is None
    for sq in ("a2", "b2", "c2", "d2"):
        assert r.board.at(sq).kind == "pawn"


def test_spell_eight_or_eight_material_mode_must_total_8():
    r, w, b = _setup()
    # bishop + bishop + rook = 3+3+5? no, target is exactly 8.
    # knight(2) + bishop(3) + rook... = 10. So use bishop(3)+bishop(3)+pawn? pawn not allowed.
    # Use queen(8) alone, or 5+3 = rook+bishop, or 5+2+? not 8. Use 5+3.
    targets = [
        {"square": "a1", "piece_kind": "rook"},
        {"square": "b1", "piece_kind": "bishop"},
    ]
    _, err = _play(r, "white", "spell_eight_or_eight", targets, modal="Material")
    assert err is None
    assert r.board.at("a1").kind == "rook"
    assert r.board.at("b1").kind == "bishop"


# ---- 9g ----

def test_spell_wipe_type_removes_all_of_kind():
    r, w, b = _setup()
    r.board.place("a4", Piece("white", "knight"))
    r.board.place("h5", Piece("black", "knight"))
    r.board.place("d4", Piece("white", "bishop"))
    _, err = _play(r, "white", "spell_wipe_type", modal="Knight")
    assert err is None
    assert r.board.at("a4") is None
    assert r.board.at("h5") is None
    assert r.board.at("d4").kind == "bishop"


def test_spell_draw_full_no_move_blocks_movement():
    r, w, b = _setup()
    # Shrink the hand so we actually draw.
    w.hand.clear()
    _, err = _play(r, "white", "spell_draw_full_no_move")
    assert err is None
    assert w.no_chess_move_this_turn is True
    assert w.moves_remaining == 0
    # Hand drawn to HAND_CAP
    assert len(w.hand) == HAND_CAP


def test_spell_choose_opp_move_immediate():
    r, w, b = _setup()
    # Give black a piece so there's something to forcibly move.
    r.board.place("a7", Piece("black", "pawn"))
    _, err = _play(r, "white", "spell_choose_opp_move")
    assert err is None
    # Prompt is for the caster (white), shown immediately during white's turn.
    cps = [p for p in r.pending_prompts.values() if p.kind == "choose_opp_move"]
    assert cps and cps[0].seat == "white"
    # White (still active) picks a move on black's behalf — applies right now.
    assert r.active_seat == "white"
    events, err2 = r.apply_opp_chosen_move("white", "a7", "a6", None)
    assert err2 is None
    assert r.board.at("a6").kind == "pawn"
    # Active seat unchanged — white continues their turn.
    assert r.active_seat == "white"


# ---- 10g ----

def test_spell_draw_rook_extra_grants_move_and_rook():
    r, w, b = _setup()
    pre_moves = w.moves_remaining
    pre_hand = len(w.hand)
    _, err = _play(r, "white", "spell_draw_rook_extra", [{"square": "a1"}])
    assert err is None
    assert r.board.at("a1").kind == "rook"
    assert w.moves_remaining == pre_moves + 1
    assert w.cannot_capture_king_this_turn is True
    # +1 (give), -1 (play), +4 (draw) = +4 from pre
    assert len(w.hand) == pre_hand + 4


def test_spell_quad_deploy_four_kinds():
    r, w, b = _setup()
    # knight/bishop/rook go in back-2 (rank 1-2), pawn in pawn-zone (rank 2-3).
    _, err = _play(r, "white", "spell_quad_deploy",
                   [{"square": "a1"}, {"square": "b1"},
                    {"square": "c1"}, {"square": "d2"}])
    assert err is None
    assert r.board.at("a1").kind == "knight"
    assert r.board.at("b1").kind == "bishop"
    assert r.board.at("c1").kind == "rook"
    assert r.board.at("d2").kind == "pawn"


def test_spell_extra_turn_replays_same_seat():
    r, w, b = _setup()
    _, err = _play(r, "white", "spell_extra_turn")
    assert err is None
    assert w.extra_turn_queued is True
    r.make_move("white", "e1", "e2", None)  # must-move
    r.end_turn("white")
    assert r.active_seat == "white"
    assert w.cannot_capture_king_this_turn is True


def test_spell_queen_and_strip_places_queen_and_removes_material():
    r, w, b = _setup()
    r.board.place("a7", Piece("black", "knight"))   # 2
    r.board.place("b7", Piece("black", "pawn"))     # 1
    _, err = _play(r, "white", "spell_queen_and_strip",
                   [{"square": "a1", "strip_squares": ["a7", "b7"]}])
    assert err is None
    assert r.board.at("a1").kind == "queen"
    assert r.board.at("a7") is None
    assert r.board.at("b7") is None


def test_spell_queen_and_strip_rejects_over_4():
    r, w, b = _setup()
    r.board.place("a7", Piece("black", "rook"))  # 5
    _, err = _play(r, "white", "spell_queen_and_strip",
                   [{"square": "a1", "strip_squares": ["a7"]}])
    assert err is not None


def test_spell_echo_steals_one_card():
    r, w, b = _setup()
    # Give opp a known card at slot 0.
    b.hand.insert(0, Card(instance_id="t", defn=CARDS_BY_ID["piece_queen"]))
    pre_w_hand = len(w.hand)
    pre_b_hand = len(b.hand)
    _, err = _play(r, "white", "spell_echo")
    assert err is None
    # Echo is now mid-resolution, waiting for caster to pick a slot.
    assert r.pending_card_play is not None
    assert r.pending_card_play.card.defn.id == "spell_echo"
    # Caster picks slot 0 (the queen).
    events, err2 = r.apply_echo_pick("white", 0)
    assert err2 is None
    assert r.pending_card_play is None
    assert len(b.hand) == pre_b_hand - 1
    assert len(w.hand) == pre_w_hand + 1
    assert w.hand[-1].defn.id == "piece_queen"


def test_spell_free_pieces_zero_cost_pieces_this_turn():
    r, w, b = _setup()
    _, err = _play(r, "white", "spell_free_pieces")
    assert err is None
    assert w.pieces_free_this_turn is True
    # Now we can play a queen (cost 8) for 0 gold.
    w.gold = 0
    _give(w, "piece_queen")
    events, err2 = r.play_card("white", 0, [{"square": "a1"}], None, [])
    assert err2 is None
    assert r.board.at("a1").kind == "queen"


# ---- Modular board interaction with movement ----

def test_modular_board_lets_rook_wrap_in_make_move():
    r, w, b = _setup()
    r.board.place("a4", Piece("white", "rook"))
    _, err = _play(r, "white", "spell_modular_board")
    assert err is None
    _, err2 = r.make_move("white", "a4", "h4", None)
    assert err2 is None
    assert r.board.at("h4").kind == "rook"


# ---- Sanity: every effect_key resolves without NotImplementedError ----

def test_no_card_remains_a_stub():
    # Probe each effect via its happy path being EXERCISED above. As a
    # belt-and-braces guard, confirm effects.py has no NotImplementedError
    # paths reachable for any declared card.
    import inspect

    from src.chess import effects as eff
    src = inspect.getsource(eff)
    # Only the catch-all at the bottom is allowed.
    assert src.count("raise NotImplementedError") == 1
