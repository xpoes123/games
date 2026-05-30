"""Unit tests for the chess persistence layer.

Each test uses a tmpdir-scoped DB. Disabled (no configure) calls are no-ops
and asserted explicitly so we don't accidentally talk to a global DB."""
from __future__ import annotations

import json
from pathlib import Path

from src.chess import persistence


def _configure(tmp_path: Path) -> str:
    db = tmp_path / "g.db"
    persistence.configure(str(db))
    return str(db)


def teardown_function(_):
    persistence.configure(None)


def test_disabled_when_unconfigured(tmp_path):
    persistence.configure(None)
    assert not persistence.enabled()
    # All write fns should silently no-op.
    slug = persistence.create_game(
        room_code="X", white_name="a", black_name="b",
        white_player_id=None, black_player_id=None,
        white_setup=[], black_setup=[],
    )
    assert slug is None
    persistence.append_events(None, [{"kind": "noop"}])
    persistence.finalize_game(None, "white", "king_capture")
    assert persistence.list_games() == []
    assert persistence.get_game("ANY") is None


def test_create_append_finalize_roundtrip(tmp_path):
    _configure(tmp_path)
    slug = persistence.create_game(
        room_code="ABCD", white_name="alice", black_name="bob",
        white_player_id="cid-a", black_player_id="cid-b",
        white_setup=[{"kind": "pawn", "square": "a2"}],
        black_setup=[{"kind": "queen", "square": "d8"}],
    )
    assert slug and len(slug) == 6

    persistence.append_events(slug, [
        {"kind": "piece_moved", "from": "e1", "to": "e2"},
        {"kind": "card_played", "by": "white", "card_id": "spell_draw_2"},
    ])
    persistence.append_events(slug, [{"kind": "king_captured", "winner": "white"}])
    persistence.finalize_game(slug, "white", "king_capture")

    row = persistence.get_game(slug)
    assert row is not None
    assert row["white_name"] == "alice"
    assert row["black_name"] == "bob"
    assert row["white_player_id"] == "cid-a"
    assert row["winner"] == "white"
    assert row["win_reason"] == "king_capture"
    assert row["ended_at"] is not None
    assert row["white_setup"] == [{"kind": "pawn", "square": "a2"}]
    assert len(row["event_log"]) == 3
    assert row["event_log"][-1]["kind"] == "king_captured"


def test_truncate_events_drops_undone_action(tmp_path):
    """Reproduces the replay corruption: a move is persisted, undone (which
    truncates the log back), then a different move is persisted. The undone
    move must not survive in the event_log — otherwise the replay applies a
    phantom move and pieces vanish."""
    _configure(tmp_path)
    slug = persistence.create_game(
        room_code="X", white_name="a", black_name="b",
        white_player_id=None, black_player_id=None,
        white_setup=[], black_setup=[],
    )
    # Two events already committed this turn.
    persistence.append_events(slug, [
        {"kind": "piece_moved", "from": "e1", "to": "f2"},
        {"kind": "turn_end", "next": "black", "turn_number": 2},
    ])
    # White acts: the phantom move that later gets undone.
    persistence.append_events(slug, [{"kind": "piece_moved", "from": "f3", "to": "f4"}])
    assert len(persistence.get_game(slug)["event_log"]) == 3
    # Undo → truncate back to the pre-action count (2).
    persistence.truncate_events(slug, 2)
    # The real move replaces it.
    persistence.append_events(slug, [{"kind": "piece_moved", "from": "f3", "to": "g3"}])

    log = persistence.get_game(slug)["event_log"]
    assert len(log) == 3
    assert log[-1] == {"kind": "piece_moved", "from": "f3", "to": "g3"}
    assert all(e.get("to") != "f4" for e in log)  # phantom gone

    # Truncating to >= current length is a no-op.
    persistence.truncate_events(slug, 99)
    assert len(persistence.get_game(slug)["event_log"]) == 3


def test_list_games_filters_by_player_id(tmp_path):
    _configure(tmp_path)
    a = persistence.create_game(
        room_code="R1", white_name="alice", black_name="bob",
        white_player_id="cid-a", black_player_id="cid-b",
        white_setup=[], black_setup=[],
    )
    b = persistence.create_game(
        room_code="R2", white_name="carol", black_name="dave",
        white_player_id="cid-c", black_player_id="cid-d",
        white_setup=[], black_setup=[],
    )
    persistence.finalize_game(a, "white", "concede")
    persistence.finalize_game(b, "black", "king_capture")

    all_rows = persistence.list_games()
    assert {r["slug"] for r in all_rows} == {a, b}

    mine = persistence.list_games(player_id="cid-a")
    assert [r["slug"] for r in mine] == [a]

    none = persistence.list_games(player_id="cid-unknown")
    assert none == []


def test_finalize_does_not_overwrite_already_ended(tmp_path):
    """finalize_game is idempotent — double-call shouldn't clobber the first
    winner. (Defends against the corner case where _persist_tick fires twice
    after a concede.)"""
    _configure(tmp_path)
    slug = persistence.create_game(
        room_code="X", white_name="a", black_name="b",
        white_player_id=None, black_player_id=None,
        white_setup=[], black_setup=[],
    )
    persistence.finalize_game(slug, "white", "king_capture")
    persistence.finalize_game(slug, "black", "concede")
    row = persistence.get_game(slug)
    assert row["winner"] == "white"
    assert row["win_reason"] == "king_capture"


def test_list_games_excludes_in_progress(tmp_path):
    _configure(tmp_path)
    persistence.create_game(
        room_code="R", white_name="a", black_name="b",
        white_player_id=None, black_player_id=None,
        white_setup=[], black_setup=[],
    )
    assert persistence.list_games() == []


# ---- Elo ------------------------------------------------------------------

def _seeded_game(room: str, w_id: str, b_id: str, w_name: str, b_name: str,
                 winner: str) -> str:
    slug = persistence.create_game(
        room_code=room, white_name=w_name, black_name=b_name,
        white_player_id=w_id, black_player_id=b_id,
        white_setup=[], black_setup=[],
    )
    persistence.finalize_game(slug, winner, "king_capture")
    return slug


def test_first_game_sets_handle_and_default_rating(tmp_path):
    _configure(tmp_path)
    _seeded_game("R1", "a", "b", "alice", "bob", "white")
    alice = persistence.get_player("a")
    bob = persistence.get_player("b")
    # Equal pre-ratings → winner gets +16, loser -16 at K=32.
    assert alice["rating"] == 1016
    assert bob["rating"] == 984
    assert alice["wins"] == 1 and alice["losses"] == 0
    assert bob["wins"] == 0 and bob["losses"] == 1
    assert alice["handle"] == "alice"
    assert bob["handle"] == "bob"


def test_handle_is_locked_in_after_first_finalize(tmp_path):
    _configure(tmp_path)
    _seeded_game("R1", "a", "b", "alice", "bob", "white")
    # Alice plays again under a different display name.
    _seeded_game("R2", "a", "c", "ALICE-NEW", "carol", "black")
    a = persistence.get_player("a")
    assert a["handle"] == "alice"  # name from the first game, immutable here


def test_self_game_does_not_change_rating(tmp_path):
    _configure(tmp_path)
    slug = persistence.create_game(
        room_code="R", white_name="me", black_name="me",
        white_player_id="same", black_player_id="same",
        white_setup=[], black_setup=[],
    )
    persistence.finalize_game(slug, "white", "concede")
    assert persistence.get_player("same") is None
    # The game row still exists, just with no rating delta.
    row = persistence.get_game(slug)
    assert row["winner"] == "white"
    assert row["white_rating_pre"] is None
    assert row["white_rating_post"] is None


def test_leaderboard_filters_low_game_count(tmp_path):
    _configure(tmp_path)
    # Alice plays 3 games (above threshold), Bob plays 1.
    _seeded_game("R1", "a", "b", "alice", "bob", "white")
    _seeded_game("R2", "a", "c", "alice", "carol", "white")
    _seeded_game("R3", "a", "d", "alice", "dan", "white")
    rows = persistence.leaderboard(limit=10, min_games=3)
    ids = [r["client_id"] for r in rows]
    assert "a" in ids
    assert "b" not in ids  # only 1 game


def test_merge_reattributes_games_and_recompute_fixes_elo(tmp_path):
    _configure(tmp_path)
    # Alice plays under two different client_ids — say she cleared storage.
    _seeded_game("R1", "alice1", "bob", "alice", "bob", "white")  # alice1 wins
    _seeded_game("R2", "alice2", "bob", "alice", "bob", "black")  # alice2 loses
    pre_a1 = persistence.get_player("alice1")
    pre_a2 = persistence.get_player("alice2")
    assert pre_a1["rating"] == 1016
    assert pre_a2["rating"] != pre_a1["rating"]

    res = persistence.merge_clients("alice2", "alice1")
    assert res["games_updated"] == 1
    assert res["from_row_removed"] is True

    persistence.recompute_ratings()

    a1 = persistence.get_player("alice1")
    a2 = persistence.get_player("alice2")
    assert a2 is None  # merged away
    # 2 games (1W, 1L) — pure symmetric, so rating should return to ~1000.
    assert a1["games_played"] == 2
    assert a1["wins"] == 1
    assert a1["losses"] == 1
    # 1016 then loss against 984 → +16 then close-to-zero on parity; net ≈ +16
    # actually after the W (1016 vs 984), L against a higher Bob (1000) → -16.
    # End state should be back to 1000 ± rounding.
    assert abs(a1["rating"] - 1000) <= 2


def test_game_row_carries_rating_delta(tmp_path):
    _configure(tmp_path)
    slug = _seeded_game("R", "a", "b", "alice", "bob", "white")
    row = persistence.get_game(slug)
    assert row["white_rating_pre"] == 1000
    assert row["white_rating_post"] == 1016
    assert row["black_rating_pre"] == 1000
    assert row["black_rating_post"] == 984
