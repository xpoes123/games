"""Board state + custom legal-move generation for Hearthstone Chess.

We maintain a dict[square -> Piece] and hand-roll movement. python-chess is
imported for incidental constants but the move logic here is self-contained
because we need: no check enforcement, no castling, king-capture-legal,
modular-board (toroidal) edge wrap, and arbitrary piece placement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

Color = Literal["white", "black"]
Kind = Literal["pawn", "knight", "bishop", "rook", "queen", "king"]

FILES = "abcdefgh"
RANKS = "12345678"

PIECE_GLYPHS = {
    ("white", "king"): "K", ("white", "queen"): "Q", ("white", "rook"): "R",
    ("white", "bishop"): "B", ("white", "knight"): "N", ("white", "pawn"): "P",
    ("black", "king"): "k", ("black", "queen"): "q", ("black", "rook"): "r",
    ("black", "bishop"): "b", ("black", "knight"): "n", ("black", "pawn"): "p",
}


@dataclass
class Piece:
    color: Color
    kind: Kind
    # Pawns track whether they've taken their first move yet; the 2-step
    # option is gated on this rather than on the pawn's rank, since cards can
    # place pawns anywhere.
    has_moved: bool = False
    # Summoning-sickness: a piece placed via a card on the current turn
    # cannot move that same turn. Cleared at end-of-turn for that color.
    placed_this_turn: bool = False


def sq_to_fr(sq: str) -> tuple[int, int]:
    return FILES.index(sq[0]), RANKS.index(sq[1])


def fr_to_sq(f: int, r: int) -> str:
    return FILES[f] + RANKS[r]


def in_bounds(f: int, r: int) -> bool:
    return 0 <= f < 8 and 0 <= r < 8


def wrap_fr(f: int, r: int) -> tuple[int, int]:
    return f % 8, r % 8


@dataclass
class Move:
    src: str
    dst: str
    promote: Kind | None = None


class Board:
    """Mutable board. Movement validation lives on the Board for cohesion
    with the squares dict — rooms.py asks `board.legal_moves(seat)` and
    `board.apply_move(...)`."""

    def __init__(self) -> None:
        self.squares: dict[str, Piece] = {}
        # En-passant target square (the empty square jumped over), if any.
        # Cleared every move.
        self.ep_target: str | None = None

    @classmethod
    def starting(cls) -> "Board":
        b = cls()
        b.squares["e1"] = Piece("white", "king")
        b.squares["e8"] = Piece("black", "king")
        return b

    def at(self, sq: str) -> Piece | None:
        return self.squares.get(sq)

    def place(self, sq: str, piece: Piece) -> None:
        self.squares[sq] = piece

    def remove(self, sq: str) -> Piece | None:
        return self.squares.pop(sq, None)

    def is_empty(self, sq: str) -> bool:
        return sq not in self.squares

    def clear_sickness_for(self, color: Color) -> None:
        for p in self.squares.values():
            if p.color == color:
                p.placed_this_turn = False

    def find_king(self, color: Color) -> str | None:
        for sq, p in self.squares.items():
            if p.kind == "king" and p.color == color:
                return sq
        return None

    def pieces_of(self, color: Color) -> list[tuple[str, Piece]]:
        return [(sq, p) for sq, p in self.squares.items() if p.color == color]

    # ---- movement ----

    def legal_destinations(self, src: str, modular: bool = False) -> list[str]:
        """All squares this piece can legally land on. Includes captures and
        the pseudo-EP square. Does NOT filter for own-king-safety (no check
        enforcement in this variant)."""
        p = self.squares.get(src)
        if p is None:
            return []
        f, r = sq_to_fr(src)
        dests: list[str] = []
        if p.kind == "pawn":
            dests = self._pawn_moves(src, p, f, r, modular)
        elif p.kind == "knight":
            dests = self._knight_moves(p, f, r, modular)
        elif p.kind == "king":
            dests = self._king_moves(p, f, r, modular)
        else:
            rays = _RAYS[p.kind]
            for df, dr in rays:
                dests.extend(self._ray(p, f, r, df, dr, modular))
        return dests

    def all_legal_moves(self, color: Color, modular: bool = False) -> list[Move]:
        out: list[Move] = []
        for sq, p in list(self.squares.items()):
            if p.color != color:
                continue
            for dst in self.legal_destinations(sq, modular):
                # Promotion: pawn reaching last rank generates 4 promote moves.
                if p.kind == "pawn" and _is_promotion_rank(p.color, dst):
                    for promote in ("queen", "rook", "bishop", "knight"):
                        out.append(Move(sq, dst, promote))  # type: ignore[arg-type]
                else:
                    out.append(Move(sq, dst))
        return out

    def apply_move(self, move: Move, modular: bool = False) -> dict:
        """Apply a move that has already been validated by legal_destinations.
        Returns a small event dict describing what happened: captured kind,
        promoted-to, ep flag, king-captured flag."""
        p = self.squares[move.src]
        captured = self.squares.get(move.dst)
        ep_capture_sq: str | None = None

        # En-passant: pawn moves diagonally onto ep_target, capturing the
        # pawn that just double-stepped (which sits on the same file as dst,
        # same rank as src).
        if (
            p.kind == "pawn"
            and self.ep_target == move.dst
            and captured is None
            and move.src[0] != move.dst[0]
        ):
            ep_capture_sq = move.dst[0] + move.src[1]
            captured = self.squares.get(ep_capture_sq)
            if captured is not None:
                del self.squares[ep_capture_sq]

        del self.squares[move.src]
        self.squares[move.dst] = p
        # Track first-move for pawns (used for 2-step option). King/rook
        # has_moved is irrelevant because castling is disabled.
        if p.kind == "pawn":
            # Promotion replaces the piece.
            if move.promote is not None and _is_promotion_rank(p.color, move.dst):
                self.squares[move.dst] = Piece(p.color, move.promote)
            else:
                p.has_moved = True

        # Update ep_target: only a pawn 2-square push sets it.
        prev_ep = self.ep_target
        self.ep_target = None
        if p.kind == "pawn":
            sf, sr = sq_to_fr(move.src)
            df_, dr_ = sq_to_fr(move.dst)
            # 2-square push: rank difference of 2 (mod-board ignored for EP
            # because EP itself is bounded chess).
            if not modular and abs(dr_ - sr) == 2 and sf == df_:
                # ep target is the jumped-over square.
                self.ep_target = fr_to_sq(sf, (sr + dr_) // 2)

        evt = {
            "captured_kind": captured.kind if captured else None,
            "captured_color": captured.color if captured else None,
            "captured_sq": ep_capture_sq if ep_capture_sq else (move.dst if captured else None),
            "king_captured": captured is not None and captured.kind == "king",
            "promoted": (
                move.promote
                if p.kind == "pawn" and move.promote and _is_promotion_rank(p.color, move.dst)
                else None
            ),
            "ep": ep_capture_sq is not None,
            "ep_target": self.ep_target,
            "prev_ep_target": prev_ep,
        }
        return evt

    def to_state(self) -> list[dict]:
        return [
            {"sq": sq, "kind": p.kind, "color": p.color}
            for sq, p in sorted(self.squares.items())
        ]

    # ---- internals ----

    def _step(self, f: int, r: int, df: int, dr: int, modular: bool) -> tuple[int, int] | None:
        nf, nr = f + df, r + dr
        if modular:
            return wrap_fr(nf, nr)
        if in_bounds(nf, nr):
            return nf, nr
        return None

    def _ray(self, p: Piece, f: int, r: int, df: int, dr: int, modular: bool) -> list[str]:
        out: list[str] = []
        cf, cr = f, r
        # Toroidal rays could loop forever; cap at 8 steps in each direction.
        for _ in range(8):
            nxt = self._step(cf, cr, df, dr, modular)
            if nxt is None:
                break
            cf, cr = nxt
            # Catching our own origin = full loop, stop (modular only).
            if (cf, cr) == (f, r):
                break
            target = fr_to_sq(cf, cr)
            occ = self.squares.get(target)
            if occ is None:
                out.append(target)
            elif occ.color != p.color:
                out.append(target)
                break
            else:
                break
        return out

    def _knight_moves(self, p: Piece, f: int, r: int, modular: bool) -> list[str]:
        out: list[str] = []
        for df, dr in _KNIGHT_DELTAS:
            nxt = self._step(f, r, df, dr, modular)
            if nxt is None:
                continue
            target = fr_to_sq(*nxt)
            occ = self.squares.get(target)
            if occ is None or occ.color != p.color:
                out.append(target)
        return out

    def _king_moves(self, p: Piece, f: int, r: int, modular: bool) -> list[str]:
        out: list[str] = []
        for df, dr in _KING_DELTAS:
            nxt = self._step(f, r, df, dr, modular)
            if nxt is None:
                continue
            target = fr_to_sq(*nxt)
            occ = self.squares.get(target)
            if occ is None or occ.color != p.color:
                out.append(target)
        return out

    def _pawn_moves(self, src: str, p: Piece, f: int, r: int, modular: bool) -> list[str]:
        out: list[str] = []
        direction = 1 if p.color == "white" else -1
        # Forward 1
        fwd = self._step(f, r, 0, direction, modular)
        if fwd is not None:
            fwd_sq = fr_to_sq(*fwd)
            if self.squares.get(fwd_sq) is None:
                # Don't add a promotion-rank forward move when modular wrap
                # would put us back on our starting rank — only forbidden in
                # bounded mode.
                out.append(fwd_sq)
                # Forward 2 (only if pawn hasn't moved and forward-1 was empty)
                if not p.has_moved:
                    fwd2 = self._step(fwd[0], fwd[1], 0, direction, modular)
                    if fwd2 is not None:
                        fwd2_sq = fr_to_sq(*fwd2)
                        if self.squares.get(fwd2_sq) is None:
                            out.append(fwd2_sq)
        # Captures
        for df in (-1, 1):
            cap = self._step(f, r, df, direction, modular)
            if cap is None:
                continue
            cap_sq = fr_to_sq(*cap)
            occ = self.squares.get(cap_sq)
            if occ is not None and occ.color != p.color:
                out.append(cap_sq)
            elif self.ep_target == cap_sq:
                out.append(cap_sq)
        return out


_KNIGHT_DELTAS = [(1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)]
_KING_DELTAS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
_RAYS = {
    "rook": [(1, 0), (-1, 0), (0, 1), (0, -1)],
    "bishop": [(1, 1), (1, -1), (-1, 1), (-1, -1)],
    "queen": [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)],
}


def _is_promotion_rank(color: Color, sq: str) -> bool:
    return (color == "white" and sq[1] == "8") or (color == "black" and sq[1] == "1")


def back_two_ranks(color: Color) -> list[str]:
    if color == "white":
        return [fr_to_sq(f, r) for r in (0, 1) for f in range(8)]
    return [fr_to_sq(f, r) for r in (6, 7) for f in range(8)]


def opponent_2nd_rank(color: Color) -> list[str]:
    # White's "opponent's 2nd/7th": rank 7 (index 6). Black: rank 2 (index 1).
    r = 6 if color == "white" else 1
    return [fr_to_sq(f, r) for f in range(8)]


CENTRAL_4 = ["d4", "d5", "e4", "e5"]
