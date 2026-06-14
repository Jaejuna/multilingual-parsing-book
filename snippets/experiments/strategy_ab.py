#!/usr/bin/env python3
"""A/B a matcher change against a labeled gold set before you ship it.

WHY THIS EXISTS  (experiment design)
------------------------------------
README #4 lists matching strategies -- plain substring, word-boundary,
min-length guard -- and notes the trade-offs in prose. Prose is where bad
decisions hide. Before swapping the production matcher from "substring" to
"word-boundary" you owe product a number: does it actually cut false
positives, and what does it cost in recall? This harness turns the #4
options into a controlled experiment with precision / recall / F1 over a
labeled set, so the rollout decision is evidence, not vibes.

This is the "Design and conduct product experiments" muscle: a fixed gold
set (the control), several candidate strategies (the variants), one metric
table, one winner.

THE GOLD SET
------------
A CSV of judgements -- "in this text, should this term count as a hit?"

    text,term,gold
    "He Said yes",AI,0          # 'AI' inside 'Said' is NOT a real hit
    "AI Director rules",AI,1     # real hit
    "冷却を減らす",冷却,1          # CJK: substring is correct

gold = 1 means a correct matcher SHOULD report the term present.

USAGE
-----
    python strategy_ab.py gold.csv
    python strategy_ab.py gold.csv --min-len 3 --format json

Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import random
import re
import sys
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable


# --------------------------------------------------------------------------
# Helpers (shared lessons with the rest of the book)
# --------------------------------------------------------------------------


def read_csv_smart(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        raise SystemExit(f"could not decode {path}")
    return [dict(r) for r in csv.DictReader(io.StringIO(text))]


def is_cjk(text: str) -> bool:
    for ch in text:
        name = unicodedata.name(ch, "")
        if any(t in name for t in ("CJK", "HIRAGANA", "KATAKANA", "HANGUL")):
            return True
    return False


# --------------------------------------------------------------------------
# The candidate strategies (the README #4 menu, made executable)
# --------------------------------------------------------------------------

Strategy = Callable[[str, str], bool]


def fold(s: str) -> str:
    """NFKC + casefold for caseless, width-insensitive matching (full-width
    'ＡＩ' -> 'AI'; ß -> ss). casefold(), not lower(); on match keys only."""
    return unicodedata.normalize("NFKC", s).casefold()


def strat_substring(text: str, term: str) -> bool:
    """#4.1 -- plain case-insensitive substring. Fast, over-fires in Latin."""
    return fold(term) in fold(text)


def strat_word_boundary(text: str, term: str) -> bool:
    """#4.2(1) -- \\b for Latin scripts, substring for CJK (no boundaries)."""
    t = fold(term)
    if is_cjk(term):
        return t in fold(text)
    return re.search(rf"\b{re.escape(t)}\b", fold(text)) is not None


def make_word_boundary_minlen(min_len: int) -> Strategy:
    """#4.2(2) -- word-boundary plus a min-length guard against 1-2 char noise."""
    def strat(text: str, term: str) -> bool:
        if not is_cjk(term) and len(term) < min_len:
            return False
        return strat_word_boundary(text, term)
    return strat


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


@dataclass
class Scores:
    name: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d.update(precision=round(self.precision, 4),
                 recall=round(self.recall, 4),
                 f1=round(self.f1, 4))
        return d


def run_experiment(gold: list[dict[str, str]], strategies: dict[str, Strategy]) -> dict[str, Scores]:
    results = {name: Scores(name=name) for name in strategies}
    for row in gold:
        text = row.get("text") or ""
        term = (row.get("term") or "").strip()
        label = (row.get("gold") or "").strip() in ("1", "true", "True", "yes")
        for name, strat in strategies.items():
            pred = strat(text, term)
            s = results[name]
            if pred and label:
                s.tp += 1
            elif pred and not label:
                s.fp += 1
            elif not pred and label:
                s.fn += 1
            else:
                s.tn += 1
    return results


# --------------------------------------------------------------------------
# Statistical significance: is the F1 gap real, or is the sample too small?
# --------------------------------------------------------------------------
#
# README #10 produced a clean table — word_boundary 100%, substring 78% — but a
# table is a point estimate. With 13 gold judgements that gap could be luck.
# This is where most "we A/B'd it" claims quietly fall apart, so the harness
# now reports uncertainty, not just the winner.


def predictions(gold: list[dict[str, str]],
                strategies: dict[str, Strategy]) -> dict[str, list[tuple[bool, bool]]]:
    """Per-item (predicted, label) pairs per strategy — paired tests need the
    item-level alignment that the aggregate Scores throw away."""
    out: dict[str, list[tuple[bool, bool]]] = {name: [] for name in strategies}
    for row in gold:
        text = row.get("text") or ""
        term = (row.get("term") or "").strip()
        label = (row.get("gold") or "").strip() in ("1", "true", "True", "yes")
        for name, strat in strategies.items():
            out[name].append((strat(text, term), label))
    return out


def f1_of(pairs: list[tuple[bool, bool]]) -> float:
    tp = sum(1 for p, l in pairs if p and l)
    fp = sum(1 for p, l in pairs if p and not l)
    fn = sum(1 for p, l in pairs if not p and l)
    pr = tp / (tp + fp) if (tp + fp) else 0.0
    rc = tp / (tp + fn) if (tp + fn) else 0.0
    return 2 * pr * rc / (pr + rc) if (pr + rc) else 0.0


def mcnemar_exact(a: list[tuple[bool, bool]],
                  b: list[tuple[bool, bool]]) -> tuple[int, int, float]:
    """Exact (binomial) McNemar test on two strategies' per-item correctness.

    Only *discordant* items matter — where one strategy is right and the other
    wrong. Under the null (the two are equally good) each discordant item is a
    coin flip, so their split follows Binomial(b+c, 0.5). The exact two-sided
    p-value avoids the chi-square approximation, which is unreliable at the
    small n this kind of gold set usually has.
    """
    b_only = c_only = 0
    for (pa, la), (pb, lb) in zip(a, b):
        ca, cb = (pa == la), (pb == lb)
        if ca and not cb:
            b_only += 1
        elif cb and not ca:
            c_only += 1
    n = b_only + c_only
    if n == 0:
        return b_only, c_only, 1.0
    k = min(b_only, c_only)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return b_only, c_only, min(2 * tail, 1.0)


def bootstrap_f1_ci(pairs: list[tuple[bool, bool]], n_boot: int = 2000,
                    alpha: float = 0.05, seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap CI for F1: resample items with replacement, recompute
    F1, take the middle (1-alpha). A wide interval is the honest signal that the
    point estimate isn't trustworthy yet."""
    rng = random.Random(seed)
    n = len(pairs)
    f1s = sorted(f1_of([pairs[rng.randrange(n)] for _ in range(n)])
                 for _ in range(n_boot))
    lo = f1s[int((alpha / 2) * n_boot)]
    hi = f1s[min(int((1 - alpha / 2) * n_boot), n_boot - 1)]
    return lo, hi


def significance_report(gold: list[dict[str, str]], strategies: dict[str, Strategy],
                        n_boot: int = 2000, alpha: float = 0.05, seed: int = 0) -> str:
    preds = predictions(gold, strategies)
    ranked = sorted(strategies, key=lambda nm: f1_of(preds[nm]), reverse=True)
    out: list[str] = []
    w = out.append
    w("# Significance\n")
    w(f"- gold judgements: **{len(gold)}**\n")
    w("## 95% bootstrap CI for F1\n")
    w("| strategy | F1 | 95% CI |")
    w("|----------|----|--------|")
    for nm in ranked:
        lo, hi = bootstrap_f1_ci(preds[nm], n_boot=n_boot, alpha=alpha, seed=seed)
        w(f"| `{nm}` | {f1_of(preds[nm]):.1%} | [{lo:.1%}, {hi:.1%}] |")
    w("")
    top, second = ranked[0], ranked[1]
    b, c, pval = mcnemar_exact(preds[top], preds[second])
    w(f"## McNemar: `{top}` vs `{second}`\n")
    w(f"- discordant items: `{top}` right & `{second}` wrong = {b}; reverse = {c}")
    verdict = "significant" if pval < alpha else "NOT significant"
    w(f"- exact two-sided p = **{pval:.4f}** → **{verdict}** at α={alpha}")
    if pval >= alpha:
        w(f"- the F1 gap is within noise at this sample size; collect more gold "
          f"before claiming `{top}` beats `{second}`.")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def to_markdown(results: dict[str, Scores], n: int) -> str:
    out: list[str] = []
    w = out.append
    w("# Matcher A/B experiment\n")
    w(f"- gold judgements: **{n}**")
    winner = max(results.values(), key=lambda s: s.f1)
    w(f"- winner by F1: **{winner.name}** ({winner.f1:.1%})\n")

    w("## Results\n")
    w("| strategy | precision | recall | F1 | FP | FN |")
    w("|----------|-----------|--------|----|----|----|")
    for s in sorted(results.values(), key=lambda s: s.f1, reverse=True):
        mark = " 🏆" if s.name == winner.name else ""
        w(f"| `{s.name}`{mark} | {s.precision:.1%} | {s.recall:.1%} "
          f"| {s.f1:.1%} | {s.fp} | {s.fn} |")
    w("")
    w("> FP = false positives (over-firing, e.g. 'AI' inside 'Said'); "
      "FN = false negatives (a real term missed). The right trade-off is "
      "product-dependent -- a glossary augmenter usually prefers high "
      "precision so it doesn't inject spurious terms.\n")
    return "\n".join(out)


def to_json(results: dict[str, Scores]) -> str:
    return json.dumps({k: v.as_dict() for k, v in results.items()},
                      ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="A/B test glossary matching strategies.")
    p.add_argument("gold", type=Path, help="labeled CSV: text,term,gold")
    p.add_argument("--min-len", type=int, default=3,
                   help="min-length guard for the guarded strategy (default 3)")
    p.add_argument("--significance", action="store_true",
                   help="add McNemar test + bootstrap CIs (is the gap real?)")
    p.add_argument("--n-boot", type=int, default=2000, help="bootstrap resamples")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--format", choices=["md", "json"], default="md")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if not args.gold.exists():
        print(f"file not found: {args.gold}", file=sys.stderr)
        return 2

    gold = read_csv_smart(args.gold)
    strategies: dict[str, Strategy] = {
        "substring": strat_substring,
        "word_boundary": strat_word_boundary,
        f"word_boundary+min{args.min_len}": make_word_boundary_minlen(args.min_len),
    }
    results = run_experiment(gold, strategies)
    print(to_markdown(results, len(gold)) if args.format == "md" else to_json(results))
    if args.significance:
        print("\n" + significance_report(gold, strategies,
                                          n_boot=args.n_boot, seed=args.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
