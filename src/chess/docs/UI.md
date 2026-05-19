# UI / Frontend Design

## Palette (Tokyo Night Dark — match bridge)

```
--bg       #1a1b26
--panel    #24283b
--panel-2  #1f2335
--fg       #c0caf5
--muted    #565f89
--border   #414868
--accent   #7aa2f7   /* blue */
--accent-2 #bb9af7   /* purple */
--good     #9ece6a   /* green */
--warn     #e0af68   /* orange */
--bad      #f7768e   /* red */
--gold     #e0af68   /* mana display */
```

Pieces: render as Unicode chess glyphs (♔♕♖♗♘♙ / ♚♛♜♝♞♟) — large, in the
4-color chess scheme borrowed from bridge suits if we want to color-code:
- White pieces: `--fg` (light)
- Black pieces: `--accent-2` (purple-tinted) — distinguishable, on-brand

Card frame:
- Flat rectangle, ~110×160px in hand, ~140×200px on hover.
- Top-left: gold-colored circle with cost number.
- Body: piece glyph (for piece cards) OR card name + tiny effect text (for
  spells).
- Bottom: 1-line tag like "Pawn" / "Spell · 3".
- Border darkens to `--accent` when card is playable; greys (`--muted`)
  when not affordable.
- **No gradients, no glow, no drop-shadow.** Plain solid backgrounds.

## Layout (rough wireframe)

```
┌────────────────────────────────────────────────────────────────────────────┐
│ HEADER: "Hearthstone Chess  ·  room ABCD  ·  copy link  ·  concede"        │
├──────────────┬─────────────────────────────────────────┬───────────────────┤
│ OPP PANEL    │           BOARD AREA                    │  LOG              │
│              │                                         │                   │
│ name: Steph  │     [ chess board, 480x480 ]            │  · turn 4         │
│ deck: 88     │     a-h files, 1-8 ranks                │  · W plays Pawn   │
│ hand: 4 ★★★★ │     pieces tween 200ms                  │    on a2          │
│ gold: 3 / 4  │                                         │  · W moves e1-e2  │
│              │                                         │  · turn 5         │
│              │                                         │  · B places...    │
├──────────────┘                                         └───────────────────┤
│ YOUR PANEL                                                                 │
│ name: Dave  · gold ●●●○○○○○○○ 3 / 3  · deck 87  · turn ●●●○○ 65s           │
│ ┌────┬────┬────┬────┬────┬────┐                                            │
│ │ 1  │ 2  │ 3  │ 1  │ 5  │ n  │   ← hand fan, drag/click to play           │
│ │ ♟  │ ♞  │ ♝  │ Draw│ ♜ │ Any│                                            │
│ └────┴────┴────┴────┴────┴────┘                                            │
│ [End Turn]                                                                 │
└────────────────────────────────────────────────────────────────────────────┘
```

- Board is always rendered from current player's POV (white-on-bottom for
  white, black-on-bottom for black).
- Opponent hand is shown as face-down card backs, count visible.
- Gold display is dots (filled = current, empty = remaining cap), matching
  Hearthstone's mana crystal vibe but **flat**, no glow.
- Turn timer is dots (each = 18s, 5 total = 90s), drains visually. Soft
  cue: turns red when 1 dot left.
- Active seat: the player panel whose turn it is gets a `--accent` left
  border. Other panel is `--muted`.

## Interactions

### Playing a card
1. Click or drag the card.
2. If it needs targets, the UI enters **targeting mode**:
   - Valid targets get a subtle `--accent` outline.
   - Status line: "Pick a friendly pawn".
   - Click to select. Cancel with right-click or Escape.
3. Once all targets collected, card animates from hand to a "spell zone"
   above the board, resolves, fades.

### Making a move
- Click your piece → legal destinations highlight with a small dot in
  center of each valid square.
- Click destination → move.
- Drag-and-drop also works.
- If promotion, a small popover near the rank shows 4 options (Q/R/B/N).

### End-of-turn auto-pass
If `moves_remaining == 0` AND no playable cards AND no pending prompt,
button changes to "End Turn (no actions left)" pulsing in accent color.
Player must still click — never auto-end without confirmation.

### Animations
All animations CSS-transition-based; no JS animation libs.
- Piece move: `transform` over 200ms ease-out.
- Card draw: fade-in + translateX from deck position over 250ms.
- Card play: fade-out + scale-up to overlay, 350ms.
- Piece capture: opacity 1→0 over 150ms.
- King capture: full-board flash on `--bad` for 400ms, then "GAME OVER"
  banner.

### Modals (targeting / opponent-choice)
Centered overlay, dark backdrop (`rgba(26,27,38,0.85)`). White-paneled
modal with question + buttons. Single keyboard-focus on first button.

### Empty states
- Pre-game (waiting for opponent): big monospace "waiting for opponent..."
  with the room code prominently displayed.
- Mulligan: shows your hand with each card clickable to mark for replace;
  bottom shows "Replace 0 cards" → submit.

## Sound

Optional, off by default. If we add later: card draw click, piece thunk,
spell resolution hum. Reuse bridge's turn-sound utility if convenient.

## Accessibility

- All clickable elements keyboard-tabbable.
- Hover labels for every card cost / icon.
- Color isn't the only signal — important state has a text label too.
