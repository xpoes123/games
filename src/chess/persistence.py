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


DEFAULT_RATING = 1000
K_FACTOR = 32
LEADERBOARD_MIN_GAMES = 3  # filters out single-game noise


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
        # Idempotent ALTERs for ratings columns. SQLite has no IF NOT EXISTS
        # on ADD COLUMN, so probe via PRAGMA.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(games)").fetchall()}
        for col in ("white_rating_pre", "white_rating_post",
                    "black_rating_pre", "black_rating_post"):
            if col not in cols:
                c.execute(f"ALTER TABLE games ADD COLUMN {col} INTEGER")

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
              client_id          TEXT PRIMARY KEY,
              handle             TEXT NOT NULL,
              rating             INTEGER NOT NULL DEFAULT 1000,
              games_played       INTEGER NOT NULL DEFAULT 0,
              wins               INTEGER NOT NULL DEFAULT 0,
              losses             INTEGER NOT NULL DEFAULT 0,
              draws              INTEGER NOT NULL DEFAULT 0,
              created_at         INTEGER NOT NULL,
              last_finalized_at  INTEGER
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS players_rating_idx "
                  "ON players(rating DESC)")


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
    """Mark a game as ended, then update Elo for both sides if both have
    client_ids (and they aren't the same). Skip rating updates silently when
    the inputs make the game un-rateable — the games row still gets winner
    + ended_at."""
    if not enabled() or not slug:
        return
    with _conn() as c:
        row = c.execute(
            "SELECT white_player_id, black_player_id, white_name, black_name, ended_at "
            "FROM games WHERE slug=?", (slug,)).fetchone()
        if row is None:
            return
        if row["ended_at"] is not None:
            return  # already finalized — idempotent guard
        now = int(time.time())
        c.execute(
            "UPDATE games SET winner=?, win_reason=?, ended_at=? WHERE slug=?",
            (winner, win_reason, now, slug),
        )

        # Skip rating math for unrateable games.
        w_id = row["white_player_id"]
        b_id = row["black_player_id"]
        if not w_id or not b_id or w_id == b_id:
            return
        if winner not in ("white", "black"):
            # Draw: count it but don't shift ratings (no in-engine draws yet,
            # but be safe). For draws the formula is score=0.5 each — applying
            # only changes ratings if they differ. Leaving disabled until we
            # actually emit draw outcomes from the engine.
            return

        w_pre = _ensure_player(c, w_id, row["white_name"], now)
        b_pre = _ensure_player(c, b_id, row["black_name"], now)
        w_score = 1.0 if winner == "white" else 0.0
        b_score = 1.0 - w_score
        w_post, b_post = _elo_update(w_pre, b_pre, w_score)
        _apply_player_result(c, w_id, w_post, w_score, now)
        _apply_player_result(c, b_id, b_post, b_score, now)
        c.execute(
            "UPDATE games SET white_rating_pre=?, white_rating_post=?, "
            "black_rating_pre=?, black_rating_post=? WHERE slug=?",
            (w_pre, w_post, b_pre, b_post, slug),
        )


def _elo_update(a_rating: int, b_rating: int, a_score: float,
                k: int = K_FACTOR) -> tuple[int, int]:
    """Symmetric Elo: returns (a_new, b_new). a_score is 1=win, 0.5=draw, 0=loss."""
    ea = 1.0 / (1.0 + 10 ** ((b_rating - a_rating) / 400))
    delta = round(k * (a_score - ea))
    return a_rating + delta, b_rating - delta


def _ensure_player(c: sqlite3.Connection, client_id: str, handle: str,
                   now: int) -> int:
    """Upsert a players row. Returns current rating. Locks handle on insert;
    subsequent calls don't overwrite the stored handle."""
    row = c.execute(
        "SELECT rating FROM players WHERE client_id=?", (client_id,)
    ).fetchone()
    if row is not None:
        return int(row["rating"])
    c.execute(
        "INSERT INTO players (client_id, handle, rating, created_at) "
        "VALUES (?,?,?,?)",
        (client_id, handle or "anon", DEFAULT_RATING, now),
    )
    return DEFAULT_RATING


def _apply_player_result(c: sqlite3.Connection, client_id: str,
                         new_rating: int, score: float, now: int) -> None:
    if score >= 1.0:
        wld = "wins = wins + 1"
    elif score <= 0.0:
        wld = "losses = losses + 1"
    else:
        wld = "draws = draws + 1"
    c.execute(
        f"UPDATE players SET rating=?, games_played = games_played + 1, "
        f"{wld}, last_finalized_at=? WHERE client_id=?",
        (new_rating, now, client_id),
    )


def get_player(client_id: str) -> dict | None:
    if not enabled() or not client_id:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM players WHERE client_id=?", (client_id,)
        ).fetchone()
        return dict(row) if row else None


def leaderboard(limit: int = 50, min_games: int = LEADERBOARD_MIN_GAMES) -> list[dict]:
    if not enabled():
        return []
    with _conn() as c:
        rows = c.execute(
            "SELECT client_id, handle, rating, games_played, wins, losses, draws "
            "FROM players WHERE games_played >= ? "
            "ORDER BY rating DESC, games_played DESC LIMIT ?",
            (min_games, max(1, min(limit, 200))),
        ).fetchall()
        return [dict(r) for r in rows]


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
        "started_at, ended_at, "
        "white_rating_pre, white_rating_post, "
        "black_rating_pre, black_rating_post "
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


# ---- admin: merge + recompute ---------------------------------------------

def merge_clients(from_id: str, to_id: str) -> dict:
    """Re-attribute every game's player_id from `from_id` to `to_id` and drop
    the source players row. Caller should call recompute_ratings() after to
    re-derive Elo cleanly. Returns counts for confirmation output."""
    if not enabled():
        return {"games_updated": 0, "from_row_removed": False}
    with _conn() as c:
        w = c.execute(
            "UPDATE games SET white_player_id=? WHERE white_player_id=?",
            (to_id, from_id)).rowcount
        b = c.execute(
            "UPDATE games SET black_player_id=? WHERE black_player_id=?",
            (to_id, from_id)).rowcount
        removed = c.execute(
            "DELETE FROM players WHERE client_id=?", (from_id,)).rowcount > 0
    return {"games_updated": int(w + b), "from_row_removed": removed}


def recompute_ratings() -> dict:
    """Replay every finalized game in chronological order to re-derive each
    player's rating, W/L/D, games_played. Handles the post-merge case where
    rating arithmetic on the running totals is wrong. Preserves each player's
    stored handle (first finalize-time name)."""
    if not enabled():
        return {"players": 0, "games": 0}
    with _conn() as c:
        # Snapshot handles + created_at so we don't lose them on the rebuild.
        existing = {r["client_id"]: dict(r) for r in c.execute(
            "SELECT client_id, handle, created_at FROM players").fetchall()}
        c.execute("DELETE FROM players")

        ratings: dict[str, int] = {}
        wld: dict[str, dict[str, int]] = {}
        rows = c.execute(
            "SELECT slug, white_player_id, black_player_id, white_name, black_name, "
            "winner, ended_at FROM games "
            "WHERE ended_at IS NOT NULL ORDER BY ended_at ASC"
        ).fetchall()
        for r in rows:
            w_id, b_id = r["white_player_id"], r["black_player_id"]
            winner = r["winner"]
            # Always set pre/post columns to NULL first; we'll fill them only
            # for rateable games.
            c.execute(
                "UPDATE games SET white_rating_pre=NULL, white_rating_post=NULL, "
                "black_rating_pre=NULL, black_rating_post=NULL WHERE slug=?",
                (r["slug"],))
            if not w_id or not b_id or w_id == b_id or winner not in ("white", "black"):
                continue
            for cid, name in ((w_id, r["white_name"]), (b_id, r["black_name"])):
                if cid not in ratings:
                    ratings[cid] = DEFAULT_RATING
                    wld[cid] = {"w": 0, "l": 0, "d": 0, "games": 0,
                                "handle": (existing.get(cid) or {}).get("handle") or name or "anon",
                                "created_at": (existing.get(cid) or {}).get("created_at") or r["ended_at"],
                                "last": r["ended_at"]}
            w_pre, b_pre = ratings[w_id], ratings[b_id]
            w_score = 1.0 if winner == "white" else 0.0
            w_post, b_post = _elo_update(w_pre, b_pre, w_score)
            ratings[w_id], ratings[b_id] = w_post, b_post
            wld[w_id]["games"] += 1
            wld[b_id]["games"] += 1
            wld[w_id]["w" if w_score == 1 else "l"] += 1
            wld[b_id]["w" if w_score == 0 else "l"] += 1
            wld[w_id]["last"] = r["ended_at"]
            wld[b_id]["last"] = r["ended_at"]
            c.execute(
                "UPDATE games SET white_rating_pre=?, white_rating_post=?, "
                "black_rating_pre=?, black_rating_post=? WHERE slug=?",
                (w_pre, w_post, b_pre, b_post, r["slug"]))

        for cid, rating in ratings.items():
            d = wld[cid]
            c.execute(
                "INSERT INTO players (client_id, handle, rating, games_played, "
                "wins, losses, draws, created_at, last_finalized_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (cid, d["handle"], rating, d["games"], d["w"], d["l"], d["d"],
                 d["created_at"], d["last"])
            )
    return {"players": len(ratings), "games": len(rows)}
