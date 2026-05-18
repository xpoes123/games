"""Dealer rotation + cumulative trick totals across hands."""
from src.cards import Card
from src.rooms import Bid, Phase, Table


def _force_done_hand(t: Table, declarer: int, partner_seat: int, level: int, strain: str,
                    tricks_split: list[int]) -> None:
    """Skip bidding/playing — drive the table into a DONE state with given
    tricks per seat by manually filling the last trick to trigger resolve."""
    assert sum(tricks_split) == 13
    t.phase = Phase.PLAYING
    t.declarer = declarer
    t.contract = Bid(seat=declarer, level=level, strain=strain)
    t.partner_seat = partner_seat
    t.partner_card = Card("A", "S")
    # Set all but the final trick directly
    t.tricks_won = list(tricks_split)
    t.tricks_won[0] -= 1  # the 13th trick will be added by resolve
    # Build a final trick where seat 0 wins
    trump = strain if strain != "NT" else None
    t.current_trick = [
        (0, Card("A", "C") if trump != "C" else Card("A", "C")),
        (1, Card("2", "C")),
        (2, Card("3", "C")),
        (3, Card("4", "C")),
    ]
    t.resolve_trick_if_complete()


def test_dealer_rotates_after_hand():
    t = Table()
    t.dealer = 1
    _force_done_hand(t, declarer=1, partner_seat=3, level=2, strain="H",
                     tricks_split=[3, 4, 3, 3])
    assert t.phase == Phase.DONE
    assert t.dealer == 2  # rotated clockwise from 1


def test_total_tricks_accumulate():
    t = Table()
    _force_done_hand(t, declarer=0, partner_seat=2, level=1, strain="NT",
                     tricks_split=[4, 3, 3, 3])
    assert t.total_tricks == [4, 3, 3, 3]
    assert t.hands_played == 1


def test_total_tricks_survive_redeal():
    t = Table()
    _force_done_hand(t, declarer=0, partner_seat=2, level=1, strain="NT",
                     tricks_split=[5, 2, 3, 3])
    # Now deal again — totals must stay, per-hand tricks_won resets
    t.deal()
    assert t.total_tricks == [5, 2, 3, 3]
    assert t.tricks_won == [0, 0, 0, 0]
    assert t.hands_played == 1
    assert t.phase == Phase.BIDDING


def test_full_reset_wipes_totals():
    t = Table()
    _force_done_hand(t, declarer=0, partner_seat=2, level=1, strain="NT",
                     tricks_split=[6, 2, 3, 2])
    t.reset()
    assert t.total_tricks == [0, 0, 0, 0]
    assert t.hands_played == 0
    assert t.dealer == 0
