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
