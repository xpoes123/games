"""How slappable is Math ERS vs normal ERS?

For each game we model the pile as a shuffled 52-card deck and ask, at every
flip, whether the current top of the pile is slappable. The fraction of flips
that are slappable is the "slap density" — how often an opportunity exists.

Normal ERS baseline = doubles + sandwiches (the near-universal core rules).
Run: ./venv/bin/python scripts/analyze_ers.py
"""
from __future__ import annotations

import random
from collections import Counter

from src.ers.rooms import CONCAT_MAX, matching_rules

DECK = [r for r in range(1, 14) for _ in range(4)]
WINDOW = max(CONCAT_MAX, 3)  # rules never look deeper than this many top cards


def normal_rules(window: list[int]) -> list[str]:
    """Classic ERS slap conditions on the pile top (rank-based)."""
    out = []
    if len(window) >= 2 and window[-1] == window[-2]:
        out.append("double")
    if len(window) >= 3 and window[-1] == window[-3]:
        out.append("sandwich")
    return out


def run(trials: int, seed: int = 0) -> None:
    random.seed(seed)
    math_hits = normal_hits = positions = 0
    math_by = Counter()
    normal_by = Counter()
    for _ in range(trials):
        deck = DECK[:]
        random.shuffle(deck)
        for i in range(2, len(deck) + 1):
            win = deck[max(0, i - WINDOW):i]
            positions += 1
            m = matching_rules(win)
            if m:
                math_hits += 1
                for r in m:
                    math_by[r] += 1
            n = normal_rules(win)
            if n:
                normal_hits += 1
                for r in n:
                    normal_by[r] += 1

    print(f"{trials} games, {positions} flips analyzed\n")
    print(f"  Math ERS slap density:   {math_hits / positions:6.2%}")
    print(f"  Normal ERS slap density: {normal_hits / positions:6.2%}")
    print(f"  ratio: {(math_hits / normal_hits):.2f}x more slappable\n")
    print("  Math ERS, by rule (rules can overlap on one pile):")
    for r, c in math_by.most_common():
        print(f"    {r:11} {c / positions:6.2%}")
    print("  Normal ERS, by rule:")
    for r, c in normal_by.most_common():
        print(f"    {r:11} {c / positions:6.2%}")


if __name__ == "__main__":
    run(trials=20000)
