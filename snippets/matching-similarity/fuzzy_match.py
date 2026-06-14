#!/usr/bin/env python3
"""Similarity matching: catch term variants that exact matching (#4) misses.

WHY THIS EXISTS
---------------
Chapter 4's matchers are exact: `cooldown` matches `cooldown` and nothing else.
Real input is messier — `cool-down`, `cooldwn` (typo), `cooldowns` (plural), or a
synonym a glossary never listed. Exact matching silently drops all of these. The
fix is to stop comparing strings and start comparing *representations*: turn each
term into a vector and measure similarity. This is the conceptual on-ramp from
rules to ML — the same move embeddings make, shown at a scale you can read.

We use character n-gram TF-IDF + cosine similarity: no model, no training, no
dependency, but a genuine vector space where `cooldown` and `cooldwn` land close
and `cooldown` and `refund` land far apart.

WHY CHARACTER n-grams (not word embeddings)
-------------------------------------------
- they're robust to typos/morphology: `cooldown` and `cooldwn` share most 3-grams
- they need no pretrained model or training data — important for a portable tool
- they work across the messy, mixed-script terms this book deals with
The trade-off: they capture *surface* similarity, not *meaning*. `big` and
`large` are synonyms but share no characters, so this won't link them — that is
where real (sub)word embeddings earn their weight. Know which problem you have.

USAGE
-----
    python fuzzy_match.py                     # demo over a tiny glossary
    python fuzzy_match.py --threshold 0.4 --query "cooldwn"

Stdlib only (math, collections). Python 3.10+.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import Counter


def char_ngrams(text: str, n: int = 3) -> list[str]:
    """Padded character n-grams, case-folded. Padding (^/$) lets the start and
    end of a token contribute, so short terms still get usable features."""
    s = f"^{text.lower().strip()}$"
    if len(s) <= n:
        return [s]
    return [s[i:i + n] for i in range(len(s) - n + 1)]


class FuzzyMatcher:
    """TF-IDF over character n-grams + cosine similarity.

    Build once over the glossary; query per surface form. IDF is learned from
    the glossary so that n-grams common to every term (e.g. padding) count for
    less than distinctive ones.
    """

    def __init__(self, n: int = 3) -> None:
        self.n = n
        self.idf: dict[str, float] = {}
        self.vectors: dict[str, dict[str, float]] = {}

    def fit(self, terms: list[str]) -> "FuzzyMatcher":
        df: Counter = Counter()
        grams_per_term = {}
        for t in terms:
            grams = char_ngrams(t, self.n)
            grams_per_term[t] = grams
            for g in set(grams):
                df[g] += 1
        n_docs = len(terms) or 1
        # smoothed idf
        self.idf = {g: math.log((1 + n_docs) / (1 + d)) + 1 for g, d in df.items()}
        self.vectors = {t: self._vectorize(grams) for t, grams in grams_per_term.items()}
        return self

    def _vectorize(self, grams: list[str]) -> dict[str, float]:
        tf = Counter(grams)
        return {g: c * self.idf.get(g, 0.0) for g, c in tf.items()}

    def vectorize(self, text: str) -> dict[str, float]:
        # query n-grams reuse the fitted idf (unknown grams get idf 0)
        return {g: c * self.idf.get(g, 0.0)
                for g, c in Counter(char_ngrams(text, self.n)).items()}

    @staticmethod
    def cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        common = set(a) & set(b)
        dot = sum(a[g] * b[g] for g in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def rank(self, query: str) -> list[tuple[str, float]]:
        qv = self.vectorize(query)
        scored = [(t, self.cosine(qv, v)) for t, v in self.vectors.items()]
        return sorted(scored, key=lambda kv: kv[1], reverse=True)

    def match(self, query: str, threshold: float = 0.5) -> tuple[str, float] | None:
        ranked = self.rank(query)
        if ranked and ranked[0][1] >= threshold:
            return ranked[0]
        return None


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------

GLOSSARY = ["cooldown", "respawn", "loot", "AI Director", "checkpoint", "inventory"]
QUERIES = [
    ("cooldown", "exact"),
    ("cool-down", "hyphenated variant"),
    ("cooldwn", "typo"),
    ("cooldowns", "plural"),
    ("respawning", "morphology"),
    ("AI director", "case variant"),
    ("lewt", "leetspeak-ish typo"),
    ("banana", "unrelated"),
]


def exact_hit(query: str, glossary: list[str]) -> bool:
    q = query.lower()
    return any(q == t.lower() for t in glossary)


def demo(threshold: float) -> str:
    m = FuzzyMatcher().fit(GLOSSARY)
    out = ["# Fuzzy term matching (char n-gram TF-IDF + cosine)\n",
           f"glossary: {', '.join(GLOSSARY)}\n",
           "| query | note | exact? | best fuzzy match | score | fuzzy@%.2f |" % threshold,
           "|-------|------|:------:|------------------|------:|:----------:|"]
    for q, note in QUERIES:
        best, score = m.rank(q)[0]
        ex = "hit" if exact_hit(q, GLOSSARY) else "miss"
        fuzzy = "match" if score >= threshold else "—"
        out.append(f"| `{q}` | {note} | {ex} | `{best}` | {score:.2f} | {fuzzy} |")
    out.append(
        "\nExact matching only fires on the first row. Fuzzy matching also "
        "recovers the hyphenated/typo/plural/morphology/case variants, while "
        "still rejecting the unrelated word — surface similarity, no model "
        "required. It will NOT catch true synonyms with no shared characters; "
        "that needs real embeddings.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fuzzy term matching via char n-gram TF-IDF.")
    p.add_argument("--threshold", type=float, default=0.5)
    p.add_argument("--query", help="match a single query against the demo glossary")
    p.add_argument("--n", type=int, default=3, help="character n-gram size")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if args.query:
        m = FuzzyMatcher(n=args.n).fit(GLOSSARY)
        for term, score in m.rank(args.query)[:5]:
            mark = "  <- match" if score >= args.threshold else ""
            print(f"{score:.3f}  {term}{mark}")
        return 0

    print(demo(args.threshold))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
