import random

from src.cards import RANKS, SUITS, deal_four, new_deck


def test_deck_has_52_unique_cards():
    deck = new_deck()
    assert len(deck) == 52
    assert len({(c.rank, c.suit) for c in deck}) == 52
    assert {c.suit for c in deck} == set(SUITS)
    assert {c.rank for c in deck} == set(RANKS)


def test_deal_four_partitions_full_deck():
    rng = random.Random(42)
    hands = deal_four(rng)
    assert len(hands) == 4
    assert all(len(h) == 13 for h in hands)
    flat = {(c.rank, c.suit) for h in hands for c in h}
    assert len(flat) == 52


def test_deal_four_is_shuffled():
    rng_a = random.Random(1)
    rng_b = random.Random(2)
    a = deal_four(rng_a)
    b = deal_four(rng_b)
    assert a != b
