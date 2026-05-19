from collections import Counter

from src.chess.cards import CARD_DEFS, total_card_count
from src.chess.deck import build_deck


# CARDS.md is internally inconsistent: it claims "deck has 102 cards" /
# "Spell cards (54 total)" but the per-tier listing sums to 48 pieces +
# 60 spells = 108. The detailed listing is more granular (every card has
# an explicit copy count) so we honor that and document the gap. Phase 2
# can reconcile if David picks a different resolution.
EXPECTED_TOTAL = 108


def test_total_count_matches_per_card_sum():
    assert total_card_count() == EXPECTED_TOTAL


def test_built_deck_has_expected_size():
    deck = build_deck()
    assert len(deck) == EXPECTED_TOTAL


def test_per_definition_counts_match_registry():
    deck = build_deck()
    by_id = Counter(c.id for c in deck)
    for defn in CARD_DEFS:
        assert by_id[defn.id] == defn.copies, defn.id


def test_piece_subset_counts():
    # Per CARDS.md: 24 pawn + 6 knight + 6 bishop + 6 rook + 4 queen + 2 any
    expected = {
        "piece_pawn": 24, "piece_knight": 6, "piece_bishop": 6,
        "piece_rook": 5, "piece_queen": 4, "piece_any": 2,
    }
    # NOTE intentional: rook is 6 per CARDS.md, not 5. Use registry directly.
    expected["piece_rook"] = 6
    by_id = Counter(c.id for c in build_deck())
    for k, v in expected.items():
        assert by_id[k] == v


def test_two_decks_are_independent():
    d1 = build_deck()
    d2 = build_deck()
    # Different instance_ids despite same card set
    ids1 = {c.instance_id for c in d1}
    ids2 = {c.instance_id for c in d2}
    assert ids1.isdisjoint(ids2)
