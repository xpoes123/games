"""Hand ends as soon as the contract outcome is locked in."""
from src.bridge.cards import Card
from src.bridge.rooms import Bid, Phase, Table


def _setup_contract(level: int, strain: str, declarer: int = 0, partner_seat: int = 2) -> Table:
    t = Table()
    t.phase = Phase.PLAYING
    t.declarer = declarer
    t.contract = Bid(seat=declarer, level=level, strain=strain)
    t.partner_seat = partner_seat
    t.partner_card = Card("A", "S")
    t.tricks_won = [0, 0, 0, 0]
    # Give each player one trump-irrelevant card so resolve_trick_if_complete
    # can fire without exercising the rule checks.
    t.hands = [[] for _ in range(4)]
    return t


def _resolve_one_trick(t: Table, winner_seat: int) -> None:
    """Inject a 4-card trick where the chosen seat wins."""
    # Use clubs (no trump for NT) — the winner_seat plays the Ace.
    cards = [
        (s, Card("A" if s == winner_seat else "2", "C"))
        for s in range(4)
    ]
    t.current_trick = cards
    t.resolve_trick_if_complete()


def test_hand_ends_when_declarer_team_makes_1nt():
    # 1NT → target 7. Declarer team needs 7 of 13.
    t = _setup_contract(level=1, strain="NT", declarer=0, partner_seat=2)
    # Win 6 tricks for declarer team; not enough yet
    for _ in range(6):
        _resolve_one_trick(t, winner_seat=0)
    assert t.phase == Phase.PLAYING, "should still be playing after 6/7"
    # 7th declarer trick — contract made, hand ends
    _resolve_one_trick(t, winner_seat=2)
    assert t.phase == Phase.DONE, "should end at 7/7 made"
    assert sum(t.tricks_won) == 7  # not 13 — early termination


def test_hand_ends_when_defenders_set_1nt():
    # 1NT → target 7. Defenders set with 7 tricks.
    t = _setup_contract(level=1, strain="NT", declarer=0, partner_seat=2)
    # Defenders are seats 1 and 3
    for _ in range(7):
        _resolve_one_trick(t, winner_seat=1)
    assert t.phase == Phase.DONE
    assert sum(t.tricks_won) == 7


def test_7nt_plays_all_13_tricks():
    # 7NT → target 13. Hand can only end at 13 tricks.
    t = _setup_contract(level=7, strain="NT", declarer=0, partner_seat=2)
    # Win 12 declarer tricks — still not decided (need 13, defenders need 1)
    for _ in range(12):
        _resolve_one_trick(t, winner_seat=0)
    # Wait — defenders need 14-13=1 trick to set, so after 1 defender trick,
    # the contract is already set. Let me redo this test more carefully:
    # If we give 12 to declarer and they make all 13, ends at 13.
    _resolve_one_trick(t, winner_seat=2)
    assert t.phase == Phase.DONE
    assert sum(t.tricks_won) == 13


def test_7nt_ends_immediately_when_defenders_take_one():
    # 7NT → target 13, defenders need just 1 to set. Hand ends after that.
    t = _setup_contract(level=7, strain="NT", declarer=0, partner_seat=2)
    _resolve_one_trick(t, winner_seat=1)  # first defender trick = set
    assert t.phase == Phase.DONE
    assert sum(t.tricks_won) == 1


def test_4h_made_with_overtricks_possible():
    # 4H → target 10. After declarer wins 10 tricks, hand ends.
    t = _setup_contract(level=4, strain="H", declarer=1, partner_seat=3)
    for _ in range(10):
        _resolve_one_trick(t, winner_seat=1)
    assert t.phase == Phase.DONE
    # team_tricks == 10, delta == 0 (just made — no overtricks counted
    # because we terminated early)
    r = t.compute_result()
    assert r["made"] and r["delta"] == 0


def test_result_correct_when_set_early():
    # 3NT → target 9. After defenders win 5 tricks (14-9), they've set.
    t = _setup_contract(level=3, strain="NT", declarer=0, partner_seat=2)
    for _ in range(5):
        _resolve_one_trick(t, winner_seat=1)
    assert t.phase == Phase.DONE
    r = t.compute_result()
    assert not r["made"] and r["team_tricks"] == 0
