#!/usr/bin/env python3
"""Sliding window and two pointers over a quality stream (#15).

WHY THIS EXISTS
---------------
Corpus quality is rarely uniform — a vendor's later batches drift, or one section
of a file is machine-translated. A single global average hides that. What you want
is *local* quality: a moving view that says "rows 4000-4200 are where coverage
fell off a cliff." Computing that by re-summing a window at every position is
O(N*W); the sliding-window trick makes it O(N) by adding the entering element and
subtracting the leaving one — the running sum is maintained, never recomputed.

The same family solves "what is the longest stretch I can keep below a defect
budget?" with two pointers: a left and right index defining a window that grows on
the right and only shrinks from the left when the budget is blown. Each pointer
moves forward at most N times, so the whole scan is O(N).

WHAT'S HERE
-----------
- sliding_window_mean(values, w) : O(1)-per-step rolling mean over a fixed window
- longest_ok_run(flags, max_bad) : longest contiguous span with <= max_bad defects
  (1 = defect/untranslated, 0 = ok), via the two-pointer variable window

USAGE
-----
    python windowed_metrics.py                   # demo over a synthetic stream
    python windowed_metrics.py --window 5 --max-bad 2

Stdlib only (collections). Python 3.10+.
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from typing import Iterable, Iterator


def sliding_window_mean(values: Iterable[float], w: int) -> Iterator[tuple[int, float]]:
    """Yield (end_index, mean) for each full window of width w. Keeps a deque of
    the current window and a running sum, so each step is O(1): add the new value,
    pop and subtract the oldest once the window is full. Total O(N)."""
    if w <= 0:
        raise ValueError("window must be positive")
    window: deque[float] = deque()
    total = 0.0
    for i, v in enumerate(values):
        window.append(v)
        total += v
        if len(window) > w:
            total -= window.popleft()      # drop the element leaving the window
        if len(window) == w:
            yield i, total / w


def longest_ok_run(flags: list[int], max_bad: int) -> tuple[int, int, int]:
    """Longest contiguous span containing at most max_bad defect flags (1=defect).
    Two pointers: extend `right` over every element, and whenever the defect count
    exceeds the budget, advance `left` until it fits again. The window [left,right]
    is always feasible, and we track the widest one. Returns (length, start, end)
    with end exclusive. O(N) — each pointer only moves forward."""
    left = 0
    bad = 0
    best = (0, 0, 0)
    for right, f in enumerate(flags):
        bad += f
        while bad > max_bad:
            bad -= flags[left]
            left += 1
        if right - left + 1 > best[0]:
            best = (right - left + 1, left, right + 1)
    return best


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------

# per-row "translated?" stream: a clean head, a degraded middle, a clean tail
DEMO_FLAGS = [0, 0, 0, 1, 0, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0, 0]   # 1 = untranslated


def demo(window: int, max_bad: int) -> str:
    coverage = [1 - f for f in DEMO_FLAGS]   # 1 = translated, for a coverage mean
    means = list(sliding_window_mean(coverage, window))
    worst_end, worst_val = min(means, key=lambda kv: kv[1])
    length, start, end = longest_ok_run(DEMO_FLAGS, max_bad)
    out = ["# Windowed quality metrics\n",
           f"stream of {len(DEMO_FLAGS)} rows (1 = untranslated): {DEMO_FLAGS}\n",
           f"## Sliding-window coverage (width {window})\n",
           "| window end | coverage |",
           "|-----------:|---------:|"]
    for end_i, m in means:
        flag = "  <- worst" if (end_i, m) == (worst_end, worst_val) else ""
        out.append(f"| rows {end_i - window + 1}-{end_i} | {m:.2f}{flag} |")
    out.append(
        f"\nThe dip localizes the bad batch to rows {worst_end - window + 1}-"
        f"{worst_end} (coverage {worst_val:.2f}) — invisible in the global mean "
        f"of {sum(coverage)/len(coverage):.2f}.\n")
    out.append(f"## Longest clean run (<= {max_bad} defects)\n")
    out.append(f"longest span with at most {max_bad} untranslated rows: "
               f"**{length} rows** (indices {start}-{end - 1}).")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sliding-window + two-pointer stream metrics.")
    p.add_argument("--window", type=int, default=4)
    p.add_argument("--max-bad", type=int, default=2)
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    print(demo(args.window, args.max_bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
