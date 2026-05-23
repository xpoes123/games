# Card List + Interpretations

The deck has **108 cards** (48 pieces + 60 spells; each player has their own copy of this list).
This doc is the authoritative interpretation of every card. The id slugs
(`pawn`, `spell_extra_pawn_move`, etc.) are the keys used in code.

Every card has:
- `id` (string slug, unique per *card definition*)
- `name` (display)
- `cost` (int gold; `n` for variable)
- `type` (`piece` | `spell`)
- `copies` (how many of this card in the deck)
- `effect` (engine impl key — matches function in effects.py)
- `targets` (list of required targets, resolved client-side before play)

`targets` schema entries:
- `friendly_pawn` — pick one of your own pawns on the board
- `friendly_piece` — pick one of your pieces on the board
- `enemy_piece` — pick one of opponent's pieces
- `any_piece_kind` — pick from {pawn,knight,bishop,rook,queen,king}
- `any_knight_or_bishop` — pick a knight or bishop on the board (either side)
- `empty_square_back2` — pick an empty square in your back two ranks
- `empty_square_back2_opp` — pick an empty square in opponent's 2nd/7th rank
- `empty_square_central4` — pick an empty square in d4/d5/e4/e5
- `any_empty_square` — pick any empty square
- `discard_subset` — pick a subset of your hand to discard (0+)
- `modal_choice` — pick one of several pre-defined option labels (provided
  by the effect; e.g. for "Choose 1: pawn moves back OR move 2 pawns").

## Piece cards (48 total)

| copies | cost | name | id | placement |
|---|---|---|---|---|
| 24 | 1 | Pawn | `piece_pawn` | empty in your back 2 ranks |
| 6  | 2 | Knight | `piece_knight` | empty in your back 2 ranks |
| 6  | 3 | Bishop | `piece_bishop` | empty in your back 2 ranks |
| 6  | 5 | Rook | `piece_rook` | empty in your back 2 ranks |
| 4  | 8 | Queen | `piece_queen` | empty in your back 2 ranks |
| 2  | n | Any Piece | `piece_any` | pick kind at play, pay that kind's cost, place in back 2 |

`Any Piece`: at play time the player picks a piece kind (Pawn/Knight/Bishop/
Rook/Queen). The gold cost paid equals the chosen piece's normal cost
(1/2/3/5/8). The card itself has no other effect.

## Spell cards (60 total)

### 1 gold (6 cards, 2 copies each = 6)

- **Extra Pawn Step** `spell_extra_pawn_move` ×2 — target friendly pawn.
  This turn, that pawn may move one extra square forward on its move
  (so a normal 1-step pawn move can be 2; if the pawn is on its 2-step
  starting rank, it can move up to 3 squares). Does not allow captures via
  the extra distance — captures still follow the diagonal rule.
- **Gain 2 Gold** `spell_gain_2_gold` ×2 — +2 gold immediately this turn
  (does not raise cap). Discardable into a 0-gold play.
- **Draw 2** `spell_draw_2` ×2 — draw 2 cards.

### 2 gold (3 unique × 2 copies = 6)

- **Pawn Backward / Two-Pawn Move** `spell_pawn_back_or_two_moves` ×2 —
  modal:
  - Option A "Backward": pick a friendly pawn. This turn it may move one
    square backward (no capture). Replaces a normal pawn move it makes this
    turn (counts as the chess move).
  - Option B "Two pawns": this turn, you may make a second chess move
    instead of one, provided both moves are friendly pawn moves.
- **Play 2 Pawns** `spell_play_2_pawns` ×2 — place 2 pawns this turn in
  your back 2 ranks. (Both placements happen at resolution.) Targets:
  2× `empty_square_back2`.
- **Combine 3 Pawns** `spell_combine_pawns` ×2 — select 3 of your own
  pawns on the board, remove them; choose Knight or Bishop, place it on
  any empty square in your back 2 ranks. Modal: knight or bishop.

### 3 gold (6 cards = 3×2)

- **Adjacent En Passant** `spell_adjacent_en_passant` ×2 — select a
  friendly pawn that has any enemy piece on one of the 8 adjacent squares.
  Capture that adjacent piece. **Counts as your move.** Pawn does not move.
- **Sacrifice Pawn, Draw 3** `spell_remove_pawn_draw_3` ×2 — select friendly
  pawn on the board, remove it, draw 3.
- **Modular Board** `spell_modular_board` ×2 — for the rest of this turn,
  the board is **toroidal** (edges wrap: a-file connects to h-file, rank 1
  wraps to rank 8). Pieces moving off one edge re-emerge on the opposite
  edge. **You cannot capture your opponent's king this turn.** (Affects
  both your normal chess move and any other movement effects this turn.)

### 4 gold (6 cards = 3×2)

- **Spell Tax** `spell_tax_opponent` ×2 — opponent's spell cards cost +3
  gold during their **next** turn. (Piece cards are unaffected.) Stacks if
  multiple are cast.
- **Pawn-into-Rook** `spell_pawn_to_rook` ×2 — select friendly pawn, remove
  it. Place a rook in your back 2 ranks.
- **Remove a Minor** `spell_remove_minor` ×2 — pick any knight or bishop
  on the board (either side). Remove it. (Use this defensively or
  aggressively.)

### 5 gold (6 cards = 3×2)

- **Two Minors, Opp Picks** `spell_two_minors_opp_picks` ×2 — opponent
  chooses Knight or Bishop. Place 2 of that kind in your back 2 ranks.
  Opponent's choice is a synchronous prompt (their UI gets a modal). If
  they don't answer in 30s, default = Knight.
- **Discard for Draw** `spell_discard_draw` ×2 — choose a subset of your
  hand (the just-played card is already gone). Discard them; draw one card
  per discard.
### 6 gold (6 cards = 3×2)

- **King to Center** `spell_king_to_center` ×2 — pick a king (either side),
  pick an empty central square (d4, d5, e4, e5). Move the king there.
  **Counts as your move.** Cannot capture opponent's king (you can't move
  enemy king onto a square attacking your king if it would chain, etc. —
  just enforce: target square must be empty, and the king arrives there).
- **Draw to Hand Doubled** `spell_draw_double` ×2 — draw N cards, where N
  is your current hand size at the moment of resolution (post-cast).
  Cap at hand-cap 10.
- **5 Pawns → Queen** `spell_pawns_to_queen` ×2 — requires ≥5 friendly pawns
  on the board. Remove ALL your pawns; place a queen in your back 2 ranks.

### 7 gold (6 cards = 3×2)

- **7 Material → Queen** `spell_material_to_queen` ×2 — pick friendly
  non-king pieces totaling **exactly 7** points of material (pawn=1,
  knight=2, bishop=3, rook=5, queen=8 — using card costs as point values).
  Remove them; place a queen in your back 2 ranks. If you can't reach
  exactly 7, the card can't be played.
- **Convert Piece** `spell_convert_piece` ×2 — pick an opponent's non-king
  piece. It becomes yours (same kind, same square). **Counts as your move.**
- **Triple Move** `spell_triple_move` ×2 — this turn, your chess-move
  allowance is set to 3 (replacing 1, additive with any extras already
  granted? — set to **3** flat). **Cannot capture opponent's king this turn.**

### 8 gold (6 cards = 3×2)

- **Teleport** `spell_teleport` ×2 — pick one of your pieces, pick any
  empty square. Move it there. **Counts as your move.** Cannot capture the
  opponent's king via teleport (and the target must be empty).
- **Rook + Minor** `spell_rook_and_minor` ×2 — place a rook AND a knight-or-
  bishop (modal). Both in your back 2 ranks.
- **Pawn/Rook on Enemy Rank** `spell_deploy_enemy_rank` ×2 — place a pawn
  OR a rook (modal) on an empty square in the opponent's 2nd/7th rank
  (rank 7 for White, rank 2 for Black). Note: a pawn placed there is one
  move from promotion.
- **8-Pawns or 8-Material** `spell_eight_or_eight` ×2 — modal:
  - "Up to 8 Pawns": choose 1..8 empty squares in your back 2 ranks; place
    a pawn on each.
  - "8 Material": place non-pawn pieces (K/N/B/R/Q from K/B 2/3/5/8) totaling
    exactly 8 points of material. (e.g., 2 bishops + a knight = 3+3+2=8.
    All placements in your back 2.)

### 9 gold (6 cards = 3×2)

- **Wipe Type** `spell_wipe_type` ×2 — choose ONE piece kind from
  {pawn, knight, bishop, rook, queen}. **Remove every piece of that kind
  from the board** (both sides). Kings are immune.
- **Draw to Full, No Move** `spell_draw_full_no_move` ×2 — draw until hand
  is 10. **You cannot make a chess move this turn.**
- **Choose Opp Move** `spell_choose_opp_move` ×2 — on your opponent's next
  turn, you select their chess move from their legal-move list. The move
  cannot capture your king. (Implementation: set a flag on opponent for
  their next turn that surfaces the chooser UI to *you*.)

### 10 gold (6 cards, 1 copy each = 6)

- **Draw 4 + Rook + Extra Move** `spell_draw_rook_extra` — draw 4, place a
  rook in your back 2, gain an extra move this turn. **Cannot checkmate.**
- **Quadruple Deploy** `spell_quad_deploy` — place a knight, a bishop, a
  rook, AND a pawn (in that order) in your back 2 ranks.
- **Extra Turn** `spell_extra_turn` — take another full turn after this one
  ends, **at the same gold cap** (gold refreshes at upkeep of the bonus
  turn, draw still happens). **Cannot capture opponent's king during the
  bonus turn.**
- **Queen + Strip Material** `spell_queen_and_strip` — place a queen in your
  back 2 ranks. Then remove ≤4 points of opponent material (pawn=1, etc.,
  total ≤ 4, your choice of pieces; non-king).
- **Discard Hand, Draw 3** `spell_discard_opp_hand` — opponent discards
  their entire hand, then draws 3.
- **Free Pieces** `spell_free_pieces` — draw 5. Until end of turn, **all
  piece cards you play cost 0 gold.**

## Engine notes

- Spell costs are paid at play time. A spell can fail validation if its
  targets don't exist (e.g. `combine_pawns` with <3 pawns) — UI should grey
  the card out client-side and the server must re-validate.
- "Counts as your move" cards may only be played if you still have a chess
  move available this turn.
- "Cannot capture opponent's king" tag: while in effect, an attempted king
  capture is rejected (server returns error).
- "Extra move" / "Triple move" stack additively up to a per-turn cap of 4.
- Order-of-operations for a spell with multiple placements (`quad_deploy`):
  one target prompt per placement, in the order written. Each placement
  resolves before the next prompt.
- All target validation runs server-side. Client provides a chosen target;
  server verifies legality.
