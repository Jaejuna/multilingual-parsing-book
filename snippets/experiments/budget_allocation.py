#!/usr/bin/env python3
"""Subset DP: 0/1 knapsack and coin change for budget decisions (#15, #16).

WHY THIS EXISTS
---------------
Localization work is always under a budget — a fixed number of segments a human
can review, a word-count cap on a paid MT engine. Two classic decisions fall out,
and both are dynamic-programming over a *budget axis* rather than over a sequence:

- **0/1 knapsack.** "I can review at most C segments' worth of effort; each
  candidate segment costs some effort and is worth some coverage value. Which
  subset maximizes total value?" Each item is taken or not (0/1), and the DP fills
  a table indexed by remaining budget. This is how you spend a review budget where
  it buys the most quality, instead of first-come-first-served.
- **Coin change.** "Hit an exact target — say a batch of exactly N words — using
  the segment sizes available, in the fewest pieces." Unbounded (a size can repeat)
  and minimizing count: the canonical coin-change DP.

Different shape from the sequence DP in sequence_align.py: here the table axis is
the budget, and the recurrence asks "use this item or skip it."

WHAT'S HERE
-----------
- knapsack_01(items, capacity) : (best_value, chosen_labels) under a 0/1 constraint
- coin_change(sizes, target)   : (min_count, combo) to total exactly target, or None

USAGE
-----
    python budget_allocation.py                  # demo: review budget + exact batch
    python budget_allocation.py --capacity 8

Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    label: str
    cost: int       # effort / weight (integer units)
    value: float    # coverage value gained if chosen


def knapsack_01(items: list[Item], capacity: int) -> tuple[float, list[str]]:
    """Maximum total value choosing each item at most once, total cost <= capacity.

    dp[c] = best value achievable with budget c. Processing items in the outer loop
    and iterating the budget *downward* is the 0/1 trick: it stops an item from
    being reused within the same pass (counting up would allow unbounded reuse).
    A parallel `take` table records the decision per (item, budget) so the chosen
    set can be reconstructed. O(items * capacity)."""
    dp = [0.0] * (capacity + 1)
    take = [[False] * (capacity + 1) for _ in items]
    for i, it in enumerate(items):
        for c in range(capacity, it.cost - 1, -1):
            cand = dp[c - it.cost] + it.value
            if cand > dp[c]:
                dp[c] = cand
                take[i][c] = True
    # reconstruct: walk items backwards, undoing the budget when an item was taken
    chosen: list[str] = []
    c = capacity
    for i in range(len(items) - 1, -1, -1):
        if take[i][c]:
            chosen.append(items[i].label)
            c -= items[i].cost
    return dp[capacity], list(reversed(chosen))


def coin_change(sizes: list[int], target: int) -> tuple[int, list[int]] | None:
    """Fewest pieces (sizes reusable) that sum to exactly target, or None if no
    combination hits it. dp[t] = min pieces to total t; build up from 0 and take
    the best over each size. `from_size[t]` records which size closed the total t,
    so the actual combination reconstructs. O(target * len(sizes))."""
    INF = float("inf")
    dp = [0] + [INF] * target
    from_size = [0] * (target + 1)
    for t in range(1, target + 1):
        for s in sizes:
            if s <= t and dp[t - s] + 1 < dp[t]:
                dp[t] = dp[t - s] + 1
                from_size[t] = s
    if dp[target] == INF:
        return None
    combo: list[int] = []
    t = target
    while t > 0:
        combo.append(from_size[t])
        t -= from_size[t]
    return int(dp[target]), sorted(combo, reverse=True)


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------

DEMO_ITEMS = [
    Item("intro",     cost=2, value=3.0),
    Item("combat_ui", cost=3, value=5.0),
    Item("tutorial",  cost=4, value=6.0),
    Item("lore",      cost=5, value=4.0),
    Item("settings",  cost=1, value=1.0),
]
DEMO_SIZES = [50, 30, 20]     # available segment word-sizes
DEMO_TARGET = 100


def demo(capacity: int) -> str:
    value, chosen = knapsack_01(DEMO_ITEMS, capacity)
    change = coin_change(DEMO_SIZES, DEMO_TARGET)
    out = ["# Subset DP: knapsack + coin change\n",
           "## 0/1 knapsack — spend a review budget for the most coverage value\n",
           f"items (cost, value): "
           + ", ".join(f"{it.label}({it.cost},{it.value:g})" for it in DEMO_ITEMS),
           f"\nbudget = {capacity}",
           f"-> best value **{value:g}** by reviewing: {', '.join(chosen)}\n",
           "## Coin change — hit an exact word-count batch in fewest segments\n",
           f"segment sizes: {DEMO_SIZES}, target: {DEMO_TARGET}"]
    if change:
        count, combo = change
        out.append(f"-> {count} segments: {combo} (sum {sum(combo)})")
    else:
        out.append("-> target not reachable from these sizes")
    out.append(
        "\nKnapsack maximizes value under a cost ceiling (each item taken once); "
        "coin change minimizes pieces for an exact total (sizes reused). Both index "
        "the DP table by the budget, not by position — the subset-DP shape.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="0/1 knapsack + coin-change budget DP.")
    p.add_argument("--capacity", type=int, default=8, help="knapsack budget")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    print(demo(args.capacity))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
