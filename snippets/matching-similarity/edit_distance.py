#!/usr/bin/env python3
"""Edit distance: a bounded, character-level companion to fuzzy matching (#4).

WHY THIS EXISTS
---------------
The char n-gram cosine matcher (see fuzzy_match.py) is great at ranking, but it
answers "how similar?" with a fuzzy score, not "how many edits apart?" with a
hard number. For typo correction you often want the latter: "accept a candidate
only if it is within 1 edit of a real term." That is Levenshtein distance — the
minimum number of single-character insertions, deletions, or substitutions to
turn one string into another.

It also gives you a cheap, explainable spell-correct: map `cooldwn` -> `cooldown`
because they are exactly one deletion apart, with no model and no training.

WHAT'S HERE
-----------
- levenshtein(a, b)            : full distance, O(len(a)*len(b)) time, O(min) space
- bounded_levenshtein(a,b,max) : early-exit version — stops once it is certain the
                                 distance exceeds `max`, so screening a big term
                                 list against a query stays cheap
- similarity(a, b)             : distance turned into a 0..1 score
- closest(query, terms, max)   : nearest term within an edit budget, or None

WHEN NOT TO
-----------
Edit distance measures *surface* edits, like the n-gram matcher — `big`/`large`
are zero similarity. And it is character-blind to meaning: `1000`/`l000` are one
edit apart but so are `cat`/`car`. Use the budget (`max`) to keep it honest, and
reach for embeddings when you need meaning, not spelling.

USAGE
-----
    python edit_distance.py                       # demo over a tiny glossary
    python edit_distance.py --query cooldwn --max 2
    python edit_distance.py --pair cooldown cooldwn

Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata


def fold(s: str) -> str:
    """NFKC + casefold so distance is width- and case-insensitive: 'ＡＩ' == 'AI',
    'STRASSE' == 'straße'. casefold(), not lower(). See Appendix B field notes."""
    return unicodedata.normalize("NFKC", s).casefold()


def levenshtein(a: str, b: str, *, normalize: bool = True) -> int:
    """Levenshtein distance via the classic DP, kept to two rolling rows so the
    memory is O(min(len(a), len(b))) instead of the full matrix."""
    if normalize:
        a, b = fold(a), fold(b)
    if a == b:
        return 0
    if len(a) < len(b):          # iterate over the shorter string's columns
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1,        # deletion
                           cur[j - 1] + 1,     # insertion
                           prev[j - 1] + cost))  # substitution / match
        prev = cur
    return prev[-1]


def bounded_levenshtein(a: str, b: str, max_dist: int, *, normalize: bool = True) -> int:
    """Like levenshtein, but returns max_dist + 1 as soon as it is provable the
    true distance is greater. The length gap alone can settle it; otherwise each
    DP row is checked — if its smallest value already exceeds max_dist, no later
    row can recover, so we bail. Turns an O(n*m) screen into a near-linear one for
    the common 'is this within k edits?' question."""
    if normalize:
        a, b = fold(a), fold(b)
    if abs(len(a) - len(b)) > max_dist:
        return max_dist + 1
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a) if len(a) <= max_dist else max_dist + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        if min(cur) > max_dist:           # whole row already over budget
            return max_dist + 1
        prev = cur
    return prev[-1] if prev[-1] <= max_dist else max_dist + 1


def similarity(a: str, b: str) -> float:
    """Distance as a 0..1 score: 1.0 is identical, 0.0 shares nothing. Normalized
    by the longer length so it compares across term sizes."""
    longest = max(len(fold(a)), len(fold(b)))
    if longest == 0:
        return 1.0
    return 1.0 - levenshtein(a, b) / longest


def closest(query: str, terms: list[str], max_dist: int = 2) -> tuple[str, int] | None:
    """Nearest term within `max_dist` edits, ties broken by glossary order. Uses
    the bounded form so most non-candidates are rejected on length alone."""
    best: tuple[str, int] | None = None
    for t in terms:
        d = bounded_levenshtein(query, t, max_dist)
        if d <= max_dist and (best is None or d < best[1]):
            best = (t, d)
            if d == 0:
                break
    return best


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------

GLOSSARY = ["cooldown", "respawn", "loot", "checkpoint", "inventory", "AI Director"]
QUERIES = [
    ("cooldown", "exact"),
    ("cooldwn", "1 deletion"),
    ("cooldwon", "1 transposition (= 2 edits for Levenshtein)"),
    ("respwn", "1 deletion"),
    ("inventary", "1 substitution"),
    ("loots", "1 insertion (plural)"),
    ("banana", "unrelated"),
]


def demo(max_dist: int) -> str:
    out = ["# Edit distance for typo-tolerant term lookup\n",
           f"glossary: {', '.join(GLOSSARY)}\n",
           f"edit budget: <= {max_dist}\n",
           "| query | note | nearest term | edits | within budget? |",
           "|-------|------|--------------|------:|:--------------:|"]
    for q, note in QUERIES:
        hit = closest(q, GLOSSARY, max_dist)
        if hit:
            out.append(f"| `{q}` | {note} | `{hit[0]}` | {hit[1]} | yes |")
        else:
            # show how far the true nearest was, to make the rejection legible
            near = min(((levenshtein(q, t), t) for t in GLOSSARY), default=(0, ""))
            out.append(f"| `{q}` | {note} | (`{near[1]}`, {near[0]} edits) | — | no |")
    out.append(
        "\nA tight budget recovers single-character typos while rejecting the "
        "unrelated word. Note the transposition costs 2 plain Levenshtein edits; "
        "if transpositions are common in your input, Damerau-Levenshtein folds "
        "them into a single edit.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Levenshtein edit distance utilities.")
    p.add_argument("--query", help="find the nearest demo-glossary term")
    p.add_argument("--pair", nargs=2, metavar=("A", "B"), help="distance between two strings")
    p.add_argument("--max", type=int, default=2, help="edit budget for lookup")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if args.pair:
        a, b = args.pair
        print(f"levenshtein({a!r}, {b!r}) = {levenshtein(a, b)}  "
              f"(similarity {similarity(a, b):.2f})")
        return 0

    if args.query:
        hit = closest(args.query, GLOSSARY, args.max)
        print(f"{args.query!r} -> {hit}" if hit
              else f"{args.query!r}: no term within {args.max} edits")
        return 0

    print(demo(args.max))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
