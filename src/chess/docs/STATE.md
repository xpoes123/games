# Server State Model

In-memory only (no DB). Mirrors the bridge model.

## Module layout

```
src/chess/
  __init__.py
  app.py            FastAPI sub-app: routes, WS handler, message dispatch
  rooms.py          Room, Player, Phase enum, RoomRegistry, turn machine
  board.py          Board state, piece movement (wraps python-chess), placement,
                    king capture detection
  deck.py           Card dataclasses, build_deck(), draw/shuffle helpers
  cards.py          Card definitions (the registry; mirrors CARDS.md)
  effects.py        Effect implementations, keyed by card.effect
  prompts.py        Pending prompt state machine (target acquisition)
  static/           Frontend (HTML/CSS/JS)
  docs/             SPEC.md, CARDS.md, PROTOCOL.md, STATE.md, UI.md, PLAN.md
```

## Dataclasses (sketch)

```python
# rooms.py
class Phase(Enum):
    LOBBY = "LOBBY"
    MULLIGAN = "MULLIGAN"
    PLAYING = "PLAYING"
    DONE = "DONE"

@dataclass
class Player:
    pid: str                      # uuid
    name: str
    seat: Literal["white","black","spectator"]
    ws: WebSocket | None
    hand: list[Card] = field(default_factory=list)
    deck: list[Card] = field(default_factory=list)
    gold: int = 0
    gold_cap: int = 0
    # per-turn state, reset at end of own turn:
    moves_remaining: int = 0
    pieces_free_this_turn: bool = False
    no_chess_move_this_turn: bool = False
    cannot_capture_king_this_turn: bool = False
    modular_board_this_turn: bool = False
    pawn_two_moves_armed: bool = False            # mode B of 2g pawn modal
    pawn_back_pawn_sq: str | None = None          # mode A: which pawn may move back
    extra_pawn_squares: dict[str, int] = field(default_factory=dict)  # pawn-sq -> bonus
    # next-turn state, applied at upkeep:
    spell_tax_next_turn: int = 0                  # gold surcharge inflicted on you
    # extra-turn bonus from 10g card:
    extra_turn_queued: bool = False
    extra_turn_no_capture_king: bool = False
    # picked-opponent's-move (10g 'choose opp move'):
    opp_moves_chosen_by_me_next_turn: bool = False
    mulligan_done: bool = False

@dataclass
class Room:
    code: str
    players: dict[str, Player]            # pid -> Player
    seats: dict[str, str]                 # "white"/"black" -> pid
    phase: Phase
    board: Board
    active_seat: Literal["white","black"]
    turn_number: int
    pending_prompts: dict[str, Prompt]    # prompt_id -> Prompt
    pending_card_play: PendingPlay | None # the card mid-resolution
    log: deque[LogEntry]                  # ~30 most recent
    turn_started_ms: int
    winner: str | None
    extra_turn_pending: bool              # true while a queued extra turn waits

@dataclass
class PendingPlay:
    seat: str
    card: Card
    slot: int
    paid_gold: int
    collected_targets: list[dict]         # filled as prompts resolve
    remaining_steps: list[Step]           # macro program left to run
```

### Effect programs

Each card has a sequence of `Step`s. A Step is one of:

- `prompt(kind=..., **opts)` — emit a server→client prompt and wait.
- `place(color, kind, source="back2"|"opp_rank"|"central4"|"any")` — pop a
  collected square target, place a piece.
- `remove(piece_ref)` — remove a piece referenced by collected target or
  predicate.
- `draw(n)` — draw n.
- `gold(delta, scope="this_turn")` — add gold.
- `set_flag(flag_name, value)` — flip a per-turn / next-turn flag.
- `grant_move(n)` — add to moves_remaining.
- `apply_modal(modal_idx)` — branch on the modal choice picked.

This lets effects.py be data-driven without needing a giant if-tree.

## Turn machine (high-level)

```
on_connect:
  if both seats filled and phase==LOBBY:
    deal 3 to white, 4 to black, MULLIGAN

on_mulligan(pid, redraw_slots):
  swap those cards back in, redraw same number, reshuffle
  mark player.mulligan_done
  if both done:
    phase = PLAYING
    set active_seat=white
    begin_turn(white)

begin_turn(seat):
  apply queued upkeep:
    gold_cap = min(gold_cap+1, 10)
    gold = gold_cap
    moves_remaining = 1 + bonus_movements_pending
    pieces_free_this_turn = False (unless effect_set)
    no_chess_move_this_turn = False (unless effect_set)
    spell_tax_active = spell_tax_next_turn; spell_tax_next_turn = 0
    draw 1
  emit state
  start 90s soft timer

play_card(slot, payload):
  validate: phase, active_seat, can_afford (with discount/tax), targets exist
  pay gold (apply pieces_free / pieces_free_this_turn for piece cards)
  execute card's Step program; this may push a PendingPlay onto room.pending_card_play
  on completion: log, emit events, broadcast state

move(from, to):
  validate moves_remaining > 0 and not no_chess_move_this_turn
  compute legal moves on current board (python-chess as engine,
    adapted for: arbitrary piece placement, no castling, no check enforcement)
  if move captures opponent king: check cannot_capture_king flags; if blocked,
    reject
  apply move, moves_remaining -= 1
  if king captured: phase = DONE, winner = active_seat
  if moves_remaining == 0 and no playable cards: auto end_turn

end_turn():
  resolve queued "extra_turn" if applicable
  else swap active_seat, begin_turn(new active)
```

## Legal-move generation

We use **python-chess** for K/Q/R/B/N/P pseudo-legal generation, but we override:
- No castling (skip those moves).
- No check enforcement (don't filter moves that leave own king attacked).
- King capture: a move whose target square holds the enemy king is legal
  (and ends the game).
- 2-square pawn move: only from ranks 2 (white) / 7 (black), regardless of
  whether the pawn arrived via card placement.
- En passant: only valid for the standard 1-tempo window after an opponent's
  two-square pawn push from rank 2/7.
- Modular board flag: when active, edges wrap. We'll implement this as a
  custom move-generator path that wraps file/rank arithmetic. Bishops,
  rooks, queens use ray-walking with wrap; pawns and knights too.

Implementation tactic: maintain our own 64-square board representation
(`board.squares: dict[str, Piece]`), and write a small custom legal-move
generator. Use python-chess only as a sanity reference for tests if needed.

## Promotion

When a pawn moves to rank 8 (white) or rank 1 (black), if the move includes
`promote=`, validate it ∈ {queen, rook, bishop, knight} and replace. If not
included, server emits a `prompt {kind: select_promotion}` and pauses.

## Logging

Each public action appends to `room.log` as `{ts, text}`. Older than 30
entries get popped. The log is shown in the UI side panel.

## Disconnect / reconnect

On disconnect, the player's `ws` is set to None but their seat is held.
When they reconnect with `room=<same>&name=<same>` they slot back in.
Spectators just get a fresh state on join.

## Concurrency

One asyncio task per room handles all message dispatch through a queue.
This serializes state mutations and avoids races (same pattern as bridge).
