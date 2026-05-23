"""SQLite persistence for completed Hearthstone Chess games.

Disabled unless `configure(db_path)` is called with a path. Tests run without
a path and all functions become no-ops.

Schema: one row per game, with the full event log stored as JSON. Replay is
driven by re-applying events client-side from the stored setups, so the row
is self-contained — no foreign keys, no auxiliary tables.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time

# Crockford-style base32 (no 0/O/1/I/L) — 32 chars, 6 picks = ~1B slugs.
_SLUG_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_SLUG_LEN = 6

_DB_PATH: str | None = None


def configure(db_path: str | None) -> None:
    global _DB_PATH
    _DB_PATH = db_path or None
    if _DB_PATH:
        _ensure_schema()


def enabled() -> bool:
    return _DB_PATH is not None


def _conn() -> sqlite3.Connection:
    assert _DB_PATH is not None
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _ensure_schema() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS games (
              slug              TEXT PRIMARY KEY,
              room_code         TEXT NOT NULL,
              white_name        TEXT NOT NULL,
              black_name        TEXT NOT NULL,
              white_player_id   TEXT,
              black_player_id   TEXT,
              winner            TEXT,
              win_reason        TEXT,
              started_at        INTEGER NOT NULL,
              ended_at          INTEGER,
              white_setup       TEXT NOT NULL,
              black_setup       TEXT NOT NULL,
              event_log         TEXT NOT NULL
            )
            """
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS games_ended_at_idx "
            "ON games(ended_at DESC) WHERE ended_at IS NOT NULL"
        )


def _new_slug() -> str:
    return "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(_SLUG_LEN))


def create_game(
    *,
    room_code: str,
    white_name: str,
    black_name: str,
    white_player_id: str | None,
    black_player_id: str | None,
    white_setup: list[dict],
    black_setup: list[dict],
) -> str | None:
    """Insert an in-progress game row. Returns the slug, or None if disabled."""
    if not enabled():
        return None
    now = int(time.time())
    for _ in range(8):
        slug = _new_slug()
        try:
            with _conn() as c:
                c.execute(
                    """
                    INSERT INTO games (
                        slug, room_code, white_name, black_name,
                        white_player_id, black_player_id,
                        started_at, white_setup, black_setup, event_log
                    ) VALUES (?,?,?,?,?,?,?,?,?, '[]')
                    """,
                    (slug, room_code, white_name, black_name,
                     white_player_id, black_player_id, now,
                     json.dumps(white_setup), json.dumps(black_setup)),
                )
            return slug
        except sqlite3.IntegrityError:
            continue
    return None


def append_events(slug: str | None, events: list[dict]) -> None:
    if not enabled() or not slug or not events:
        return
    with _conn() as c:
        row = c.execute("SELECT event_log FROM games WHERE slug=?", (slug,)).fetchone()
        if row is None:
            return
        log = json.loads(row[0])
        log.extend(events)
        c.execute("UPDATE games SET event_log=? WHERE slug=?",
                  (json.dumps(log), slug))


def finalize_game(slug: str | None, winner: str | None, win_reason: str | None) -> None:
    if not enabled() or not slug:
        return
    with _conn() as c:
        c.execute(
            "UPDATE games SET winner=?, win_reason=?, ended_at=? "
            "WHERE slug=? AND ended_at IS NULL",
            (winner, win_reason, int(time.time()), slug),
        )


def get_game(slug: str) -> dict | None:
    if not enabled():
        return None
    with _conn() as c:
        row = c.execute("SELECT * FROM games WHERE slug=?", (slug,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["event_log"] = json.loads(d["event_log"])
        d["white_setup"] = json.loads(d["white_setup"])
        d["black_setup"] = json.loads(d["black_setup"])
        return d


def list_games(*, limit: int = 50, player_id: str | None = None) -> list[dict]:
    """Recently-finished games, newest first. If player_id is given, filter to
    games where that id was on either side."""
    if not enabled():
        return []
    sql = (
        "SELECT slug, room_code, white_name, black_name, "
        "white_player_id, black_player_id, winner, win_reason, "
        "started_at, ended_at "
        "FROM games WHERE ended_at IS NOT NULL"
    )
    args: list = []
    if player_id:
        sql += " AND (white_player_id=? OR black_player_id=?)"
        args.extend([player_id, player_id])
    sql += " ORDER BY ended_at DESC LIMIT ?"
    args.append(max(1, min(limit, 200)))
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
