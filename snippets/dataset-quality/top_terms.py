#!/usr/bin/env python3
"""Top-K terms over a stream with a bounded heap (#7, #15).

WHY THIS EXISTS
---------------
"Which source terms are most often left untranslated?" is a ranking question over
a corpus that may not fit in memory. The lazy answer is to count everything into a
dict and sort it — O(U log U) in the number of *unique* terms, and it holds every
term in RAM. When you only want the top 20, that is wasteful.

The streaming answer keeps a min-heap of size K. Each counted term is pushed; once
the heap is full, a new term only displaces the current smallest. Memory is O(K),
not O(U), and the per-term cost is O(log K). For K=20 over millions of terms that
is the difference between a tool you can run on a laptop and one you can't.

This is the classic "top-K" pattern, here doing a real corpus job: surfacing the
highest-impact untranslated terms so glossary work can be prioritized.

WHAT'S HERE
-----------
- stream_untranslated(rows, base, langs) : yield source terms missing a translation
- top_k(counts, k)                       : size-K min-heap selection
- top_k_sorted(counts, k)                : full-sort reference, for parity testing

USAGE
-----
    python top_terms.py                          # demo over a planted corpus
    python top_terms.py corpus.csv --k 10 --base en

Stdlib only (heapq, collections). Python 3.10+.
"""

from __future__ import annotations

import argparse
import csv
import heapq
import io
import sys
from collections import Counter
from pathlib import Path


def top_k(counts: dict[str, int], k: int) -> list[tuple[str, int]]:
    """The K most frequent (term, count) pairs via a bounded min-heap.

    We keep at most K items. The heap orders by (count, term) so the smallest
    count sits at the root; a new item with a higher count pushes it out. Ties
    break on the term for a deterministic result. Returned high-to-low."""
    if k <= 0:
        return []
    heap: list[tuple[int, str]] = []
    for term, c in counts.items():
        if len(heap) < k:
            heapq.heappush(heap, (c, term))
        elif (c, term) > heap[0]:
            heapq.heapreplace(heap, (c, term))   # pop smallest, push new in one step
    return [(term, c) for c, term in sorted(heap, reverse=True)]


def top_k_sorted(counts: dict[str, int], k: int) -> list[tuple[str, int]]:
    """Reference implementation: sort everything, take K. Same answer as top_k,
    used in tests to prove the heap version is a faithful drop-in."""
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ordered[:max(k, 0)]


def stream_untranslated(rows, base: str, langs: list[str]):
    """Yield one source term per (row, language) where that language's cell is
    empty or copied through unchanged from the base — i.e. effectively missing a
    translation. A generator so the caller never holds the whole corpus."""
    for row in rows:
        src = (row.get(base) or "").strip()
        if not src:
            continue
        for lang in langs:
            tgt = (row.get(lang) or "").strip()
            if not tgt or tgt == src:        # blank or copy-through == untranslated
                yield src


def count_untranslated(rows, base: str, langs: list[str]) -> Counter:
    c: Counter = Counter()
    for term in stream_untranslated(rows, base, langs):
        c[term] += 1
    return c


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------

DEMO_ROWS = [
    {"en": "Cooldown", "ko": "", "ja": ""},          # untranslated x2
    {"en": "Cooldown", "ko": "쿨다운", "ja": ""},      # x1 (ja still missing)
    {"en": "Respawn", "ko": "", "ja": "リスポーン"},   # x1
    {"en": "Respawn", "ko": "", "ja": ""},           # x2
    {"en": "Loot", "ko": "전리품", "ja": "戦利品"},     # fully translated, never yielded
    {"en": "Inventory", "ko": "Inventory", "ja": ""},  # copy-through + missing -> x2
]


def demo(k: int) -> str:
    counts = count_untranslated(DEMO_ROWS, "en", ["ko", "ja"])
    ranked = top_k(counts, k)
    out = ["# Top untranslated source terms (bounded-heap top-K)\n",
           f"unique untranslated terms: {len(counts)}; showing top {k}\n",
           "| rank | term | missing-translation count |",
           "|-----:|------|--------------------------:|"]
    for i, (term, c) in enumerate(ranked, start=1):
        out.append(f"| {i} | `{term}` | {c} |")
    out.append(
        "\nThe heap holds only K entries regardless of corpus size, so this same "
        "code runs over a stream too large to sort in memory. 'Loot' never appears "
        "— it is fully translated — while a copy-through ('Inventory') counts as "
        "missing, the same judgment coverage_bias.py uses.")
    return "\n".join(out)


def read_csv_smart(path: Path) -> tuple[list[dict], list[str]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        raise SystemExit(f"could not decode {path}")
    rows = [dict(r) for r in csv.DictReader(io.StringIO(text))]
    return rows, (list(rows[0].keys()) if rows else [])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Top-K untranslated terms via a bounded heap.")
    p.add_argument("corpus", nargs="?", type=Path, help="CSV corpus (omit for demo)")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--base", default="en", help="source-language column")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if not args.corpus:
        print(demo(args.k))
        return 0

    rows, headers = read_csv_smart(args.corpus)
    langs = [h for h in headers if h != args.base]
    counts = count_untranslated(rows, args.base, langs)
    for i, (term, c) in enumerate(top_k(counts, args.k), start=1):
        print(f"{i:>3}  {c:>6}  {term}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
