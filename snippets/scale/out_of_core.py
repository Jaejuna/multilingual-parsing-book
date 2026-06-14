#!/usr/bin/env python3
"""Out-of-core corpus metrics: query a CSV bigger than RAM without loading it.

WHY THIS EXISTS  (scale, part 3: beyond one machine's RAM)
----------------------------------------------------------
Parts 1-2 of the scaling story stay in pure Python: Aho-Corasick for time
(bench_matching.py), Welford for memory (stream_vs_load.py). But when the
corpus genuinely will not fit in RAM and you want real analytic queries —
group-bys, joins, percentiles — hand-rolling a streaming aggregator stops
being the right call. The right call is a columnar engine that streams from
disk: DuckDB (SQL on files) or polars (lazy DataFrame). This is the
production escalation of ch.14 (the same metrics in SQL) and ch.8 (pandas).

This file computes per-language coverage straight off a CSV on disk, three
ways, and shows they agree:

    stdlib streaming  -> O(1) memory, zero deps, our baseline
    DuckDB            -> SQL over read_csv(), engine streams from disk
    polars (lazy)     -> scan_csv + group_by, collected in streaming mode

DuckDB and polars are OPTIONAL heavy dependencies (like pyahocorasick in #4).
If absent, that backend is skipped with a note — the stdlib path always runs.

    pip install duckdb polars     # to enable the columnar backends

WHEN to reach for this
----------------------
- the file does not fit in RAM (pandas/ch.8 would OOM)
- you want SQL-grade analytics (percentiles, joins) without a database server
WHEN not to
- small data -> ch.7 stdlib / ch.8 pandas are simpler
- the data already lives in Postgres -> ch.14, query it in place

USAGE
-----
    python out_of_core.py                 # generates a temp CSV and compares
    python out_of_core.py --rows 2000000

Python 3.10+. stdlib path needs nothing; columnar paths need duckdb/polars.
"""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
from pathlib import Path


# --------------------------------------------------------------------------
# Make a long-format corpus on disk: (id, lang, text). Large enough that
# "load it all" is the wrong instinct.
# --------------------------------------------------------------------------


def write_corpus(path: Path, rows: int) -> None:
    langs = ["en", "ko", "ja", "zh-CN", "ar", "th"]
    state = 999
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "lang", "text"])
        for i in range(rows):
            lang = langs[i % len(langs)]
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            # th and ar are deliberately sparse (missing ~half) -> low coverage.
            # Use a HIGH bit: an LCG's low bits are barely random and here would
            # correlate with the 6-language cycle, making coverage 0 or 1.
            empty = (lang in ("th", "ar")) and ((state >> 20) & 1)
            w.writerow([f"s{i}", lang, "" if empty else "word"])


# --------------------------------------------------------------------------
# Three backends, same question: fraction of non-empty text per language
# --------------------------------------------------------------------------


def coverage_stdlib(path: Path) -> dict[str, float]:
    """Single streaming pass, O(languages) memory — never holds all rows."""
    total: dict[str, int] = {}
    filled: dict[str, int] = {}
    with path.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            lang = row["lang"]
            total[lang] = total.get(lang, 0) + 1
            if row["text"].strip():
                filled[lang] = filled.get(lang, 0) + 1
    return {l: round(filled.get(l, 0) / total[l], 4) for l in total}


def coverage_duckdb(path: Path) -> dict[str, float] | None:
    try:
        import duckdb
    except ImportError:
        return None
    # read_csv streams from disk; the engine never materializes the whole file.
    q = """
        SELECT lang, ROUND(AVG(CASE WHEN trim(text) <> '' THEN 1.0 ELSE 0 END), 4) AS cov
        FROM read_csv(?, header=true)
        GROUP BY lang
    """
    rows = duckdb.execute(q, [str(path)]).fetchall()
    return {lang: float(cov) for lang, cov in rows}


def coverage_polars(path: Path) -> dict[str, float] | None:
    try:
        import polars as pl
    except ImportError:
        return None
    # scan_csv is lazy: the plan executes streaming when collected. polars
    # reads an empty CSV cell as null, so fill_null("") first to match the
    # stdlib rule (blank == not filled), else nulls would skew the mean.
    lf = (
        pl.scan_csv(path)
        .with_columns(
            (pl.col("text").fill_null("").str.strip_chars().str.len_chars() > 0).alias("filled")
        )
        .group_by("lang")
        .agg(pl.col("filled").mean().round(4))
    )
    df = lf.collect()
    return {row["lang"]: float(row["filled"]) for row in df.to_dicts()}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Out-of-core corpus coverage.")
    p.add_argument("--rows", type=int, default=600_000)
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "corpus.csv"
        write_corpus(path, args.rows)
        size_mb = path.stat().st_size / 1e6
        print(f"# Out-of-core coverage over {args.rows:,} rows ({size_mb:.1f} MB on disk)\n")

        base = coverage_stdlib(path)
        backends = {"stdlib (stream)": base,
                    "duckdb": coverage_duckdb(path),
                    "polars (lazy)": coverage_polars(path)}

        langs = sorted(base)
        header = "| backend | " + " | ".join(langs) + " |"
        print(header)
        print("|" + "---|" * (len(langs) + 1))
        for name, res in backends.items():
            if res is None:
                print(f"| {name} | " + " | ".join(["(not installed)"] * len(langs)) + " |")
                continue
            cells = " | ".join(f"{res.get(l, 0):.2f}" for l in langs)
            print(f"| {name} | {cells} |")

        # parity: every available backend must match the stdlib baseline
        ok = True
        for name, res in backends.items():
            if res is None:
                continue
            if any(abs(res.get(l, 0) - base[l]) > 1e-3 for l in langs):
                ok = False
                print(f"\nMISMATCH in {name}")
        print("\nAll available backends agree." if ok else "\nPARITY FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
