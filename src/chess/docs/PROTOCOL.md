# WebSocket Protocol

WS endpoint: `/chess/ws?room=ABCD&name=<display>`

All messages JSON. Format: `{"type": "<name>", ...fields}`.

## Connection lifecycle

1. Client opens WS with `room=` (4-letter uppercase code) and `name=`.
2. Server assigns a `player_id` (uuid) and `seat` (`white` / `black` /
   `spectator`). First connect → white, second → black, rest → spectator.
3. Server sends `state` snapshot. Client renders.

## Server → Client

### `state`
Full snapshot. Sent on connect, on any state change, on reconnect.

```jsonc
{
  "type": "state",
  "phase": "MULLIGAN" | "PLAYING" | "DONE",
  "you": {"seat": "white", "name": "Dave"},
  "white_name": "Dave",
  "black_name": "Steph",
  "active_seat": "white",
  "turn_number": 4,           // 1-based, increments on Black's end-turn
  "turn_deadline_ms": 1715900000000,  // wall-clock ms; null if untimed
  "white": {
    "gold": 3, "gold_cap": 3,
    "deck_size": 87, "hand_size": 5,
    "extra_moves": 0,             // movement allowance left this turn (white's pov: only on their turn)
    "spell_tax": 0,               // gold surcharge applied by opp tax cards (effective on white's turn)
    "must_move_pawn": false,      // from "two pawn moves" mode B
    "pieces_free_this_turn": false,
    "no_chess_move_this_turn": false,
    "cannot_capture_king_this_turn": false,
    "modular_board_this_turn": false
  },
  "black": { /* same shape */ },
  "board": [
    // 64-entry array, index 0 = a1, 1 = b1, ..., 63 = h8.
    {"sq": "e1", "kind": "king", "color": "white"},
    {"sq": "e8", "kind": "king", "color": "black"}
  ],
  "hand": [                       // ONLY YOUR hand; opponent gets opaque count
    {"slot": 0, "card_id": "piece_pawn", "name": "Pawn", "cost": 1,
     "type": "piece", "playable": true},
    ...
  ],
  "opp_hand_size": 4,
  "log": [                        // recent game log, last ~30 entries
    {"ts": 1715899999000, "text": "White plays Pawn on a2"},
    ...
  ],
  "winner": null,                 // "white" | "black" | null
  "pending_prompt": null          // see "prompts" below
}
```

### `prompt`
Sent when a player must answer an inline modal (target selection,
opponent's-choice prompts, choose-opp-move prompts, mulligan).

```jsonc
{
  "type": "prompt",
  "prompt_id": "p-1234",
  "kind": "select_square_back2" | "select_friendly_pawn" | "select_enemy_piece"
        | "select_any_piece_kind" | "select_any_minor" | "select_central_empty"
        | "select_any_empty" | "select_modal" | "mulligan"
        | "select_enemy_rank_empty" | "discard_subset" | "choose_opp_move",
  "label": "Pick a square in your back two ranks",
  "options": [...],                 // for select_modal: ["Knight","Bishop"]
  "min": 0, "max": null,            // for discard_subset / placement counts
  "moves": [...],                   // for choose_opp_move: list of legal moves
  "deadline_ms": 1715900030000
}
```

Client responds with `prompt_response` (see below).

### `event`
Lightweight notifications to drive animations. Always paired with a fresh
`state`. Client uses events to animate then re-renders from state.

```jsonc
{"type": "event", "kind": "card_drawn", "by": "white", "count": 1}
{"type": "event", "kind": "card_played", "by": "white", "card_id": "piece_pawn"}
{"type": "event", "kind": "piece_placed", "color": "white", "kind": "pawn", "sq": "a2"}
{"type": "event", "kind": "piece_moved", "from": "e2", "to": "e4", "captured": null}
{"type": "event", "kind": "piece_captured", "sq": "d5", "victim_kind": "knight", "victim_color": "black"}
{"type": "event", "kind": "king_captured", "winner": "white"}
{"type": "event", "kind": "turn_end", "next": "black", "turn_number": 5}
```

### `error`
Validation rejection, surfaced to the player only.

```jsonc
{"type": "error", "text": "It's not your turn"}
{"type": "error", "text": "Illegal move: pawn can't capture forward"}
```

## Client → Server

### `mulligan`
```jsonc
{"type": "mulligan", "redraw_slots": [0, 2]}  // slot indexes to replace
```
Sent once during MULLIGAN phase. Empty array = keep all. Server moves into
PLAYING phase once both seats have submitted.

### `play_card`
```jsonc
{
  "type": "play_card",
  "slot": 3,                            // index in your hand
  "targets": [                          // resolved targets, in card's prompt order
    {"square": "a2"},
    {"piece_kind": "knight"},
    {"square": "b1"}
  ],
  "modal": "Knight",                    // for cards with modal_choice
  "discard_slots": [0, 4, 5]            // for discard_subset
}
```

If the card needs sequential prompts the client sends `play_card_begin`
followed by `prompt_response` messages — see "Two-step play" below.

### `play_card_begin`
For cards needing multiple prompts whose options depend on intermediate
state. Server sends prompts; client sends `prompt_response`s.

```jsonc
{"type": "play_card_begin", "slot": 3}
```

### `prompt_response`
```jsonc
{
  "type": "prompt_response",
  "prompt_id": "p-1234",
  "square": "a2",                       // OR
  "piece_kind": "knight",               // OR
  "option": "Knight",                   // OR
  "slots": [0, 1, 4],                   // OR
  "move": {"from": "e2", "to": "e4", "promote": null}
}
```

### `move`
Make a chess move.

```jsonc
{"type": "move", "from": "e2", "to": "e4", "promote": "queen"}  // promote optional
```

### `end_turn`
```jsonc
{"type": "end_turn"}
```

Server may auto-end the turn if you exhaust moves and have no playable cards,
but explicit end is always allowed.

### `cancel_prompt`
Cancel a pending card-play (refund gold, return card to hand). Only valid
before any irreversible step of the card's resolution.

```jsonc
{"type": "cancel_prompt", "prompt_id": "p-1234"}
```

### `concede`
```jsonc
{"type": "concede"}
```
Other player wins.

## Two-step play example: `Combine 3 Pawns`

1. Client: `play_card_begin {slot: 2}`
2. Server: `prompt {kind: select_friendly_pawn, ...}` (1st pawn)
3. Client: `prompt_response {square: "a2"}`
4. Server: `prompt {kind: select_friendly_pawn, exclude: ["a2"], ...}` (2nd)
5. ... etc
6. Server: `prompt {kind: select_modal, options: ["Knight","Bishop"]}`
7. Server: `prompt {kind: select_square_back2}`
8. Server resolves; `event card_played`, `event piece_placed`, `state`.

## Errors during a prompt sequence
If the client sends an invalid `prompt_response`, server replies with
`error` and keeps the prompt open. Client can also `cancel_prompt` to abort.
