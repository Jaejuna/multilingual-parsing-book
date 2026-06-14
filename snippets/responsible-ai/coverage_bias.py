#!/usr/bin/env python3
"""Surface per-language disparities in a multilingual dataset.

WHY THIS EXISTS  (responsible / equitable AI)
---------------------------------------------
A voice assistant that works great in English and limps in Thai is a
fairness problem, not just a backlog item. Aggregate quality numbers hide
this: "92% coverage" can mean every language is at 92%, or English at 100%
and five languages at 70%. Responsible-AI review asks the disaggregated
question -- *is any language group systematically underserved?* -- and
needs it as a number a product review can act on.

This report disaggregates a corpus by language and flags inequity, using
three proxies that need no human labels:

  coverage        fraction of rows with a non-empty translation
  untranslated    target equals the source verbatim (copy-through)
  too-short       target far shorter than the source median (likely stub)

It then reports the spread across languages (gap from the best language,
coefficient of variation) so "is this fair" has an answer, not a vibe.

WHAT IT IS NOT
--------------
Not a measure of *translation* quality -- a present, same-length, non-copy
cell can still be a bad translation. This is an equity screen over the
data, complementary to (not a replacement for) human/LLM quality review.

USAGE
-----
    python coverage_bias.py corpus.csv
    python coverage_bias.py corpus.csv --base ko --format json

Expects the wide corpus shape used across this book:
    key, ko, en-US, ja-JP, zh-CN, ...   (one column per language)

Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

_LANG_RE = re.compile(r"^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})*$")
_NON_LANG = {"key", "id", "term", "source", "slug", "context", "note"}


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


def is_lang(col: str) -> bool:
    return col.lower() not in _NON_LANG and bool(_LANG_RE.match(col.strip()))


@dataclass
class LangStat:
    lang: str
    rows: int = 0
    filled: int = 0
    untranslated: int = 0   # equals source verbatim
    too_short: int = 0      # << source length

    @property
    def coverage(self) -> float:
        return self.filled / self.rows if self.rows else 0.0

    @property
    def untranslated_rate(self) -> float:
        return self.untranslated / self.filled if self.filled else 0.0

    @property
    def too_short_rate(self) -> float:
        return self.too_short / self.filled if self.filled else 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d.update(
            coverage=round(self.coverage, 4),
            untranslated_rate=round(self.untranslated_rate, 4),
            too_short_rate=round(self.too_short_rate, 4),
        )
        return d


@dataclass
class Report:
    base: str
    languages: list[str] = field(default_factory=list)
    stats: dict[str, dict] = field(default_factory=dict)
    coverage_gap: float = 0.0           # best - worst (percentage points)
    coverage_cv: float = 0.0            # coefficient of variation
    underserved: list[str] = field(default_factory=list)


def analyze(rows: list[dict[str, str]], base: str | None, short_ratio: float) -> Report:
    if not rows:
        raise SystemExit("empty corpus")
    cols = list(rows[0].keys())
    langs = [c for c in cols if is_lang(c)]
    if not langs:
        raise SystemExit(f"no language columns in {cols}")

    if not base or base not in langs:
        fill = {l: sum(1 for r in rows if (r.get(l) or "").strip()) for l in langs}
        base = max(fill, key=fill.get)

    stats = {l: LangStat(lang=l, rows=len(rows)) for l in langs}
    for row in rows:
        src = (row.get(base) or "").strip()
        for l in langs:
            val = (row.get(l) or "").strip()
            st = stats[l]
            if not val:
                continue
            st.filled += 1
            if l != base and src:
                if val == src:
                    st.untranslated += 1
                elif len(val) < len(src) * short_ratio:
                    st.too_short += 1

    covs = [stats[l].coverage for l in langs]
    best, worst = max(covs), min(covs)
    mean = sum(covs) / len(covs)
    var = sum((c - mean) ** 2 for c in covs) / len(covs)
    cv = (var ** 0.5) / mean if mean else 0.0

    # underserved = coverage more than 10pp below the best language
    underserved = [l for l in langs if best - stats[l].coverage > 0.10]

    return Report(
        base=base,
        languages=langs,
        stats={l: stats[l].as_dict() for l in langs},
        coverage_gap=round(best - worst, 4),
        coverage_cv=round(cv, 4),
        underserved=sorted(underserved, key=lambda l: stats[l].coverage),
    )


def to_markdown(rep: Report) -> str:
    out: list[str] = []
    w = out.append
    w("# Language equity report\n")
    w(f"- base (reference) language: `{rep.base}`")
    w(f"- coverage gap (best − worst): **{rep.coverage_gap:.1%}**"
      f"{'  ⚠️' if rep.coverage_gap > 0.10 else ''}")
    w(f"- coverage coefficient of variation: **{rep.coverage_cv:.3f}** "
      f"(0 = perfectly equal)\n")

    w("## Per-language\n")
    w("| language | coverage | untranslated | too-short |")
    w("|----------|----------|--------------|-----------|")
    for l in sorted(rep.languages, key=lambda l: rep.stats[l]["coverage"]):
        s = rep.stats[l]
        flag = " ⚠️" if l in rep.underserved else ""
        w(f"| `{l}`{flag} | {s['coverage']:.1%} | "
          f"{s['untranslated_rate']:.1%} | {s['too_short_rate']:.1%} |")
    w("")

    if rep.underserved:
        w("## ⚠️ Underserved languages (>10pp below best)\n")
        for l in rep.underserved:
            w(f"- `{l}` — coverage {rep.stats[l]['coverage']:.1%}; "
              f"prioritize for data collection")
        w("")
    else:
        w("## ✅ No language is materially underserved\n")

    w("> Caveat: `too-short` compares raw character counts and will "
      "over-flag dense scripts (CJK packs more meaning per character than "
      "Latin). Read it as a screen, and compare within a script, not across.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Disaggregate corpus quality by language.")
    p.add_argument("csv", type=Path)
    p.add_argument("--base", help="reference language (auto = most-filled)")
    p.add_argument("--short-ratio", type=float, default=0.3,
                   help="flag target shorter than ratio×source length (default 0.3)")
    p.add_argument("--format", choices=["md", "json"], default="md")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if not args.csv.exists():
        print(f"file not found: {args.csv}", file=sys.stderr)
        return 2

    rep = analyze(read_csv_smart(args.csv), args.base, args.short_ratio)
    print(to_markdown(rep) if args.format == "md"
          else json.dumps(asdict(rep), ensure_ascii=False, indent=2))
    return 1 if rep.underserved else 0


if __name__ == "__main__":
    raise SystemExit(main())
