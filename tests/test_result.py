"""End-of-hand result + partner reveal."""
from src.bridge.cards import Card
from src.bridge.rooms import Bid, Phase, Table


def _ready_done_table(declarer: int, partner_seat: int, level: int, strain: str,
                      declarer_tricks: int, partner_tricks: int) -> Table:
    t = Table()
    t.phase = Phase.DONE
    t.declarer = declarer
    t.contract = Bid(seat=declarer, level=level, strain=strain)
    t.partner_seat = partner_seat
    t.partner_card = Card("A", "S")
    t.tricks_won = [0, 0, 0, 0]
    t.tricks_won[declarer] = declarer_tricks
    t.tricks_won[partner_seat] = partner_tricks
    # Distribute remaining tricks among opponents
    remaining = 13 - declarer_tricks - partner_tricks
    opps = [s for s in range(4) if s != declarer and s != partner_seat]
    for i, s in enumerate(opps):
        t.tricks_won[s] = remaining // 2 + (1 if i == 0 and remaining % 2 else 0)
    return t


def test_result_made_exactly():
    t = _ready_done_table(declarer=0, partner_seat=2, level=2, strain="H",
                          declarer_tricks=5, partner_tricks=3)
    # target = 2 + 6 = 8, team = 5+3 = 8, made +0
    r = t.compute_result()
    assert r["target"] == 8
    assert r["team_tricks"] == 8
    assert r["made"] is True
    assert r["delta"] == 0


def test_result_made_with_overtricks():
    t = _ready_done_table(declarer=1, partner_seat=3, level=1, strain="NT",
                          declarer_tricks=6, partner_tricks=4)
    # target = 7, team = 10, +3
    r = t.compute_result()
    assert r["made"] is True and r["delta"] == 3


def test_result_down():
    t = _ready_done_table(declarer=0, partner_seat=2, level=4, strain="S",
                          declarer_tricks=4, partner_tricks=4)
    # target = 10, team = 8, down 2
    r = t.compute_result()
    assert r["made"] is False and r["delta"] == -2


def test_partner_seat_revealed_in_state_at_done():
    t = _ready_done_table(declarer=0, partner_seat=2, level=1, strain="NT",
                          declarer_tricks=7, partner_tricks=0)
    state = t.public_state()
    assert state["phase"] == "done"
    assert state["partner_seat"] == 2
    assert state["result"]["made"] is True


def test_partner_seat_not_revealed_during_playing():
    """While playing, opponents must not see partner_seat in state."""
    t = Table()
    t.phase = Phase.PLAYING
    t.contract = Bid(seat=0, level=1, strain="NT")
    t.declarer = 0
    t.partner_seat = 2
    state = t.public_state()
    assert "partner_seat" not in state
