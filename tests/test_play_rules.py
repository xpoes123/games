"""Rule enforcement for the play phase: turn, follow-suit, trump break."""
from src.cards import Card
from src.rooms import Bid, Phase, Table


def _ready_table(trump_strain: str = "H", declarer: int = 0) -> Table:
    """Build a Table that's mid-PLAYING with a known trump and known hands."""
    t = Table()
    # Hand-craft hands so tests are deterministic.
    t.hands = [[] for _ in range(4)]
    t.phase = Phase.PLAYING
    t.contract = Bid(seat=declarer, level=1, strain=trump_strain)
    t.declarer = declarer
    t.turn = declarer
    t.current_trick = []
    t.tricks_won = [0, 0, 0, 0]
    t.trump_broken = False
    return t


def test_must_follow_suit_when_able():
    t = _ready_table(trump_strain="H")
    t.hands[0] = [Card("A", "S"), Card("K", "C")]
    t.hands[1] = [Card("Q", "S"), Card("J", "C")]  # has spades, must follow
    t.hands[2] = [Card("T", "S")]
    t.hands[3] = [Card("9", "S")]
    # Seat 0 leads A♠
    card, err = t.play_card(0, "A", "S")
    assert err is None and card is not None
    # Seat 1 has Q♠ — must follow with spade. Try to play J♣ — illegal.
    card, err = t.play_card(1, "J", "C")
    assert card is None and err is not None and "follow" in err.lower()
    # Q♠ is legal.
    card, err = t.play_card(1, "Q", "S")
    assert err is None


def test_can_discard_when_no_led_suit():
    t = _ready_table(trump_strain="H")
    t.hands[0] = [Card("A", "S")]
    t.hands[1] = [Card("J", "C"), Card("T", "D")]  # no spades — may discard
    t.hands[2] = []
    t.hands[3] = []
    t.play_card(0, "A", "S")
    card, err = t.play_card(1, "J", "C")
    assert err is None and card is not None


def test_trump_cannot_be_led_until_broken():
    t = _ready_table(trump_strain="H")
    t.hands[0] = [Card("A", "H"), Card("K", "S")]  # leader has trump + non-trump
    t.hands[1] = [Card("Q", "S")]
    t.hands[2] = [Card("J", "S")]
    t.hands[3] = [Card("T", "S")]
    assert t.turn == 0
    card, err = t.play_card(0, "A", "H")
    assert card is None and err is not None and "trump" in err.lower()
    # Leading a non-trump is fine
    card, err = t.play_card(0, "K", "S")
    assert err is None


def test_can_lead_trump_when_hand_only_has_trump():
    t = _ready_table(trump_strain="H")
    t.hands[0] = [Card("A", "H"), Card("K", "H")]  # only trump
    t.hands[1] = []; t.hands[2] = []; t.hands[3] = []
    card, err = t.play_card(0, "A", "H")
    assert err is None  # forced — nothing else to play


def test_trump_breaks_when_played_off_suit():
    t = _ready_table(trump_strain="H", declarer=0)
    t.hands[0] = [Card("K", "S"), Card("A", "H")]
    t.hands[1] = [Card("Q", "S")]
    t.hands[2] = [Card("J", "D"), Card("2", "H")]  # no spades, has trump
    t.hands[3] = [Card("T", "S")]
    t.play_card(0, "K", "S")            # lead spade
    t.play_card(1, "Q", "S")            # follow
    assert not t.trump_broken
    card, err = t.play_card(2, "2", "H")  # trump on a spade trick
    assert err is None
    assert t.trump_broken


def test_trump_can_be_led_after_break():
    t = _ready_table(trump_strain="H")
    t.hands[0] = [Card("A", "H"), Card("K", "S")]
    t.trump_broken = True  # pretend it's already broken
    card, err = t.play_card(0, "A", "H")
    assert err is None


def test_not_your_turn():
    t = _ready_table()
    t.hands[0] = [Card("A", "S")]
    t.hands[1] = [Card("K", "S")]
    t.turn = 0
    card, err = t.play_card(1, "K", "S")
    assert card is None and "turn" in err.lower()


def test_card_must_be_in_hand():
    t = _ready_table()
    t.hands[0] = [Card("A", "S")]
    t.turn = 0
    card, err = t.play_card(0, "K", "S")
    assert card is None and "hold" in err.lower()
