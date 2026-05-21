"""Card registry — every card definition from docs/CARDS.md.

The `effect_key` is a string consumed by effects.py. Phase 1 implements the
trivial keys (piece placements, draw 2, gain 2 gold); the rest are stubs.

`targets` is the prompt sequence expected from the client before the effect
can resolve (modal cards declare a `modal` entry instead of / in addition to
a target list). Phase 2 will broaden the target schema as needed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CardType = Literal["piece", "spell"]


@dataclass(frozen=True)
class CardDef:
    id: str
    name: str
    cost: int                       # -1 = variable (Any Piece)
    type: CardType
    copies: int
    effect_key: str
    targets: tuple[str, ...] = ()
    modal: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()      # e.g. ("counts_as_move", "no_king_capture")


PIECE_COSTS = {"pawn": 1, "knight": 2, "bishop": 3, "rook": 5, "queen": 8}


CARD_DEFS: list[CardDef] = [
    # ---- Piece cards (48) ----
    CardDef("piece_pawn", "Pawn", 1, "piece", 24, "place_pawn", ("empty_square_pawn_zone",)),
    CardDef("piece_knight", "Knight", 2, "piece", 6, "place_knight", ("empty_square_back2",)),
    CardDef("piece_bishop", "Bishop", 3, "piece", 6, "place_bishop", ("empty_square_back2",)),
    CardDef("piece_rook", "Rook", 5, "piece", 6, "place_rook", ("empty_square_back2",)),
    CardDef("piece_queen", "Queen", 8, "piece", 4, "place_queen", ("empty_square_back2",)),
    CardDef(
        "piece_any", "Any Piece", -1, "piece", 2, "place_any",
        targets=("empty_square_back2",), modal=("Pawn", "Knight", "Bishop", "Rook", "Queen"),
    ),

    # ---- 1-gold spells (6) ----
    CardDef("spell_extra_pawn_move", "Extra Pawn Step", 1, "spell", 2,
            "spell_extra_pawn_move", ("friendly_pawn",)),
    CardDef("spell_gain_2_gold", "Gain 1 Gold", 0, "spell", 2, "spell_gain_2_gold"),
    CardDef("spell_draw_2", "Draw 2", 1, "spell", 2, "spell_draw_2"),

    # ---- 2-gold (8) ----
    CardDef("spell_pawn_back_or_two_moves", "Pawn Back / Two Pawn Moves", 2, "spell", 2,
            "spell_pawn_back_or_two_moves", modal=("Backward", "Two pawns")),
    CardDef("spell_play_2_pawns", "Play 2 Pawns", 2, "spell", 2, "spell_play_2_pawns",
            ("empty_square_pawn_zone", "empty_square_pawn_zone")),
    CardDef("spell_combine_pawns", "Combine 3 Pawns", 2, "spell", 2, "spell_combine_pawns",
            ("friendly_pawn", "friendly_pawn", "friendly_pawn", "empty_square_back2"),
            modal=("Knight", "Bishop")),

    # ---- 3-gold (6) ----
    CardDef("spell_adjacent_en_passant", "Adjacent En Passant", 3, "spell", 2,
            "spell_adjacent_en_passant", ("friendly_pawn", "any_adjacent_piece"),
            tags=("counts_as_move",)),
    CardDef("spell_remove_pawn_draw_3", "Sacrifice Pawn, Draw 3", 3, "spell", 2,
            "spell_remove_pawn_draw_3", ("friendly_pawn",)),
    CardDef("spell_modular_board", "Modular Board", 3, "spell", 2, "spell_modular_board",
            tags=("no_king_capture",)),

    # ---- 4-gold (8) ----
    CardDef("spell_tax_opponent", "Spell Tax", 4, "spell", 2, "spell_tax_opponent"),
    CardDef("spell_pawn_to_rook", "Pawn into Rook", 4, "spell", 2, "spell_pawn_to_rook",
            ("friendly_pawn", "empty_square_back2")),
    CardDef("spell_remove_minor", "Remove a Minor (Draw 1)", 4, "spell", 2, "spell_remove_minor",
            ("any_knight_or_bishop",)),
    # Opponent picks Knight or Bishop — handled via a pending prompt to the
    # opponent rather than a caster-side modal.
    CardDef("spell_forced_promotion", "Forced Promotion", 4, "spell", 2,
            "spell_forced_promotion", ("friendly_pawn",)),

    # ---- 5-gold (4) ----
    CardDef("spell_discard_draw", "Discard for Draw", 5, "spell", 2, "spell_discard_draw",
            ("discard_subset",)),
    CardDef("spell_teleport", "Teleport", 5, "spell", 2, "spell_teleport",
            ("friendly_piece", "any_empty_square"), tags=("counts_as_move",)),
    CardDef("spell_king_to_center", "King to Center (Draw 1)", 5, "spell", 2, "spell_king_to_center",
            ("any_piece_kind", "empty_square_central4"),
            tags=("counts_as_move", "no_king_capture")),

    # ---- 6-gold (4) ----
    CardDef("spell_draw_double", "Draw Hand Doubled", 6, "spell", 2, "spell_draw_double"),
    CardDef("spell_pawns_to_queen", "5 Pawns into Queen", 6, "spell", 2, "spell_pawns_to_queen",
            ("empty_square_back2",)),

    # ---- 7-gold (4) ----
    CardDef("spell_material_to_queen", "7 Material into Queen", 7, "spell", 2,
            "spell_material_to_queen", ("empty_square_back2",)),
    CardDef("spell_triple_move", "Triple Move", 7, "spell", 2, "spell_triple_move",
            tags=("no_king_capture",)),

    # ---- 8-gold (6) ----
    CardDef("spell_convert_piece", "Convert Piece", 8, "spell", 2, "spell_convert_piece",
            ("enemy_piece",), tags=("counts_as_move",)),
    CardDef("spell_rook_and_minor", "Rook + Minor", 8, "spell", 2, "spell_rook_and_minor",
            ("empty_square_back2", "empty_square_back2"), modal=("Knight", "Bishop")),
    CardDef("spell_deploy_enemy_rank", "Deploy on Enemy Rank", 8, "spell", 2,
            "spell_deploy_enemy_rank", ("empty_square_back2_opp",), modal=("Pawn", "Rook")),
    CardDef("spell_eight_or_eight", "Up to 8 Pawns / 8 Material", 8, "spell", 2,
            "spell_eight_or_eight", modal=("Pawns", "Material")),

    # ---- 9-gold (6) ----
    CardDef("spell_wipe_type", "Wipe Type", 9, "spell", 2, "spell_wipe_type",
            modal=("Pawn", "Knight", "Bishop", "Rook", "Queen"),
            tags=("no_king_capture",)),
    CardDef("spell_draw_full_no_move", "Draw to Full (No Move)", 9, "spell", 2,
            "spell_draw_full_no_move"),
    CardDef("spell_choose_opp_move", "Choose Opponent Move", 9, "spell", 2,
            "spell_choose_opp_move"),

    # ---- 10-gold (6, 1 copy each) ----
    CardDef("spell_draw_rook_extra", "Draw 4 + Rook + Extra Move", 10, "spell", 1,
            "spell_draw_rook_extra", ("empty_square_back2",), tags=("no_king_capture",)),
    CardDef("spell_quad_deploy", "Quadruple Deploy", 10, "spell", 1, "spell_quad_deploy",
            ("empty_square_back2", "empty_square_back2", "empty_square_back2",
             "empty_square_pawn_zone")),
    CardDef("spell_extra_turn", "Extra Turn", 10, "spell", 1, "spell_extra_turn",
            tags=("no_king_capture_bonus_turn",)),
    CardDef("spell_queen_and_strip", "Queen + Strip Material", 10, "spell", 1,
            "spell_queen_and_strip", ("empty_square_back2",)),
    CardDef("spell_echo", "Echo", 10, "spell", 1, "spell_echo"),
    CardDef("spell_free_pieces", "Free Pieces", 10, "spell", 1, "spell_free_pieces"),
]


CARDS_BY_ID: dict[str, CardDef] = {c.id: c for c in CARD_DEFS}


def total_card_count() -> int:
    return sum(c.copies for c in CARD_DEFS)


@dataclass
class Card:
    """A concrete card in a deck/hand (one per copy). The display data lives
    on the underlying CardDef — this layer just gives each instance an id so
    UI can dedupe / animate by slot."""
    instance_id: str
    defn: CardDef

    @property
    def id(self) -> str:
        return self.defn.id

    @property
    def name(self) -> str:
        return self.defn.name

    @property
    def cost(self) -> int:
        return self.defn.cost

    @property
    def type(self) -> CardType:
        return self.defn.type

    def to_json(self, slot: int, playable: bool) -> dict:
        return {
            "slot": slot,
            "card_id": self.id,
            "name": self.name,
            "cost": self.cost,
            "type": self.type,
            "playable": playable,
        }
