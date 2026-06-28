"""Root FastAPI app — landing page + mounts per game."""
from __future__ import annotations

import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse

from src.bridge.app import app as bridge_app
from src.chess import persistence as chess_persistence
from src.chess.app import app as chess_app
from src.ers.app import app as ers_app
from src.config import settings

# Wire chess game persistence at import time so the schema is ready before
# the first request lands. Empty path → no-op (tests, ephemeral dev).
chess_persistence.configure(settings.chess_db_path or None)

log = logging.getLogger("games")

app = FastAPI(title="games.djiang.xyz", docs_url=None, redoc_url=None)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


LANDING_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>games</title>
  <style>
    :root { color-scheme: dark; --bg: #1a1b26; --fg: #c0caf5; --muted: #565f89;
            --border: #414868; --accent: #7aa2f7; --panel: #24283b; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
           background: var(--bg); color: var(--fg); font-size: 14px; line-height: 1.5; }
    main { max-width: 720px; margin: 0 auto; padding: 3rem 1.25rem; }
    h1 { font-size: 1.4rem; font-weight: 600; margin: 0 0 1.5rem; }
    ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 0.75rem; }
    li a { display: block; padding: 1rem 1.25rem; background: var(--panel);
           border: 1px solid var(--border); color: var(--fg); text-decoration: none; }
    li a:hover { border-color: var(--accent); color: var(--accent); }
    li .name { font-weight: 600; }
    li .blurb { color: var(--muted); font-size: 0.85rem; margin-top: 0.25rem; }
  </style>
</head>
<body>
  <main>
    <h1>games</h1>
    <ul>
      <li><a href="/bridge/">
        <div class="name">bridge variant</div>
        <div class="blurb">4-player trick taker with asymmetric partnership</div>
      </a></li>
      <li><a href="/chess/">
        <div class="name">hearthstone chess</div>
        <div class="blurb">2-player chess + card game hybrid; king capture wins</div>
      </a></li>
      <li><a href="/ers/">
        <div class="name">math ers</div>
        <div class="blurb">2–6 player slap game; latency-fair slap resolution</div>
      </a></li>
    </ul>
  </main>
</body>
</html>
"""


@app.get("/")
async def landing() -> HTMLResponse:
    return HTMLResponse(LANDING_HTML)


# /bridge → /bridge/ so relative URLs in index.html resolve correctly.
@app.get("/bridge")
async def bridge_slash() -> RedirectResponse:
    return RedirectResponse("/bridge/", status_code=307)


@app.get("/chess")
async def chess_slash() -> RedirectResponse:
    return RedirectResponse("/chess/", status_code=307)


@app.get("/ers")
async def ers_slash() -> RedirectResponse:
    return RedirectResponse("/ers/", status_code=307)


# Each game is its own sub-app — isolated state, isolated WS endpoints.
app.mount("/bridge", bridge_app)
app.mount("/chess", chess_app)
app.mount("/ers", ers_app)


def main() -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
