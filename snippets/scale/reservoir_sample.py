#!/usr/bin/env python3
"""Reservoir sampling: a uniform sample from a stream of unknown size (#15).

WHY THIS EXISTS
---------------
You want a 200-row spot-check set drawn uniformly at random from a corpus, but the
corpus is a stream you read once and it is too big to hold in memory — so you
cannot count it first, then pick indices. Reservoir sampling (Algorithm R) solves
exactly this: it makes one pass, keeps K items, and guarantees every item seen has
the same K/N probability of ending up in the sample, *without ever knowing N in
advance*.

The trick: keep the first K items. For the i-th item after that (i is 1-based over
the whole stream), keep it with probability K/i, and if kept, have it evict a
uniformly random current resident. A short induction shows every item ends at
probability K/N. Memory is O(K); time is O(N).

This is the sampling counterpart to the streaming stats (Welford) and out-of-core
coverage tools: same "one pass, bounded memory" discipline, applied to building an
unbiased evaluation subset.

WHEN NOT TO
-----------
If the data fits in memory, `random.sample(list(stream), k)` is simpler. Reservoir
sampling earns its place only when you cannot materialize the stream or do not
know its length up front. For *weighted* sampling, use the A-Res variant (one
exponential key per item, keep the K largest) — noted but not implemented here.

USAGE
-----
    python reservoir_sample.py                 # demo: draw + prove uniformity
    python reservoir_sample.py --k 5 --n 1000 --seed 7

Stdlib only (random). Python 3.10+.
"""

from __future__ import annotations

import argparse
import random
import sys
from typing import Iterable, TypeVar

T = TypeVar("T")


def reservoir_sample(stream: Iterable[T], k: int, rng: random.Random) -> list[T]:
    """Algorithm R: a uniform size-k sample over a single pass of `stream`.

    Fill the reservoir with the first k items. From then on, item i (1-based) is
    admitted with probability k/i; rng.randrange(i) picks a slot in [0, i), and
    only when that slot is < k does the new item replace the resident there —
    which happens with probability exactly k/i."""
    if k <= 0:
        return []
    reservoir: list[T] = []
    for i, item in enumerate(stream):
        if i < k:
            reservoir.append(item)
        else:
            j = rng.randrange(i + 1)     # uniform in [0, i]
            if j < k:
                reservoir[j] = item
    return reservoir


# --------------------------------------------------------------------------
# Demo: draw a sample, then empirically confirm uniformity
# --------------------------------------------------------------------------


def inclusion_rates(n: int, k: int, trials: int, seed: int) -> list[float]:
    """Run the sampler `trials` times over range(n) and report, per element, the
    fraction of trials it appeared in. With true uniformity each rate tends to
    k/n; the spread shrinks as trials grow. This is how you sanity-check a
    sampler you cannot inspect analytically."""
    rng = random.Random(seed)
    counts = [0] * n
    for _ in range(trials):
        for x in reservoir_sample(range(n), k, rng):
            counts[x] += 1
    return [c / trials for c in counts]


def demo(n: int, k: int, seed: int) -> str:
    rng = random.Random(seed)
    sample = sorted(reservoir_sample(range(n), k, rng))
    trials = 4000
    rates = inclusion_rates(n, k, trials, seed)
    expected = k / n
    worst = max(abs(r - expected) for r in rates)
    avg = sum(rates) / len(rates)
    out = ["# Reservoir sampling (Algorithm R)\n",
           f"stream length N={n}, reservoir K={k}, one pass\n",
           f"one sample (seed {seed}): {sample}\n",
           "## Uniformity check\n",
           f"- expected inclusion probability K/N = {expected:.4f}",
           f"- mean observed over {trials} trials = {avg:.4f}",
           f"- worst per-element deviation = {worst:.4f}\n",
           "Every element lands in the sample with ~K/N probability though the "
           "sampler never sees N up front and holds only K items at a time — the "
           "property that lets it run over a stream too large to count, let alone "
           "store."]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Uniform reservoir sampling over a stream.")
    p.add_argument("--k", type=int, default=8, help="reservoir size")
    p.add_argument("--n", type=int, default=200, help="demo stream length")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    print(demo(args.n, args.k, args.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
