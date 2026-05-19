from src.chess.board import Board, Move, Piece, back_two_ranks, opponent_2nd_rank


def test_starting_board_has_only_kings():
    b = Board.starting()
    assert b.at("e1").kind == "king" and b.at("e1").color == "white"
    assert b.at("e8").kind == "king" and b.at("e8").color == "black"
    assert len(b.squares) == 2


def test_place_and_remove_piece():
    b = Board.starting()
    b.place("a2", Piece("white", "pawn"))
    assert b.at("a2").kind == "pawn"
    b.remove("a2")
    assert b.at("a2") is None


def test_pawn_single_and_double_step():
    b = Board.starting()
    b.place("a2", Piece("white", "pawn"))
    dests = b.legal_destinations("a2")
    assert "a3" in dests
    assert "a4" in dests
    # Blocked by piece directly ahead
    b.place("a3", Piece("white", "pawn"))
    dests = b.legal_destinations("a2")
    assert "a3" not in dests
    assert "a4" not in dests


def test_pawn_diagonal_capture():
    b = Board.starting()
    b.place("e4", Piece("white", "pawn", has_moved=True))
    b.place("d5", Piece("black", "pawn"))
    dests = b.legal_destinations("e4")
    assert "d5" in dests   # capture
    assert "f5" not in dests  # no friendly there
    assert "e5" in dests   # forward 1


def test_knight_L_moves_from_b1():
    b = Board.starting()
    b.place("b1", Piece("white", "knight"))
    dests = set(b.legal_destinations("b1"))
    assert "a3" in dests and "c3" in dests and "d2" in dests


def test_bishop_diagonal_through_empty():
    b = Board.starting()
    b.place("c1", Piece("white", "bishop"))
    dests = set(b.legal_destinations("c1"))
    # NE ray: d2 e3 f4 g5 h6
    assert "d2" in dests and "h6" in dests
    # NW ray: b2 a3
    assert "a3" in dests


def test_rook_ray_blocks_on_friendly():
    b = Board.starting()
    b.place("a1", Piece("white", "rook"))
    b.place("a4", Piece("white", "pawn"))
    dests = set(b.legal_destinations("a1"))
    assert "a2" in dests and "a3" in dests
    assert "a4" not in dests
    assert "a5" not in dests


def test_queen_ray_captures_enemy():
    b = Board.starting()
    b.place("d1", Piece("white", "queen"))
    b.place("d5", Piece("black", "pawn"))
    dests = set(b.legal_destinations("d1"))
    assert "d4" in dests and "d5" in dests
    assert "d6" not in dests  # blocked by capture


def test_king_capture_marks_event():
    b = Board.starting()
    b.place("e2", Piece("white", "queen"))
    # White queen takes the e8 king
    dests = b.legal_destinations("e2")
    assert "e8" in dests
    evt = b.apply_move(Move("e2", "e8"))
    assert evt["king_captured"] is True
    assert evt["captured_kind"] == "king"


def test_modular_board_wraps_rook():
    b = Board()
    b.place("a4", Piece("white", "rook"))
    dests = b.legal_destinations("a4", modular=True)
    # Wraps around left edge to h-file
    assert "h4" in dests
    # And vertical wrap
    assert "a8" in dests
    assert "a1" in dests


def test_modular_board_wraps_knight():
    b = Board()
    b.place("a1", Piece("white", "knight"))
    dests = set(b.legal_destinations("a1", modular=True))
    # Normally only b3 / c2; with wrap, also a-file - 1 = h-file.
    assert "h3" in dests or "h2" in dests  # at least one wrap target


def test_pawn_promotion_default_queen_via_apply():
    b = Board()
    b.place("a7", Piece("white", "pawn", has_moved=True))
    # apply_move with promote=queen
    evt = b.apply_move(Move("a7", "a8", promote="queen"))
    assert evt["promoted"] == "queen"
    assert b.at("a8").kind == "queen"


def test_back_two_ranks_helpers():
    w = set(back_two_ranks("white"))
    b = set(back_two_ranks("black"))
    assert "a1" in w and "h2" in w and "a3" not in w
    assert "a7" in b and "h8" in b and "a6" not in b


def test_opponent_2nd_rank_helper():
    assert "a7" in opponent_2nd_rank("white")
    assert "a2" in opponent_2nd_rank("black")
