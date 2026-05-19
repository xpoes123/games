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
pip install -e ".[dev]"
playwright install chromium    # one-time, for UI self-verification
python -m src.main
# open http://127.0.0.1:7781
```

## UI self-verification (Playwright)

After making frontend changes, snapshot the local server and inspect the PNG:

```bash
./venv/bin/python -m src.main &
./venv/bin/python scripts/preview.py        # → /tmp/games-preview.png
```

The helper takes a URL + output path as optional positional args. Used during
iteration so I don't have to ask David to screenshot every change.

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
  main.py            Root FastAPI app — landing page + game mounts
  config.py          pydantic-settings
  bridge/            Bridge variant sub-app, mounted at /bridge
    app.py             FastAPI sub-app: routes + WS handlers
    rooms.py           Table/Phase/Bid models, rule enforcement
    cards.py           Deck, deal_four, trick eval
    static/            Frontend (HTML/CSS/JS) for /bridge
  # future: chess/, etc. — mount each at its own URL prefix
deploy/
  games.service          systemd unit
  Caddyfile.snippet      reverse-proxy snippet
  games.nginx.conf       nginx alternative
tests/                   unit tests
scripts/                 dev helpers (Playwright previews + integration)
```

URL structure: `/` lists games, `/bridge/` is the bridge variant.
Each game is a self-contained FastAPI sub-app — its own state, its own
WS endpoint (`/<game>/ws`), no interference with siblings. To add a new
game: create `src/<name>/`, write an `app.py` exporting a FastAPI
instance, then `app.mount("/<name>", that_app)` in `src/main.py`.

## Conventions (matches Sage / Stavid / Sentinel)
- Minimal abstractions, flat over nested
- Type hints on signatures only
- Comments only when WHY is non-obvious
- pytest for tests; run `pytest tests/` before committing
- No hardcoded secrets — all config via env / `.env`

## Status (2026-05-18)

Bridge is **playable end-to-end** per David's variant spec. **Hearthstone
Chess** (the second game) was built overnight 2026-05-18: backend,
frontend, all 30 card effects, and a king-capture WS integration test.

Deploys are manual (Sentinel is offline):
  `cd /opt/games && git pull && systemctl restart games`

### Hearthstone Chess (`/chess/`)

2-player chess + Hearthstone hybrid. Kings only on the board at start;
both players draw from their own copy of a fixed 108-card deck. King
capture wins. See `src/chess/docs/` for full SPEC, CARDS, PROTOCOL,
STATE, UI, and PLAN documents.

Done:
- [x] Backend: chess engine (custom move generator, no-check rules,
      modular-board wrap), deck/hand, room state machine, full WS routing
- [x] All 60 spell effects + piece placement implemented end-to-end
- [x] Frontend: lobby, mulligan UI, board (Tokyo Night palette, filled
      Unicode glyphs), hand fan, opponent panel, log, gold/timer dots,
      targeting flow for every prompt kind from PROTOCOL.md
- [x] Animations: piece move tween, capture fade, placement fade-in,
      king-capture flash, turn-change banner
- [x] `scripts/integration_full_chess.py` — drives 2 WS clients through
      a full king-capture game in ~5s, asserts win_reason
- [x] `scripts/preview_chess.py` — Playwright screenshots of lobby,
      mulligan, midgame, targeting, GAME OVER
- [x] 112 tests pass (`./venv/bin/pytest tests/`)

Open (deferred):
- [ ] Reconnect-safe gameplay (refresh loses seat)
- [ ] Card-draw slide-in animation (CSS keyframe exists, not wired)
- [ ] Multi-table support per room code (currently 1 game per code)
- [ ] AI opponent / single-player mode
- [ ] Spectator UI (slot exists in code, no first-class UX)

Notes:
- No castling; no check enforcement. King capture is just a normal move
  with extra game-ending consequence.
- `Player` per-turn flags reset at end-of-own-turn; next-turn flags
  (`spell_tax`, `extra_turn_queued`, `opp_moves_chosen_by_me_next_turn`)
  apply at the start of the relevant player's next upkeep.
- `spell_choose_opp_move` surfaces a `prompt {kind: choose_opp_move}` to
  the CASTER on the opponent's next turn — caster picks from the
  filtered legal-move list (excluding moves that would capture the
  caster's own king).
- `spell_extra_turn` re-enters `begin_turn` for the same seat with
  `cannot_capture_king_this_turn = True` set.
- Subset-sum cards (material→queen, 8-material, queen+strip) send the
  picked piece squares in the first target dict; server re-validates
  the sum.
- `cards.py` is the single source of truth for the card list; effects
  registered in `effects.py` by `effect_key`. To add a card, append to
  the registry and register the effect.

### Bridge (`/bridge/`)

### Done
- [x] FastAPI + WS backend, Caddy-fronted at https://games.djiang.xyz
- [x] Phase machine: LOBBY → BIDDING → CALLING → PLAYING → DONE
- [x] Dealing animation (52 cards fly from deck in rotation), hand sort
      (4-color deck: ♠ dark, ♥ red, ♦ blue, ♣ green; A high, descending
      within suit)
- [x] Bidding: dealer opens, clockwise, pass-and-out, NT < ♣ < ♦ < ♥ < ♠
      strain order, stick-the-dealer if all pass
- [x] Calling: declarer names a card they don't hold; partner told
      privately via `you_are_partner`; opponents see only the card
- [x] Lead direction: declarer for suited, declarer's right for NT
- [x] Trick play with full rule enforcement:
      - must follow suit
      - trump can't be led until broken
      - trump beats off-suit, higher trump wins between trumps
- [x] Trick collection animation (all 4 cards collect toward winner)
- [x] End-of-hand: partner publicly revealed, made/down verdict
      (`team_tricks` vs `level+6`)
- [x] Dealer rotation + cumulative trick totals across hands
- [x] Bid history UI (inline chips below info line)
- [x] Turn arrow indicator on the active seat
- [x] Server-side validation surfaces back to client (illegal plays
      flash a red error in the status line)
- [x] 4-player end-to-end integration test in
      `scripts/integration_full_hand.py` — runs a full hand including
      redeal in ~40s, validates partner reveal + rotation + totals

### Still open (deliberately deferred — David's spec leaves these out)
- [ ] **Scoring system** — currently we only track tricks. No points,
      no doubled/redoubled, no game/rubber concept. Ask David when ready.
- [ ] Reconnect / refresh-safe gameplay — refresh loses your seat
- [ ] Played-card sizing parity (small visual hiccup when your big
      card shrinks to standard size on play)
- [ ] Trump indicator more prominent during play (currently inferred
      from the contract line)
- [ ] Multi-table support (currently a single global table; URL share
      is the entry mechanism)

### Notes
- Bid ordering: 1NT < 1♣ < 1♦ < 1♥ < 1♠ < 2NT < 2♣ < ... < 7♠.
  NT is *least*-prioritized in this variant.
- Stick-the-dealer minimum is 1NT (the lowest possible bid).
- `play_card` enforces: phase, turn, card-in-hand, follow-suit, trump-
  break. Returns `(card, error_string)` — `_handle_play` forwards the
  error to the player via a private `error` WS message.
- Partnership privacy: `partner_seat` is in `public_state` *only* at
  phase=DONE. During PLAYING the partner gets `you_are_partner`
  privately at call time, no other client sees it.
