#!/usr/bin/env python3
"""Binary search: exact lookups and 'search on the answer' for a threshold (#10, #18).

WHY THIS EXISTS
---------------
The fuzzy matcher (#18) fires when a similarity score clears a threshold. Pick the
threshold too low and you drown reviewers in false matches; too high and you miss
real ones. A common, concrete constraint is a *review budget*: "I can hand-check
at most K matches — what is the lowest threshold that keeps the count at or below
K?" Lowering the threshold only ever admits more matches, so "matches(t) <= K" is
monotone in t (false below some point, true above it). Any monotone predicate over
an ordered range is a binary-search problem — here, *binary search on the answer*.

This file shows both faces of binary search:

- classic lookups: lower_bound / upper_bound over a sorted list (what `bisect`
  does, written out so the invariant is visible)
- parametric search: the smallest threshold satisfying a monotone predicate,
  found in O(log n) probes instead of scanning every candidate

WHEN NOT TO
-----------
Binary search needs a *sorted* array or a *monotone* predicate. If your objective
isn't monotone (raw F1 vs threshold can wiggle), don't binary-search it — scan, or
binary-search a monotone proxy (precision, match count) instead. The demo is
careful to bind the budget to a monotone quantity for exactly this reason.

USAGE
-----
    python threshold_search.py                   # demo: budget-capped threshold
    python threshold_search.py --budget 3

Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable


def lower_bound(xs: list[float], target: float) -> int:
    """First index i with xs[i] >= target (xs sorted ascending). The half-open
    [lo, hi) window shrinks by half each step; lo is the answer when it closes."""
    lo, hi = 0, len(xs)
    while lo < hi:
        mid = (lo + hi) // 2
        if xs[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def upper_bound(xs: list[float], target: float) -> int:
    """First index i with xs[i] > target — the classic sibling of lower_bound.
    Together they bracket the run of values equal to target: [lower, upper)."""
    lo, hi = 0, len(xs)
    while lo < hi:
        mid = (lo + hi) // 2
        if xs[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def search_threshold(candidates: list[float], predicate: Callable[[float], bool]) -> float | None:
    """Smallest candidate threshold for which `predicate` is True, assuming
    predicate is monotone (once True, stays True as the threshold rises). Binary
    search on the sorted candidates: O(log n) predicate evaluations. Returns None
    if no candidate satisfies it."""
    lo, hi = 0, len(candidates)
    ascending = sorted(candidates)
    while lo < hi:
        mid = (lo + hi) // 2
        if predicate(ascending[mid]):
            hi = mid               # answer is at mid or to the left
        else:
            lo = mid + 1           # everything <= mid fails; go right
    return ascending[lo] if lo < len(ascending) else None


def matches_at(scores: list[float], threshold: float) -> int:
    """How many scores fire at this threshold. Monotone non-increasing in
    threshold — the property that makes the budget search a binary search."""
    return sum(1 for s in scores if s >= threshold)


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------

DEMO_SCORES = [0.31, 0.42, 0.55, 0.58, 0.63, 0.71, 0.79, 0.88, 0.91, 0.96]


def demo(budget: int) -> str:
    # candidate thresholds: the distinct scores are the only points where the
    # match count can change, so they are the only thresholds worth testing.
    candidates = sorted(set(DEMO_SCORES))
    chosen = search_threshold(candidates, lambda t: matches_at(DEMO_SCORES, t) <= budget)
    out = ["# Binary search on a threshold (review-budget cap)\n",
           f"scores: {DEMO_SCORES}\n",
           f"goal: lowest threshold keeping fired matches <= {budget}\n"]
    if chosen is None:
        out.append("no threshold meets the budget (even the max fires too many)")
    else:
        out.append(f"-> threshold = **{chosen:.2f}**, fires {matches_at(DEMO_SCORES, chosen)} "
                    f"match(es) (<= {budget})")
        below = chosen - 1e-9
        out.append(f"   (a hair lower would fire {matches_at(DEMO_SCORES, candidates[max(0, candidates.index(chosen)-1)])}+, "
                    "over budget)")
    out.append(
        f"\nClassic lookups on the same sorted scores: lower_bound(0.63) = "
        f"{lower_bound(sorted(DEMO_SCORES), 0.63)}, "
        f"upper_bound(0.63) = {upper_bound(sorted(DEMO_SCORES), 0.63)} "
        "(they bracket the value's position). The budget search probes only "
        f"~log2({len(candidates)}) ≈ {max(1, len(candidates).bit_length())} thresholds, not all of them.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Binary search: lookups + parametric threshold.")
    p.add_argument("--budget", type=int, default=3, help="max matches to allow")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    print(demo(args.budget))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
