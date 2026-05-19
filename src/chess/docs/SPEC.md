# Hearthstone Chess — Game Spec

## Premise
2-player chess + card game hybrid. Each player starts with only their king on
the board and a shared 102-card deck. Each turn they gain "gold" (mana),
draw cards, play cards to build an army or cast spells, and make one chess
move. **First player to capture the opponent's king wins.**

## Board
- Standard 8×8 chess board.
- White on ranks 1–2 side, Black on ranks 7–8 side.
- Files a–h, ranks 1–8 (algebraic notation).
- **No check / no checkmate enforcement.** Kings are normal pieces that can
  be moved into attacked squares — they're just very valuable. The game ends
  the instant a king is captured.

## Starting state
- Each player has only their **king** on the board, on their normal starting
  square (e1 for White, e8 for Black).
- Both players draw from their own copy of the same fixed deck (see CARDS.md).
- **Mulligan**: White draws 3, Black draws 4. Each player may mulligan any
  subset of their starting hand (replace + shuffle in) once before play begins.
- White moves first. Both start at **1 gold / 1 gold cap**.

## Turn structure
A turn is: **upkeep → main phase → end**.

1. **Upkeep** (automatic):
   - Gold cap += 1 (max 10).
   - Current gold = gold cap (refresh).
   - Draw 1 card. (If deck empty → fatigue, see below.)
   - Hand size cap is **10**; cards drawn over the cap are burned (Hearthstone-style).
2. **Main phase** (player actions, any order):
   - Play any number of cards you can afford (pay gold cost).
   - Make **one chess move** with a piece on the board.
   - Special cards labeled `(counts as your move)` consume the chess move
     instead of letting you make a normal one.
   - Cards labeled `(cannot move this turn)` lock out the chess move entirely.
   - Some cards grant **extra moves** (e.g. `move 3 pieces this turn`,
     `extra move`). These add to the per-turn move allowance.
3. **End**: explicitly press End Turn (or your move-allowance is used up and
   you have no affordable cards). Turn passes.

## Fatigue
When a player must draw and their deck is empty: nothing happens, no damage
(Hearthstone-style fatigue would need HP; we don't have HP). Just no card.

## Piece placement (from cards)
- "Play a Pawn / Knight / Bishop / Rook / Queen" places the piece on **any
  empty square in your own back two ranks** (ranks 1–2 for White, 7–8 for
  Black) chosen by the player.
- Exceptions are written on the card (e.g. "Play a pawn or rook in your
  opponent's 2nd/7th ranks" → for White, rank 7; for Black, rank 2).
- Placed pieces are normal pieces; they move on subsequent turns.
- **Pawn promotion**: a pawn that reaches the opposite-side last rank
  promotes. Player picks Queen / Rook / Bishop / Knight at promotion time.
  Captured pawns from the *board* don't go back to the deck.

## Chess movement
- Standard chess movement for K, Q, R, B, N, P (incl. 2-square pawn opening
  from rank 2 / rank 7 only if pawn is still on its original-style rank;
  see below).
- **Castling**: disabled (kings are unique starting pieces; no rooks at start;
  rooks coming in via cards have not "never moved"). Keep it simple.
- **En passant**: standard rules apply — only available immediately after an
  opponent's pawn moves two squares from rank 2 / rank 7 and lands beside
  your pawn. The 3-gold "en passant adjacent" card is a separate effect that
  ignores standard EP rules entirely.
- **2-square pawn move**: a pawn may move 2 squares on its first move from
  its placement rank only (rank 2 for White pawns placed there; rank 7 for
  Black). This applies whether the pawn started there or was deployed there
  by a card. Once moved or captured-and-replaced, the option is lost.
- **No check enforcement**: you may move a piece such that your king is
  attackable — you just risk losing.

## Win condition
**King capture.** First player to make a move (chess move or card effect)
that captures the opponent's king wins immediately. Cards explicitly tagged
`(cannot capture opponent's king)` or `(cannot checkmate)` cannot perform
that final capture, but normal play can.

## Special tags on cards
- `(counts as your move)` — uses your chess-move allowance.
- `(cannot capture opponent's king)` / `(cannot checkmate)` — effect blocks
  king capture even if it would otherwise enable it. Treat both the same:
  the king-capture step is illegal during the effect.
- `(extra move)` — adds +1 to your remaining move allowance this turn.
- `(this turn)` — modifier lasts until end-of-turn.
- `(next turn)` — modifier kicks in at start of opponent's next turn.

## Soft turn timer
Each turn has a **90-second** soft budget. UI shows a clock counting down.
Going over does NOT auto-pass — it's purely informational. (Could harden
later.)

## Animations / feedback
- Card draw: card slides in from deck to hand.
- Card play: card animates from hand to a "spell zone" overlay, resolves,
  fades out.
- Piece placement: piece appears with a brief fade-in on its target square.
- Piece move: piece tweens to its target square (~200ms).
- Capture: captured piece fades out (~150ms) just before the moving piece
  arrives.
- Turn change: a subtle banner ("Your Turn" / "Opponent's Turn") flashes.

## Aesthetic
Tokyo Night Dark only — match the bridge variant's palette. **No gradients,
glow, or drop-shadow vibe-coded slop. No friendly hint copy.** Use chip-style
inline status, terse labels ("gold 4 / 4", "deck 87", "hand 5/10").
