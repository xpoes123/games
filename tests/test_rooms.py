from src.rooms import MAX_PLAYERS, Table


def test_empty_table_public_state():
    t = Table()
    state = t.public_state()
    assert state == {
        "players": [],
        "capacity": MAX_PLAYERS,
        "dealt": False,
        "tricks_won": [0, 0, 0, 0],
    }


def test_deal_populates_hands():
    t = Table()
    t.deal()
    assert len(t.hands) == 4
    assert all(len(h) == 13 for h in t.hands)
    assert t.public_state()["dealt"] is True


def test_reset_clears_hands():
    t = Table()
    t.deal()
    t.reset()
    assert t.hands == []
    assert t.public_state()["dealt"] is False
