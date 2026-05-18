from src.cards import Card
from src.rooms import Phase, Table, bid_value


def test_bid_value_ordering():
    # 1NT < 1C < 1D < 1H < 1S < 2NT < 2C ...
    assert bid_value(1, "NT") < bid_value(1, "C")
    assert bid_value(1, "C") < bid_value(1, "D") < bid_value(1, "H") < bid_value(1, "S")
    assert bid_value(1, "S") < bid_value(2, "NT")
    assert bid_value(7, "S") == 7 * 5 + 4  # maximum bid
    assert bid_value(1, "NT") == 1 * 5 + 0  # minimum bid


def _seat_table(n: int) -> Table:
    """Build a table with n synthetic players using shim sockets."""
    t = Table()
    class _Sock:
        async def send_text(self, _): ...
        async def close(self, **_): ...
    for i in range(n):
        from src.rooms import Player
        t.players.append(Player(player_id=str(i), name=f"p{i}", socket=_Sock()))
    return t


def test_dealer_cannot_pass_round_one():
    t = _seat_table(4)
    t.deal()
    assert t.phase == Phase.BIDDING
    assert t.current_bidder == t.dealer
    err = t.submit_pass(t.dealer)
    assert err == "dealer must open"


def test_solo_bidder_auto_pass_to_declarer():
    # Only one human at the table; everyone else auto-passes.
    t = _seat_table(1)
    t.deal()
    assert t.current_bidder == 0
    err = t.submit_bid(0, 1, "NT")
    assert err is None
    # Seats 1, 2, 3 should have auto-passed during _advance_bidder
    assert {1, 2, 3} <= t.passed
    assert t.phase == Phase.CALLING
    assert t.declarer == 0
    assert t.contract.level == 1 and t.contract.strain == "NT"


def test_full_table_bidding_pass_out():
    t = _seat_table(4)
    t.deal()
    assert t.submit_bid(0, 1, "C") is None  # dealer opens 1C
    assert t.submit_pass(1) is None
    assert t.submit_pass(2) is None
    assert t.submit_pass(3) is None
    assert t.phase == Phase.CALLING
    assert t.declarer == 0
    assert t.contract.level == 1 and t.contract.strain == "C"


def test_bid_must_outrank_current():
    t = _seat_table(4)
    t.deal()
    assert t.submit_bid(0, 2, "H") is None  # 2H
    # Seat 1 tries to bid 1S — illegal (1S < 2H)
    err = t.submit_bid(1, 1, "S")
    assert err == "must outbid current"
    # 2S is legal (2S > 2H)
    assert t.submit_bid(1, 2, "S") is None


def test_call_must_not_be_in_declarers_hand():
    t = _seat_table(4)
    t.deal()
    t.submit_bid(0, 1, "NT")
    t.submit_pass(1); t.submit_pass(2); t.submit_pass(3)
    assert t.phase == Phase.CALLING
    own = t.hands[0][0]
    err = t.submit_call_partner(0, own.rank, own.suit)
    assert err == "you hold that card"
    # Pick a card not in declarer's hand
    own_keys = {(c.rank, c.suit) for c in t.hands[0]}
    target = next((r, s) for s in "SHDC" for r in "AKQJT98765432" if (r, s) not in own_keys)
    assert t.submit_call_partner(0, target[0], target[1]) is None
    assert t.phase == Phase.PLAYING
    assert t.partner_seat is not None and t.partner_seat != 0


def test_nt_lead_is_right_of_declarer():
    t = _seat_table(4)
    t.deal()
    t.submit_bid(0, 1, "NT")
    t.submit_pass(1); t.submit_pass(2); t.submit_pass(3)
    own_keys = {(c.rank, c.suit) for c in t.hands[0]}
    rank, suit = next((r, s) for s in "SHDC" for r in "AKQJT98765432" if (r, s) not in own_keys)
    t.submit_call_partner(0, rank, suit)
    # Declarer is 0, right of 0 is 3 (clockwise model: 0→1→2→3→0; right is one back)
    assert t.turn == 3


def test_suited_lead_is_declarer():
    t = _seat_table(4)
    t.deal()
    t.submit_bid(0, 1, "H")
    t.submit_pass(1); t.submit_pass(2); t.submit_pass(3)
    own_keys = {(c.rank, c.suit) for c in t.hands[0]}
    rank, suit = next((r, s) for s in "SHDC" for r in "AKQJT98765432" if (r, s) not in own_keys)
    t.submit_call_partner(0, rank, suit)
    assert t.turn == 0  # declarer leads
