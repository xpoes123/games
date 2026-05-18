from src.bridge.rooms import MAX_PLAYERS, Phase, Table


def test_empty_table_public_state():
    t = Table()
    state = t.public_state()
    assert state["players"] == []
    assert state["capacity"] == MAX_PLAYERS
    assert state["phase"] == Phase.LOBBY.value
    assert state["tricks_won"] == [0, 0, 0, 0]
    assert state["current_bid"] is None
    assert state["contract"] is None


def test_deal_populates_hands_and_starts_bidding():
    t = Table()
    t.deal()
    assert len(t.hands) == 4
    assert all(len(h) == 13 for h in t.hands)
    assert t.phase == Phase.BIDDING
    assert t.current_bidder == t.dealer


def test_reset_clears_hands_and_returns_to_lobby():
    t = Table()
    t.deal()
    t.reset()
    assert t.hands == []
    assert t.phase == Phase.LOBBY
    assert t.current_bidder is None
    assert t.contract is None
