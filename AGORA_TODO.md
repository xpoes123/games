
## 2026-11-01 — from the agora forum
- [ ] Implement decaying K-factor for chess/connect4 Elo (`src/chess/persistence.py:_elo_update`, currently flat `K_FACTOR = 32`) — scale K down with `games_played` (e.g. `K0/sqrt(n+1)`) so early-game streaks don't swing ratings at full weight; the "hidden until 3 games" UI rule in `leaderboard.html` is cosmetic, not real shrinkage.
- [ ] Revisit room-code-only auth model for `games.djiang.xyz` — it's public internet (not LAN-trusted like tv's box), with no accounts and only a 4-letter code gating a room; decide if that's an accepted risk or needs a real check (e.g. rate-limit code guessing, room expiry).
