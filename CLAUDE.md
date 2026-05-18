# games — Claude Code Context

## What this is
Online multiplayer site at **games.djiang.xyz**. First game is a 4-player
bridge variant (spec pending — David and Claude will spec it after the
infra is up). Anonymous play via 4-letter room codes; no accounts.

## Stack
- Python 3.12, FastAPI + uvicorn
- WebSockets for live room state
- pydantic-settings for config
- Vanilla HTML/JS frontend served from `src/static/`
- In-memory room state for now (no DB yet)

## Run locally
```bash
python -m venv venv
source venv/bin/activate
pip install -e .
python -m src.main
# open http://127.0.0.1:7781
```

## Deploy
Lives at `/opt/games` on the Hetzner VPS (87.99.136.82), systemd unit
`games.service`. Local bind on `127.0.0.1:7781`; a reverse proxy on the
VPS terminates TLS for `games.djiang.xyz`.

One-time VPS install (see `deploy/` for the artifacts):
1. `git clone` to `/opt/games`, create `venv`, `pip install -e .`
2. Copy `deploy/games.service` to `/etc/systemd/system/games.service`
3. Pick one of `deploy/Caddyfile.snippet` or `deploy/games.nginx.conf`,
   install it for whichever reverse proxy is already on the box, reload
4. `systemctl enable --now games`

Routine deploys: PR merge → Sentinel clones repo, copies into `/opt/games`,
`pip install -e .`, smoke-test, `systemctl restart games`. Same flow as
Sage/Stavid. Will need this repo registered with Sentinel.

## Layout
```
src/
  main.py        FastAPI app, routes, WebSocket handler
  config.py      pydantic-settings
  rooms.py       In-memory room registry
  static/        Frontend (HTML/CSS/JS)
deploy/
  games.service  systemd unit (installed at /etc/systemd/system/)
  Caddyfile.snippet  reverse-proxy config (Caddy)
  games.nginx.conf   reverse-proxy config (nginx alternative)
tests/
  test_rooms.py  unit tests
```

## Conventions (matches Sage / Stavid / Sentinel)
- Minimal abstractions, flat over nested
- Type hints on signatures only
- Comments only when WHY is non-obvious
- pytest for tests; run `pytest tests/` before committing
- No hardcoded secrets — all config via env / `.env`

## Status (2026-05-18)
- [x] Hello-world FastAPI app with WS-backed rooms and a chat relay
- [x] systemd + reverse-proxy artifacts in `deploy/`
- [x] VPS install at `/opt/games`, `games.service` active, Caddy reverse-proxied at https://games.djiang.xyz
- [ ] Sentinel is currently decommissioned — deploys are manual; redeploy by SSH:
      `cd /opt/games && git pull && ./venv/bin/pip install -q -e . && systemctl restart games`
- [ ] Spec the bridge variant — game rules, state machine, persistence
- [ ] Replace chat-relay WS handler with the real game protocol
