"""Shared accounts + game-results store (SQLite), used by every game.

Identity model:
  - Every visitor has a stable anonymous `guest_id` (a cookie). All games are
    saved under the guest_id, logged in or not.
  - Discord OAuth links a guest_id to a `discord_id` (guest_links). Linking is
    how a guest's past games get reconciled onto their account — no game rows
    move, the leaderboard just resolves guest_id → discord_id at read time.
  - Leaderboard groups by "owner": the discord_id if the guest is linked, else
    the guest_id itself (shown as an anonymous/guest entry until they connect).

Matches chess/persistence.py: synchronous sqlite3, configure(path) or in-memory.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager

DIFFICULTIES = ("easy", "medium", "hard", "insane")
_DIFF_RANK = {d: i + 1 for i, d in enumerate(DIFFICULTIES)}

_DB_PATH: str | None = None
_MEM: sqlite3.Connection | None = None


def configure(db_path: str | None) -> None:
    """File path → persistent DB; empty → shared in-memory DB (dev/tests)."""
    global _DB_PATH, _MEM
    _DB_PATH = db_path or None
    if _DB_PATH is None:
        _MEM = sqlite3.connect(":memory:", check_same_thread=False)
        _MEM.row_factory = sqlite3.Row
    _ensure_schema()


@contextmanager
def _cur():
    if _DB_PATH is None:
        c = _MEM
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
    else:
        c = sqlite3.connect(_DB_PATH)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def _ensure_schema() -> None:
    with _cur() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
              discord_id TEXT PRIMARY KEY,
              username   TEXT NOT NULL,
              avatar     TEXT,
              updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS guest_links (
              guest_id   TEXT PRIMARY KEY,
              discord_id TEXT NOT NULL,
              linked_at  INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS games (
              id         INTEGER PRIMARY KEY AUTOINCREMENT,
              game       TEXT NOT NULL,
              guest_id   TEXT NOT NULL,
              name       TEXT NOT NULL,
              won        INTEGER NOT NULL,
              mode       TEXT NOT NULL,
              opponent   TEXT,
              duration_s REAL,
              created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS games_game_idx ON games(game);
            CREATE INDEX IF NOT EXISTS games_guest_idx ON games(guest_id);
            """
        )


# --- writes ---------------------------------------------------------------
def upsert_account(discord_id: str, username: str, avatar: str | None) -> None:
    with _cur() as c:
        c.execute(
            "INSERT INTO accounts (discord_id, username, avatar, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(discord_id) DO UPDATE SET "
            "username=excluded.username, avatar=excluded.avatar, updated_at=excluded.updated_at",
            (discord_id, username, avatar, int(time.time())),
        )


def link_guest(guest_id: str, discord_id: str) -> None:
    """Reconcile this guest's past + future games onto a Discord account."""
    with _cur() as c:
        c.execute(
            "INSERT INTO guest_links (guest_id, discord_id, linked_at) VALUES (?,?,?) "
            "ON CONFLICT(guest_id) DO UPDATE SET discord_id=excluded.discord_id",
            (guest_id, discord_id, int(time.time())),
        )


def record_game(game: str, guest_id: str, name: str, won: bool, mode: str,
                opponent: str | None = None, duration_s: float | None = None) -> None:
    with _cur() as c:
        c.execute(
            "INSERT INTO games (game, guest_id, name, won, mode, opponent, duration_s, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (game, guest_id, name, int(won), mode, opponent, duration_s, int(time.time())),
        )


# --- reads ----------------------------------------------------------------
def _owner_of(guest_id: str, links: dict) -> str:
    return links.get(guest_id) or f"guest:{guest_id}"


def _aggregate(game: str | None):
    """Group game rows by resolved owner; return {owner: stats} + name/avatar maps."""
    with _cur() as c:
        links = {r["guest_id"]: r["discord_id"] for r in c.execute("SELECT * FROM guest_links")}
        accounts = {r["discord_id"]: r for r in c.execute("SELECT * FROM accounts")}
        q = "SELECT * FROM games" + ("" if game is None else " WHERE game=?")
        rows = c.execute(q, () if game is None else (game,)).fetchall()
    stats: dict[str, dict] = {}
    for r in rows:
        owner = _owner_of(r["guest_id"], links)
        s = stats.setdefault(owner, {
            "owner": owner, "wins": 0, "games": 0, "hardest": None,
            "fastest_win": None, "name": r["name"], "last": 0, "is_account": owner in accounts,
        })
        s["games"] += 1
        if r["created_at"] >= s["last"]:
            s["last"] = r["created_at"]
            s["name"] = r["name"]  # most recent guest name
        if r["won"]:
            s["wins"] += 1
            if r["mode"] == "cpu" and r["opponent"] in _DIFF_RANK:
                if s["hardest"] is None or _DIFF_RANK[r["opponent"]] > _DIFF_RANK[s["hardest"]]:
                    s["hardest"] = r["opponent"]
            if r["duration_s"] and (s["fastest_win"] is None or r["duration_s"] < s["fastest_win"]):
                s["fastest_win"] = r["duration_s"]
    # Overlay account display name/avatar onto linked owners.
    for owner, s in stats.items():
        if owner in accounts:
            s["name"] = accounts[owner]["username"]
            s["avatar"] = accounts[owner]["avatar"]
    return stats


def leaderboard(game: str | None = None, limit: int = 25) -> list[dict]:
    stats = _aggregate(game)
    ranked = sorted(
        stats.values(),
        key=lambda s: (_DIFF_RANK.get(s["hardest"], 0), s["wins"], -(s["fastest_win"] or 1e9)),
        reverse=True,
    )
    return ranked[:limit]


def profile(guest_id: str, game: str | None = None) -> dict:
    """Stats (PBs) for whoever owns this guest_id (resolved to account if linked)."""
    with _cur() as c:
        row = c.execute("SELECT discord_id FROM guest_links WHERE guest_id=?", (guest_id,)).fetchone()
    target_owner = (row["discord_id"] if row else None) or f"guest:{guest_id}"
    stats = _aggregate(game)
    return stats.get(target_owner, {
        "owner": target_owner, "wins": 0, "games": 0, "hardest": None, "fastest_win": None,
    })
