# Build Plan

## Phase 1 — Backend core (one agent)

**Goal**: a `src/chess/` Python package that, when invoked over WS, can host
a complete game with pieces, deck, turn machine, chess movement, king-capture
win — but NO card effects beyond the trivial piece-placement cards.

Deliverables:
- `src/chess/board.py` — board representation, legal-move generator (custom),
  piece placement, king-capture detection, modular-board toggle (just the
  flag; implementation of wrap in board.py is required).
- `src/chess/deck.py` — Card dataclass + the 102-card deck builder using
  `src/chess/cards.py` registry.
- `src/chess/cards.py` — the card definitions list (id, name, cost, type,
  copies, effect_key, target_schema). Spell `effect_key`s reference stubs in
  effects.py for Phase 2.
- `src/chess/effects.py` — stubs (raise NotImplementedError for unknown
  effects); fully implemented for the **trivial** ones: piece placement
  (pawn/knight/bishop/rook/queen/any), `spell_draw_2`, `spell_gain_2_gold`.
- `src/chess/prompts.py` — Prompt class, pending state machine.
- `src/chess/rooms.py` — Room, Player, Phase, turn machine, message
  dispatch.
- `src/chess/app.py` — FastAPI sub-app, WS endpoint, static file serving.
- Mount `chess_app` at `/chess` in `src/main.py` AND add to landing page.
- Tests in `tests/test_chess_board.py`, `tests/test_chess_deck.py`,
  `tests/test_chess_room.py`.

Acceptance:
- `pytest tests/` passes (incl. existing bridge tests).
- `python -m src.main`, open WS to `/chess/ws?room=ABCD`, drive a 2-player
  mulligan → playing → king-capture flow with piece cards only.

## Phase 2 — All card effects (one agent)

**Goal**: every card in CARDS.md works end-to-end.

Deliverables:
- `effects.py` fully implemented for every `effect_key` listed in CARDS.md.
- Each effect uses the Step-program style described in STATE.md.
- Targeting: each card emits the right prompt sequence.
- Tests `tests/test_chess_cards.py` — for each non-trivial card, a small
  unit test driving the engine through play.
- Per-turn / next-turn flag handling fully wired (`spell_tax`, modular
  board, no_chess_move, pieces_free, extra_turn).

Acceptance:
- All cards play without raising NotImplementedError.
- `pytest tests/test_chess_cards.py` covers all 30+ unique effects.

## Phase 3 — Frontend skeleton (one agent, can run parallel with Phase 2)

**Goal**: HTML/CSS/JS that connects to the WS endpoint, renders board, hand,
opponent panel, log; handles mulligan; lets player make raw chess moves.

Deliverables:
- `src/chess/static/index.html`
- `src/chess/static/style.css` (Tokyo Night, per UI.md)
- `src/chess/static/app.js` — WS connection, state render, mulligan UI,
  chess move with click-select-click.
- Landing page link from `/` to `/chess/`.

Acceptance:
- Two browser windows can join the same room.
- Mulligan UI works.
- Player can move pieces; capture-king ends the game with a banner.
- Cards display in hand (not yet playable beyond click → server validates).

## Phase 4 — Frontend interactions (one agent)

**Goal**: card play with targeting, modals, animations, polish.

Deliverables:
- Targeting mode: click card → if targets needed, highlight valid squares
  / pieces, status text shows the prompt label, escape cancels.
- Modal component: for `select_modal`, `choose_opp_move`, mulligan choice
  reuses same component.
- Animations per UI.md: piece move tween, card draw slide-in, card play
  fade-to-overlay, piece capture fade-out, king-capture flash.
- Hover state for cards (subtle lift, no glow).
- Turn timer dots that drain over 90s.
- Gold/mana display as filled/empty dots.
- Opponent hand back-faces with count.

Acceptance:
- All UI events from PROTOCOL.md fire visible animations.
- A full hand played by two humans feels responsive and clear.

## Phase 5 — Integration + polish (this agent, final)

**Goal**: scripted full-game integration test, Playwright UI screenshots,
visual polish.

Deliverables:
- `scripts/integration_full_chess.py` — drives 2 WS clients through a full
  game including multiple spells and a king capture. Asserts state at
  checkpoints.
- `scripts/preview_chess.py` — Playwright screenshots key states (lobby,
  mulligan, mid-game, GAME OVER).
- Visual review + tweaks (spacing, color contrast, animation timing).
- Update root README / CLAUDE.md with status line for chess.

Acceptance:
- Integration test passes.
- Screenshots look clean (no gradients, no clutter, terse status).
- `pytest tests/` all green.
- Manual smoke test in browser confirms feel.

## Orchestration notes

- Subagents read these docs (SPEC, CARDS, PROTOCOL, STATE, UI, PLAN) as
  their source of truth. Don't restate the contents in prompts — point
  them at the doc.
- Each subagent gets a single phase. After it returns, the parent (me)
  audits the diff, runs tests, and iterates if needed before moving on.
- Don't let subagents broaden scope. If they want to change PROTOCOL.md or
  the card list, that's a flag — parent reviews.
- Run `pytest tests/` after each phase. Phases must not break bridge tests.
