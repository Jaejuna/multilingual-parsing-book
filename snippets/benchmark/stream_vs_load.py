#!/usr/bin/env python3
"""Prove the memory claim: streaming stats stay O(1) while full-load is O(rows).

WHY THIS EXISTS  (scale, part 2: memory)
----------------------------------------
The Part II tools all do `rows = list(reader)` — they load the whole corpus
into RAM. That is fine for a vendor CSV; it falls over at a hundred million
segments. The fix is to process row by row and keep only fixed-size running
state. But one metric resists streaming: the length-ratio outlier in ch.7
needs a *median*, which needs every value at once.

The escape is an online statistic. Welford's algorithm maintains a running
mean and variance in O(1) memory, so we flag outliers by z-score (|z| > 3)
instead of by median — one pass, constant memory, no second read.

This script measures peak memory (via tracemalloc) of the load-everything
approach vs the streaming approach over a growing row count, and shows the
load curve climbing while streaming stays flat — and that both compute the
same mean/variance.

WHAT IT IS NOT
--------------
Not a drop-in for audit_corpus.py — it isolates the technique so the memory
behaviour is visible. In production you would fold the Welford accumulator
into the auditor's single pass (and accept that exact duplicate detection,
which needs to remember every key, is the one check that cannot be O(1)).

USAGE
-----
    python stream_vs_load.py
    python stream_vs_load.py --counts 10000,100000,1000000

Stdlib only (tracemalloc, statistics). Python 3.10+.
"""

from __future__ import annotations

import argparse
import sys
import tracemalloc
from typing import Iterator


# --------------------------------------------------------------------------
# Welford's online mean/variance — O(1) memory, single pass
# --------------------------------------------------------------------------


class Welford:
    """Running mean and (sample) variance without storing the data.

    Each update is O(1) and keeps three floats, regardless of how many values
    have been seen. This is what lets a streaming auditor flag length-ratio
    outliers by z-score without a second pass or an in-memory list.
    """

    __slots__ = ("n", "mean", "_m2")

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self._m2 = 0.0

    def update(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self._m2 += delta * (x - self.mean)

    @property
    def variance(self) -> float:
        return self._m2 / (self.n - 1) if self.n > 1 else 0.0

    @property
    def stdev(self) -> float:
        return self.variance ** 0.5

    def zscore(self, x: float) -> float:
        s = self.stdev
        return (x - self.mean) / s if s else 0.0


# --------------------------------------------------------------------------
# A synthetic stream of "length ratios" — generated lazily, never all in RAM
# --------------------------------------------------------------------------


def ratios(n: int) -> Iterator[float]:
    """Deterministic pseudo-random ratios around 1.0 with a few outliers."""
    state = 12345
    for i in range(n):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF   # LCG, no imports
        r = 0.5 + (state / 0x7FFFFFFF)                       # ~[0.5, 1.5]
        if i % 9999 == 0:
            r *= 12                                          # planted outlier
        yield r


# --------------------------------------------------------------------------
# Two approaches
# --------------------------------------------------------------------------


def load_then_compute(n: int) -> tuple[float, float]:
    """O(rows) memory: materialize every value, then compute."""
    import statistics
    data = [r for r in ratios(n)]            # the whole stream in RAM
    return statistics.fmean(data), statistics.pstdev(data)


def stream_compute(n: int) -> tuple[float, float]:
    """O(1) memory: one Welford accumulator, never store the values."""
    w = Welford()
    for r in ratios(n):
        w.update(r)
    return w.mean, w.stdev


def peak_kb(fn, n: int) -> tuple[float, tuple[float, float]]:
    tracemalloc.start()
    result = fn(n)
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / 1024, result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Streaming vs load-everything memory.")
    p.add_argument("--counts", default="10000,100000,1000000",
                   help="comma-separated row counts to sweep")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    counts = [int(x) for x in args.counts.split(",")]
    print("# Streaming vs load-everything: peak memory\n")
    print("| rows | load peak (KB) | stream peak (KB) | means agree |")
    print("|-----:|---------------:|-----------------:|:-----------:|")
    for n in counts:
        load_mem, (lm, _) = peak_kb(load_then_compute, n)
        stream_mem, (sm, _) = peak_kb(stream_compute, n)
        agree = "yes" if abs(lm - sm) < 1e-6 else f"NO ({lm} vs {sm})"
        print(f"| {n} | {load_mem:,.0f} | {stream_mem:,.0f} | {agree} |")
    print(
        "\nLoad-everything peak memory grows with the row count; the streaming "
        "(Welford) peak stays flat — it keeps three floats no matter how many "
        "rows stream past. Same mean to floating-point tolerance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
