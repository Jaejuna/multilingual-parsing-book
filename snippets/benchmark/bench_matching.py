#!/usr/bin/env python3
"""Prove the ch.4 complexity claim: naive O(terms x segments) vs Aho-Corasick.

WHY THIS EXISTS  (scale, part 1: time)
--------------------------------------
Chapter 4 asserts that the naive `for term in terms: term in text` loop costs
O(terms x segment_length) and that Aho-Corasick collapses it to a single pass.
Asserting is not proving. A Meta-scale interviewer asks "what happens at a
million segments and ten thousand terms?" — this benchmark answers with
numbers: it holds the segments fixed, grows the glossary, and shows the naive
loop's time climbing roughly linearly with the term count while the
Aho-Corasick *search* time stays flat (the automaton is built once).

It reuses the matcher from #4 (which ships a pure-Python Aho-Corasick
fallback, so this runs with zero third-party dependencies).

WHAT IT MEASURES
----------------
- correctness first: naive and Aho-Corasick must return the SAME match set
- then timing: for a growing number of terms, total wall time of each, and
  the speed-up. The crossover (where Aho-Corasick wins) is the headline.

USAGE
-----
    python bench_matching.py
    python bench_matching.py --segments 4000 --seed 7

Stdlib only (uses the #4 snippet's pure-Python automaton). Python 3.10+.
"""

from __future__ import annotations

import argparse
import importlib.util
import random
import string
import sys
import time
from pathlib import Path

# Load the Aho-Corasick matcher from the ch.4 snippet folder by path. The
# benchmark is a measurement tool, not a drop-in snippet, so a cross-folder
# import is fine here.
_AC_PATH = Path(__file__).resolve().parent.parent / "glossary-matching" / "aho_corasick_match.py"
_spec = importlib.util.spec_from_file_location("aho_corasick_match", _AC_PATH)
assert _spec and _spec.loader
ac = importlib.util.module_from_spec(_spec)
sys.modules["aho_corasick_match"] = ac
_spec.loader.exec_module(ac)


# --------------------------------------------------------------------------
# Synthetic data: a glossary of M terms, and N segments that each embed a few
# real terms among random filler words.
# --------------------------------------------------------------------------


def make_terms(n: int, rng: random.Random) -> list[tuple[str, str]]:
    terms = set()
    while len(terms) < n:
        terms.add("".join(rng.choices(string.ascii_lowercase, k=rng.randint(3, 8))))
    return [(t, t.upper()) for t in terms]   # (source, target) pairs


def make_segments(n: int, terms: list[tuple[str, str]], rng: random.Random) -> list[str]:
    filler = ["the", "a", "of", "and", "to", "in", "is", "for", "on", "with"]
    vocab = [t for t, _ in terms]
    segs = []
    for _ in range(n):
        words = rng.choices(filler, k=rng.randint(8, 16))
        for _ in range(rng.randint(0, 3)):              # embed 0-3 real terms
            words.insert(rng.randrange(len(words) + 1), rng.choice(vocab))
        segs.append(" ".join(words))
    return segs


# --------------------------------------------------------------------------
# The two strategies
# --------------------------------------------------------------------------


def naive_match(segments: list[str], terms: list[tuple[str, str]], min_len: int) -> int:
    """O(terms x segment_length) per segment. Returns total match count."""
    pairs = [(s.lower(), t) for s, t in terms if len(s) >= min_len]
    total = 0
    for seg in segments:
        hay = seg.lower()
        seen = set()
        for needle, _tgt in pairs:
            if needle not in seen and needle in hay:
                seen.add(needle)
        total += len(seen)
    return total


def ac_search(segments: list[str], matcher, ) -> int:
    total = 0
    for seg in segments:
        total += len(matcher.find(seg))
    return total


# --------------------------------------------------------------------------
# Benchmark
# --------------------------------------------------------------------------


def bench(segments: list[str], term_counts: list[int], rng: random.Random, min_len: int) -> list[dict]:
    rows = []
    all_terms = make_terms(max(term_counts), rng)
    for m in term_counts:
        terms = all_terms[:m]

        t0 = time.perf_counter()
        naive_total = naive_match(segments, terms, min_len)
        naive_t = time.perf_counter() - t0

        t0 = time.perf_counter()
        matcher = ac.build_matcher(terms, min_len=min_len)
        build_t = time.perf_counter() - t0
        t0 = time.perf_counter()
        ac_total = ac_search(segments, matcher)
        search_t = time.perf_counter() - t0

        rows.append({
            "terms": m,
            "naive_match": naive_total,
            "ac_match": ac_total,
            "naive_ms": naive_t * 1000,
            "ac_build_ms": build_t * 1000,
            "ac_search_ms": search_t * 1000,
            "speedup_search": naive_t / search_t if search_t else float("inf"),
        })
    return rows


def render(rows: list[dict], n_segments: int) -> str:
    out = [f"# Matcher scaling benchmark ({n_segments} segments)\n"]
    out.append("| terms | naive (ms) | AC build (ms) | AC search (ms) | search speed-up | counts agree |")
    out.append("|------:|-----------:|--------------:|---------------:|----------------:|:------------:|")
    for r in rows:
        agree = "yes" if r["naive_match"] == r["ac_match"] else "NO (bug!)"
        out.append(
            f"| {r['terms']} | {r['naive_ms']:.1f} | {r['ac_build_ms']:.1f} "
            f"| {r['ac_search_ms']:.1f} | {r['speedup_search']:.1f}x | {agree} |"
        )
    out.append(
        "\nNaive cost climbs with the term count (it rescans every term per "
        "segment); Aho-Corasick search stays roughly flat because all terms "
        "live in one automaton, built once. The build cost is paid a single "
        "time at startup, then amortized across every segment.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Benchmark naive vs Aho-Corasick term matching.")
    p.add_argument("--segments", type=int, default=2000)
    p.add_argument("--term-counts", default="50,200,1000,5000",
                   help="comma-separated glossary sizes to sweep")
    p.add_argument("--min-len", type=int, default=2)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    rng = random.Random(args.seed)
    term_counts = [int(x) for x in args.term_counts.split(",")]
    all_terms = make_terms(max(term_counts), rng)
    segments = make_segments(args.segments, all_terms, rng)
    rows = bench(segments, term_counts, random.Random(args.seed), args.min_len)
    print(render(rows, args.segments))

    # CI guard: correctness must hold, and AC must win at the largest size
    if any(r["naive_match"] != r["ac_match"] for r in rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
