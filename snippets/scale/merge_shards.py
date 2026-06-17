#!/usr/bin/env python3
"""K-way merge of pre-sorted corpus shards with a heap (#15).

WHY THIS EXISTS
---------------
When a corpus is too big to sort in memory, the standard move is external sort:
split it into shards small enough to sort individually, write each out sorted,
then merge the sorted shards back into one sorted stream. The merge is the
interesting half — and the part that has to stay streaming, because the whole
reason you sharded is that the full data never fits in RAM at once.

A k-way merge does this with a min-heap of size K (one slot per shard). The heap's
root is always the next smallest unconsumed item across all shards; you emit it
and pull the next item from the shard it came from. Memory is O(K) — one item per
shard, not the whole dataset — and the total work is O(N log K).

This is what `heapq.merge` does under the hood; the point here is to show the
mechanism (so it is yours to adapt — custom keys, dedup-on-merge, tie-breaking)
and to prove it against a reference, not to replace the stdlib.

WHEN NOT TO
-----------
If everything already fits in memory, `sorted(itertools.chain(*shards))` is
simpler and fine. The heap pays off only when shards arrive as streams you cannot
fully materialize, or there are too many to concatenate.

USAGE
-----
    python merge_shards.py                        # demo: merge 4 sorted shards
    python merge_shards.py --shards 8 --per 1000  # bigger, checks against sorted()

Stdlib only (heapq). Python 3.10+.
"""

from __future__ import annotations

import argparse
import heapq
import sys
from typing import Callable, Iterable, Iterator


def kway_merge(shards: list[Iterable], key: Callable = lambda x: x) -> Iterator:
    """Yield items from the already-sorted `shards` in globally sorted order.

    The heap holds one (key, shard_index, item) tuple per live shard. shard_index
    is in the tuple as a tiebreaker so two equal keys never force a comparison of
    the items themselves (which may be unorderable). When a shard's item is
    emitted, its iterator is advanced and the next item, if any, re-enters the
    heap. Constant memory in the data size: at most one item per shard at a time."""
    iters = [iter(s) for s in shards]
    heap: list[tuple] = []
    for idx, it in enumerate(iters):
        first = next(it, _SENTINEL)
        if first is not _SENTINEL:
            heapq.heappush(heap, (key(first), idx, first))
    while heap:
        _, idx, item = heapq.heappop(heap)
        yield item
        nxt = next(iters[idx], _SENTINEL)
        if nxt is not _SENTINEL:
            heapq.heappush(heap, (key(nxt), idx, nxt))


_SENTINEL = object()


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------


def make_shards(n_shards: int, per_shard: int) -> list[list[int]]:
    """Deterministically build n_shards lists, each internally sorted, with values
    interleaved across shards so the merge actually has to choose between them.
    No RNG — a simple stride keeps it reproducible and dependency-free."""
    shards = []
    for s in range(n_shards):
        shard = sorted(s + n_shards * i for i in range(per_shard))
        shards.append(shard)
    return shards


def demo(n_shards: int, per_shard: int) -> str:
    shards = make_shards(n_shards, per_shard)
    merged = list(kway_merge(shards))
    reference = sorted(v for shard in shards for v in shard)
    ok = merged == reference
    preview = ", ".join(map(str, merged[:12]))
    out = ["# K-way merge of sorted shards (heap)\n",
           f"shards: {n_shards}, items/shard: {per_shard}, total: {len(merged)}\n",
           f"first 12 merged: {preview}, ...\n",
           f"matches a full sort of the concatenation: **{ok}**\n",
           "Peak heap size stays at one entry per shard "
           f"({n_shards}), independent of the {len(merged)} total items — that is "
           "what lets this run when the data itself never fits in memory."]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Heap-based k-way merge of sorted shards.")
    p.add_argument("--shards", type=int, default=4)
    p.add_argument("--per", type=int, default=8, help="items per shard")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    shards = make_shards(args.shards, args.per)
    merged = list(kway_merge(shards))
    if merged != sorted(v for s in shards for v in s):   # pragma: no cover
        print("ERROR: merge did not match a full sort", file=sys.stderr)
        return 1
    print(demo(args.shards, args.per))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
