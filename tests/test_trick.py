from src.cards import Card
from src.rooms import RANK_VALUE, Table, evaluate_trick


def test_highest_of_led_suit_wins():
    plays = [
        (0, Card("5", "S")),
        (1, Card("A", "H")),  # off-suit, doesn't count
        (2, Card("K", "S")),  # highest spade so far
        (3, Card("Q", "S")),
    ]
    assert evaluate_trick(plays) == 2


def test_off_suit_never_wins():
    plays = [
        (0, Card("2", "D")),  # led diamond
        (1, Card("A", "S")),
        (2, Card("A", "H")),
        (3, Card("A", "C")),
    ]
    assert evaluate_trick(plays) == 0


def test_resolve_complete_trick_clears_and_increments():
    t = Table()
    t.deal()
    # Force a known trick rather than relying on the shuffled hands
    t.current_trick = [
        (0, Card("3", "C")),
        (1, Card("K", "C")),
        (2, Card("4", "C")),
        (3, Card("A", "C")),
    ]
    winner = t.resolve_trick_if_complete()
    assert winner == 3
    assert t.current_trick == []
    assert t.tricks_won == [0, 0, 0, 1]


def test_rank_value_orders_ace_high():
    assert RANK_VALUE["A"] > RANK_VALUE["K"] > RANK_VALUE["Q"] > RANK_VALUE["2"]
