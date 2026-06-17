#!/usr/bin/env python3
"""Sequence DP: LCS for diffing/aligning, LIS for monotone runs (#18, #20).

WHY THIS EXISTS
---------------
Edit distance (edit_distance.py) is one member of a family of dynamic-programming
problems over sequences; two others come up constantly in corpus work:

- **Longest Common Subsequence (LCS).** When a glossary or a translation is
  revised, you want the *diff*: which tokens survived, which were inserted or
  dropped. LCS is the backbone of `diff` — the longest order-preserving set of
  shared tokens is the "unchanged" spine, and everything off it is an edit. It
  also aligns a source and target token stream when you need a rough word
  correspondence without a model.
- **Longest Increasing Subsequence (LIS).** Given a per-batch quality score
  stream, the longest non-decreasing run is "how long did quality hold or improve
  before regressing" — a monotone-trend probe. LIS in O(n log n) is a classic that
  pairs a greedy "tails" array with binary search.

Both are textbook DP; here they earn their place doing real corpus jobs.

WHAT'S HERE
-----------
- lcs(a, b)          : the longest common subsequence (reconstructed, not just its length)
- lcs_length(a, b)   : just the length (the DP table's corner)
- lis(xs)            : a longest strictly-increasing subsequence (O(n log n))

USAGE
-----
    python sequence_align.py                 # demo: token diff + quality run
    python sequence_align.py --lcs "a b c d" "a x c d"

Stdlib only (bisect). Python 3.10+.
"""

from __future__ import annotations

import argparse
import bisect
import sys


def lcs_length(a: list, b: list) -> int:
    """Length of the longest common subsequence via the classic 2D DP, kept to two
    rolling rows for O(min(len)) space. dp[j] = LCS of a[:i] and b[:j]."""
    if len(b) > len(a):
        a, b = b, a
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, start=1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def lcs(a: list, b: list) -> list:
    """The longest common subsequence itself, recovered by walking the full DP
    table back from the corner: equal tokens step diagonally (and are part of the
    LCS), otherwise follow the larger neighbour. O(len(a)*len(b)) time and space."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = (dp[i - 1][j - 1] + 1 if a[i - 1] == b[j - 1]
                        else max(dp[i - 1][j], dp[i][j - 1]))
    out: list = []
    i, j = n, m
    while i and j:
        if a[i - 1] == b[j - 1]:
            out.append(a[i - 1])
            i, j = i - 1, j - 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return list(reversed(out))


def lis(xs: list[float]) -> list[float]:
    """A longest strictly-increasing subsequence, in O(n log n). `tails[k]` holds
    the smallest possible tail of an increasing subsequence of length k+1; each
    value either extends the longest run (append) or improves a tail (binary-search
    replace). `prev` links let us reconstruct one actual subsequence at the end."""
    tails_idx: list[int] = []          # indices into xs, one per length
    prev = [-1] * len(xs)
    tail_vals: list[float] = []
    for i, v in enumerate(xs):
        pos = bisect.bisect_left(tail_vals, v)
        if pos == len(tail_vals):
            tail_vals.append(v)
            tails_idx.append(i)
        else:
            tail_vals[pos] = v
            tails_idx[pos] = i
        prev[i] = tails_idx[pos - 1] if pos > 0 else -1
    # reconstruct from the last index of the longest run
    out: list[float] = []
    k = tails_idx[-1] if tails_idx else -1
    while k != -1:
        out.append(xs[k])
        k = prev[k]
    return list(reversed(out))


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------

OLD_TOKENS = ["the", "ancient", "loot", "chest", "respawns"]
NEW_TOKENS = ["the", "rare", "loot", "chest", "now", "respawns"]
QUALITY = [0.62, 0.55, 0.66, 0.70, 0.68, 0.74, 0.80, 0.79]


def demo() -> str:
    common = lcs(OLD_TOKENS, NEW_TOKENS)
    common_set = _diff_marks(OLD_TOKENS, NEW_TOKENS, common)
    run = lis(QUALITY)
    out = ["# Sequence DP: LCS diff + LIS trend\n",
           "## LCS — token-level diff between two revisions\n",
           f"- old: {' '.join(OLD_TOKENS)}",
           f"- new: {' '.join(NEW_TOKENS)}",
           f"- unchanged spine (LCS): {' '.join(common)}",
           f"- diff: {common_set}\n",
           "## LIS — longest non-regressing quality run\n",
           f"- per-batch scores: {QUALITY}",
           f"- longest increasing run: {run}  (length {len(run)})\n",
           "LCS finds the order-preserving shared tokens (the basis of `diff`); "
           "everything off that spine is an insertion or deletion. LIS finds the "
           "longest stretch where quality only improved — both are the same "
           "build-a-table-then-walk-it DP pattern as edit distance."]
    return "\n".join(out)


def _diff_marks(old: list[str], new: list[str], common: list[str]) -> str:
    """Render a compact diff using the LCS as the unchanged anchor."""
    keep = set(common)
    dropped = [t for t in old if t not in keep]
    added = [t for t in new if t not in keep]
    parts = []
    if dropped:
        parts.append("-[" + ", ".join(dropped) + "]")
    if added:
        parts.append("+[" + ", ".join(added) + "]")
    return " ".join(parts) or "(identical)"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="LCS / LIS sequence DP utilities.")
    p.add_argument("--lcs", nargs=2, metavar=("A", "B"),
                   help="longest common subsequence of two space-separated token strings")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if args.lcs:
        a, b = (s.split() for s in args.lcs)
        print(" ".join(lcs(list(a), list(b))))
        return 0

    print(demo())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
