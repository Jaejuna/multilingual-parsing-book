#!/usr/bin/env python3
"""The corpus-quality metrics of ch.7, rewritten the data-analyst way.

WHY THIS EXISTS  (the third view)
---------------------------------
The book computes the same metrics three ways on purpose, because knowing
*which tool to reach for* is the actual skill:

    ch.7   stdlib  (csv + Counter)   -> zero-dependency, streamable, drop-in
    ch.13  SQL     (window funcs)    -> when the data already lives in Postgres
    HERE   pandas  (groupby/melt)    -> the lingua franca of data analysis

This file is the pandas view. It is intentionally NOT a rewrite of the
stdlib tool — it exists to show the idiomatic vectorized expression of the
same questions, and a test (test_pandas_parity) asserts it returns the same
numbers as audit_corpus.py on the same sample.

WHEN pandas is the right call
-----------------------------
- exploratory analysis, notebooks, ad-hoc slicing
- joins/pivots/group-bys that are painful in hand-written loops
WHEN it is NOT
- datasets that don't fit in RAM  -> ch.14 uses polars / duckdb (out-of-core)
- a zero-dependency drop-in        -> ch.7 stdlib
- the data already in a warehouse  -> ch.13 SQL

USAGE
-----
    python corpus_metrics_pandas.py sample_corpus.csv
    python corpus_metrics_pandas.py corpus.csv --key-col term

Requires: pandas. Python 3.10+.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

_LANG_RE = re.compile(r"^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})*$")
_NON_LANG = {"key", "id", "term", "source", "slug", "context", "note"}


def base_lang(code: str) -> str:
    """'ko-KR' / 'ko_KR' -> 'ko'  (same rule as ch.2 / ch.7)."""
    return code.replace("_", "-").split("-", 1)[0].lower()


def lang_columns(df: pd.DataFrame, key_col: str | None) -> list[str]:
    return [c for c in df.columns
            if c != key_col and c.lower() not in _NON_LANG and _LANG_RE.match(c)]


def analyze(df: pd.DataFrame, key_col: str) -> dict:
    langs = lang_columns(df, key_col)
    if not langs:
        raise SystemExit(f"no language columns in {list(df.columns)}")

    # Treat blank/whitespace-only cells as missing, uniformly.
    cells = df[langs].apply(lambda s: s.astype("string").str.strip())
    nonempty = cells.replace("", pd.NA).notna()

    # --- coverage: vectorized column mean of the non-empty mask -------------
    coverage = nonempty.mean().sort_values()       # Series: lang -> fill rate

    # --- lang-code conflicts: group columns by their base form -------------
    by_base: dict[str, list[str]] = {}
    for c in langs:
        by_base.setdefault(base_lang(c), []).append(c)
    conflicts = {b: cs for b, cs in by_base.items() if len(cs) > 1}

    # --- duplicate keys ----------------------------------------------------
    dup_keys = int(df[key_col].astype("string").str.strip().duplicated().sum())

    # --- length-ratio outliers vs the most-filled base column --------------
    # Only compare cells where BOTH base and target are present (same rule as
    # ch.7): an empty target is a coverage gap, not a length outlier. We map
    # empty -> NA so it drops out of the median and the outlier mask alike.
    base_col = coverage.idxmax()
    base_len = cells[base_col].str.len().replace(0, pd.NA)
    tgt_len = cells.drop(columns=[base_col]).apply(lambda s: s.str.len()).replace(0, pd.NA)
    long_df = tgt_len.div(base_len, axis=0)         # ratio per target/base, NA-safe
    # normalize each column by its own median, flag >4x or <0.25x
    medians = long_df.median()
    normed = long_df / medians
    outliers = int(((normed > 4) | (normed < 0.25)).sum().sum())

    return {
        "rows": len(df),
        "languages": langs,
        "base": base_col,
        "coverage": {k: round(v, 4) for k, v in coverage.items()},
        "lang_code_conflicts": conflicts,
        "duplicate_keys": dup_keys,
        "length_outliers": outliers,
    }


def to_markdown(rep: dict) -> str:
    out = ["# Corpus quality (pandas view)\n",
           f"- rows: **{rep['rows']}**  ·  base: `{rep['base']}`\n"]
    if rep["lang_code_conflicts"]:
        out.append("## Lang-code conflicts\n")
        for b, cs in rep["lang_code_conflicts"].items():
            out.append(f"- base `{b}`: {', '.join(f'`{c}`' for c in cs)}")
        out.append("")
    out.append("## Coverage\n| language | fill rate |\n|----------|-----------|")
    for lang, cov in rep["coverage"].items():
        flag = " ⚠️" if cov < 0.9 else ""
        out.append(f"| `{lang}` | {cov:.1%}{flag} |")
    out.append("")
    out.append("## Metrics\n| check | count |\n|-------|-------|")
    out.append(f"| duplicate keys | {rep['duplicate_keys']} |")
    out.append(f"| length-ratio outliers | {rep['length_outliers']} |")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Corpus quality metrics with pandas.")
    p.add_argument("csv", type=Path)
    p.add_argument("--key-col", default=None)
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    # utf-8-sig handles the BOM (ch.1.5); pandas reads CSV in one call.
    df = pd.read_csv(args.csv, dtype="string", keep_default_na=False, encoding="utf-8-sig")
    key_col = args.key_col or next((c for c in df.columns
                                    if c.lower() in _NON_LANG), df.columns[0])
    print(to_markdown(analyze(df, key_col)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
