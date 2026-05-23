"""Admin CLI for the chess persistence store.

Usage (on the box, with the venv active):
    python -m src.chess.admin recompute
    python -m src.chess.admin merge <from_client_id> <to_client_id>
    python -m src.chess.admin players
    python -m src.chess.admin list_games [N]

`merge` re-attributes every game from `from` to `to` and deletes the
`from` players row, then runs `recompute` to re-derive Elo cleanly.
"""
from __future__ import annotations

import sys

from src.chess import persistence
from src.config import settings


def _require_db() -> None:
    if not settings.chess_db_path:
        sys.exit("CHESS_DB_PATH is not set in settings/env.")
    persistence.configure(settings.chess_db_path)


def cmd_recompute(args: list[str]) -> int:
    _require_db()
    res = persistence.recompute_ratings()
    print(f"recomputed: {res['players']} players, {res['games']} games")
    return 0


def cmd_merge(args: list[str]) -> int:
    if len(args) != 2:
        sys.exit("usage: merge <from_client_id> <to_client_id>")
    from_id, to_id = args
    if from_id == to_id:
        sys.exit("from and to must differ")
    _require_db()
    res = persistence.merge_clients(from_id, to_id)
    print(f"merged: {res['games_updated']} game refs updated, "
          f"from-row {'removed' if res['from_row_removed'] else 'not present'}")
    recomp = persistence.recompute_ratings()
    print(f"recomputed: {recomp['players']} players, {recomp['games']} games")
    return 0


def cmd_players(args: list[str]) -> int:
    _require_db()
    rows = persistence.leaderboard(limit=200, min_games=0)
    print(f"{'rating':>6}  {'W':>3} {'L':>3} {'D':>3} {'games':>5}  handle  client_id")
    for r in rows:
        print(f"{r['rating']:>6}  {r['wins']:>3} {r['losses']:>3} {r['draws']:>3} "
              f"{r['games_played']:>5}  {r['handle']:<20}  {r['client_id']}")
    return 0


def cmd_list_games(args: list[str]) -> int:
    _require_db()
    n = int(args[0]) if args else 20
    rows = persistence.list_games(limit=n)
    print(f"{'slug':<7}  {'white':<14} {'black':<14}  {'winner':<6}  reason")
    for r in rows:
        print(f"{r['slug']:<7}  {r['white_name']:<14} {r['black_name']:<14}  "
              f"{(r['winner'] or '-'):<6}  {r['win_reason'] or '-'}")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd, *args = argv
    handlers = {
        "recompute": cmd_recompute,
        "merge": cmd_merge,
        "players": cmd_players,
        "list_games": cmd_list_games,
    }
    fn = handlers.get(cmd)
    if not fn:
        sys.exit(f"unknown command: {cmd}\n{__doc__}")
    return fn(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
