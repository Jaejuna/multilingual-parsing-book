#!/usr/bin/env python3
"""Audit a wide-format multilingual corpus and emit a quality report.

WHY THIS EXISTS
---------------
The rest of this book documents i18n bugs one at a time: encoding
mojibake (#1), lang-code form mismatch (#2), glossary coverage (#4).
In production those never arrive one at a time -- a single vendor CSV
shows up with cp949 bytes, mixed ``ko``/``ko-KR`` headers, half the
Japanese column blank, and three rows where the ``{player_name}``
placeholder got "translated". You need *one pass* that surfaces all of
it with row-level pointers, so the data owner can fix the source
instead of you patching symptoms downstream.

This is the capstone of the book: it practices every lesson the other
chapters teach, on a real dataset, and turns them into metrics.

WHAT IT CHECKS  (wide format: one row per key, one column per language)
----------------------------------------------------------------------
  structure      row/column counts, which columns are language columns
  lang-codes     mixed spelling of the same language (ko vs ko-KR)   -> #2
  encoding       mojibake signatures + U+FFFD replacement chars      -> #1
  normalization  cells that are not NFC (jamo-split Hangul, etc.)    -> #1.4
  coverage       per-language fill rate; missing translations        -> #4
  duplicates     duplicate keys and duplicate base-language values
  whitespace     leading/trailing space, control chars, stray BOM
  placeholders   format tokens ({0}, %s, {name}, <br>) preserved
                 across languages -- the classic localization breaker
  length         per-language char-length ratio outliers vs the base

WHAT IT IS NOT
--------------
Not a *translation quality* judge -- it never asks "is this a good
translation". It only audits the dataset as data. Semantic/fluency
scoring is a different tool (LLM-as-judge), deliberately out of scope.

USAGE
-----
    python audit_corpus.py corpus.csv
    python audit_corpus.py corpus.csv --key-col term --base ko-KR
    python audit_corpus.py corpus.csv --format json --out report.json
    python audit_corpus.py corpus.csv --format both --out report

Stdlib only (csv, unicodedata, argparse, json, re). Python 3.10+.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path

# --------------------------------------------------------------------------
# 0. Reading bytes -> rows, practicing the book's own encoding lesson (#1)
# --------------------------------------------------------------------------


def read_csv_smart(path: Path) -> tuple[list[dict[str, str]], str]:
    """Read a CSV without trusting its encoding.

    utf-8-sig first (strips a BOM if present, see README #1.5), then fall
    back to cp949 -- the encoding Excel on Korean Windows still emits.
    Returns (rows, encoding_used) so the report can flag legacy files.
    """
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - both decoders failed
        raise SystemExit(f"could not decode {path} as utf-8 or cp949")

    reader = csv.DictReader(io.StringIO(text))
    rows = [dict(r) for r in reader]
    return rows, ("utf-8" if enc == "utf-8-sig" else enc)


# --------------------------------------------------------------------------
# 1. Lang-code handling, reusing the alias rule from README #2
# --------------------------------------------------------------------------

# A header is treated as a language column if it looks like a BCP-47 tag.
_LANG_HEADER_RE = re.compile(r"^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})*$")


def base_lang(code: str) -> str:
    """'ko-KR' / 'ko_KR' -> 'ko'  (the README #2 normalization)."""
    return code.replace("_", "-").split("-", 1)[0].lower()


def is_lang_header(name: str) -> bool:
    """Heuristic: looks like a language tag and isn't an obvious key column."""
    if name.lower() in {"key", "id", "term", "source", "slug", "context", "note"}:
        return False
    return bool(_LANG_HEADER_RE.match(name.strip()))


# --------------------------------------------------------------------------
# 2. Cell-level detectors
# --------------------------------------------------------------------------

# Mojibake signature: a UTF-8 multibyte sequence wrongly decoded as latin-1
# always yields a lead char in U+00C2..U+00F4 followed by one or more
# continuation chars in U+0080..U+00BF; we also flag U+FFFD. Heuristic.
_MOJIBAKE_RE = re.compile("[Â-ô][-¿]+|�")

# Format placeholders we expect to survive translation verbatim.
_PLACEHOLDER_RE = re.compile(
    r"""
      \{[^}]*\}            # {0}, {name}, {player_name}
    | %\d*\$?[sdfx]        # %s, %d, %1$s
    | <[^>]+>             # <br>, <b>, <link>
    | \[\[[^\]]+\]\]       # [[icon]]
    """,
    re.VERBOSE,
)


def has_mojibake(s: str) -> bool:
    return bool(_MOJIBAKE_RE.search(s))


def is_not_nfc(s: str) -> bool:
    return unicodedata.normalize("NFC", s) != s


def placeholders(s: str) -> Counter:
    return Counter(m.group(0) for m in _PLACEHOLDER_RE.finditer(s))


def whitespace_issue(s: str) -> str | None:
    if s != s.strip():
        return "leading/trailing whitespace"
    if any(unicodedata.category(c) == "Cc" for c in s):
        return "control character"
    if "﻿" in s:
        return "embedded BOM"
    return None


# --------------------------------------------------------------------------
# 3. Findings model
# --------------------------------------------------------------------------


@dataclass
class Finding:
    """One row-level problem, so the data owner can jump straight to it."""

    row: int  # 1-based data row (header is row 0)
    column: str
    issue: str
    detail: str = ""

    def __str__(self) -> str:
        loc = f"row {self.row} · {self.column}"
        return f"{loc}: {self.issue}" + (f" — {self.detail}" if self.detail else "")


@dataclass
class Report:
    path: str
    encoding_used: str
    n_rows: int = 0
    key_column: str | None = None
    lang_columns: list[str] = field(default_factory=list)
    base_lang: str | None = None
    lang_code_conflicts: dict[str, list[str]] = field(default_factory=dict)
    coverage: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def add(self, **kw) -> None:
        self.findings.append(Finding(**kw))


# --------------------------------------------------------------------------
# 4. The audit
# --------------------------------------------------------------------------


def audit(
    rows: list[dict[str, str]],
    headers: list[str],
    encoding_used: str,
    path: str,
    key_col: str | None,
    base: str | None,
) -> Report:
    rep = Report(path=path, encoding_used=encoding_used, n_rows=len(rows))

    # --- identify columns ------------------------------------------------
    lang_cols = [h for h in headers if is_lang_header(h)]
    rep.lang_columns = lang_cols
    if key_col and key_col in headers:
        rep.key_column = key_col
    else:
        non_lang = [h for h in headers if h not in lang_cols]
        rep.key_column = non_lang[0] if non_lang else None

    if not lang_cols:
        rep.metrics["error"] = 1
        rep.add(row=0, column="(header)", issue="no language columns detected",
                detail=f"headers were: {headers}")
        return rep

    # --- lang-code conflicts (#2): same base, different spellings --------
    by_base: dict[str, list[str]] = defaultdict(list)
    for col in lang_cols:
        by_base[base_lang(col)].append(col)
    rep.lang_code_conflicts = {b: cs for b, cs in by_base.items() if len(cs) > 1}
    for b, cs in rep.lang_code_conflicts.items():
        rep.add(row=0, column=", ".join(cs), issue="mixed lang-code forms",
                detail=f"all map to base '{b}' — downstream lookups may miss")

    # --- pick the base language column -----------------------------------
    if base and base in lang_cols:
        base_col = base
    else:
        # most-filled column makes the most reliable length/placeholder anchor
        fill = {c: sum(1 for r in rows if (r.get(c) or "").strip()) for c in lang_cols}
        base_col = max(fill, key=fill.get)
    rep.base_lang = base_col

    # --- per-cell scan ---------------------------------------------------
    counts = Counter()
    coverage_hits = Counter()
    length_samples: dict[str, list[float]] = defaultdict(list)
    seen_keys: dict[str, int] = {}
    seen_base_vals: dict[str, int] = {}

    for i, row in enumerate(rows, start=1):
        # duplicate key / base value detection
        if rep.key_column:
            k = (row.get(rep.key_column) or "").strip()
            if k and k in seen_keys:
                counts["duplicate_key"] += 1
                rep.add(row=i, column=rep.key_column, issue="duplicate key",
                        detail=f"first seen at row {seen_keys[k]}")
            elif k:
                seen_keys[k] = i

        base_val = (row.get(base_col) or "").strip()
        if base_val:
            if base_val in seen_base_vals:
                counts["duplicate_source"] += 1
            else:
                seen_base_vals[base_val] = i
        base_ph = placeholders(base_val)

        for col in lang_cols:
            val = row.get(col) or ""
            stripped = val.strip()
            if stripped:
                coverage_hits[col] += 1

            if stripped and has_mojibake(val):
                counts["mojibake"] += 1
                rep.add(row=i, column=col, issue="possible mojibake",
                        detail=repr(val[:40]))
            if stripped and is_not_nfc(val):
                counts["not_nfc"] += 1
                rep.add(row=i, column=col, issue="not NFC-normalized",
                        detail="jamo-split or combining sequence")
            ws = whitespace_issue(val)
            if stripped and ws:
                counts["whitespace"] += 1
                rep.add(row=i, column=col, issue=ws)

            # placeholder parity vs base (skip the base column itself)
            if col != base_col and base_val and stripped:
                tgt_ph = placeholders(val)
                if tgt_ph != base_ph:
                    missing = base_ph - tgt_ph
                    extra = tgt_ph - base_ph
                    counts["placeholder_mismatch"] += 1
                    bits = []
                    if missing:
                        bits.append(f"missing {list(missing.elements())}")
                    if extra:
                        bits.append(f"extra {list(extra.elements())}")
                    rep.add(row=i, column=col, issue="placeholder mismatch",
                            detail="; ".join(bits))

            # length ratio sample (need both sides present)
            if col != base_col and base_val and stripped:
                length_samples[col].append(len(stripped) / max(len(base_val), 1))

    # --- coverage --------------------------------------------------------
    for col in lang_cols:
        rep.coverage[col] = round(coverage_hits[col] / rep.n_rows, 4) if rep.n_rows else 0.0

    # --- length outliers: flag rows far from each column's median ratio ---
    def median(xs: list[float]) -> float:
        s = sorted(xs)
        n = len(s)
        if n == 0:
            return 0.0
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    medians = {c: median(v) for c, v in length_samples.items()}
    for i, row in enumerate(rows, start=1):
        base_val = (row.get(base_col) or "").strip()
        if not base_val:
            continue
        for col in lang_cols:
            if col == base_col:
                continue
            val = (row.get(col) or "").strip()
            if not val:
                continue
            med = medians.get(col, 0.0)
            if med <= 0:
                continue
            ratio = (len(val) / max(len(base_val), 1)) / med
            if ratio > 4 or ratio < 0.25:
                counts["length_outlier"] += 1
                rep.add(row=i, column=col, issue="length-ratio outlier",
                        detail=f"{ratio:.1f}× the column median")

    rep.metrics = dict(counts)
    rep.metrics["total_findings"] = len(rep.findings)
    return rep


# --------------------------------------------------------------------------
# 5. Rendering
# --------------------------------------------------------------------------


def to_markdown(rep: Report, max_findings: int = 40) -> str:
    out: list[str] = []
    w = out.append
    w(f"# Corpus quality report — `{rep.path}`\n")
    w(f"- decoded as: **{rep.encoding_used}**"
      f"{'  ⚠️ legacy encoding' if rep.encoding_used == 'cp949' else ''}")
    w(f"- rows: **{rep.n_rows}**")
    w(f"- key column: `{rep.key_column}`")
    w(f"- language columns: {', '.join(f'`{c}`' for c in rep.lang_columns) or '(none)'}")
    w(f"- base (anchor) language: `{rep.base_lang}`\n")

    if rep.lang_code_conflicts:
        w("## ⚠️ Lang-code form conflicts (README #2)\n")
        for b, cs in rep.lang_code_conflicts.items():
            w(f"- base `{b}`: {', '.join(f'`{c}`' for c in cs)} — pick one form")
        w("")

    w("## Coverage\n")
    w("| language | fill rate | missing |")
    w("|----------|-----------|---------|")
    for c, cov in sorted(rep.coverage.items(), key=lambda kv: kv[1]):
        missing = rep.n_rows - round(cov * rep.n_rows)
        flag = " ⚠️" if cov < 0.9 else ""
        w(f"| `{c}` | {cov:.1%}{flag} | {missing} |")
    w("")

    w("## Metrics\n")
    w("| check | count |")
    w("|-------|-------|")
    label = {
        "mojibake": "possible mojibake cells (#1)",
        "not_nfc": "not-NFC cells (#1.4)",
        "whitespace": "whitespace/control issues",
        "placeholder_mismatch": "placeholder mismatches",
        "length_outlier": "length-ratio outliers",
        "duplicate_key": "duplicate keys",
        "duplicate_source": "duplicate source values",
        "total_findings": "**total findings**",
    }
    for key, lab in label.items():
        if key in rep.metrics:
            w(f"| {lab} | {rep.metrics[key]} |")
    w("")

    if rep.findings:
        shown = [f for f in rep.findings if f.row > 0][:max_findings]
        w(f"## Row-level findings (first {len(shown)})\n")
        for f in shown:
            w(f"- {f}")
        remaining = len([f for f in rep.findings if f.row > 0]) - len(shown)
        if remaining > 0:
            w(f"- … and {remaining} more")
        w("")
    else:
        w("## ✅ No row-level issues found\n")

    return "\n".join(out)


def to_json(rep: Report) -> str:
    d = asdict(rep)
    d["findings"] = [asdict(f) for f in rep.findings]
    return json.dumps(d, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# 6. CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Audit a multilingual corpus CSV.")
    p.add_argument("csv", type=Path, help="wide-format CSV (one column per language)")
    p.add_argument("--key-col", help="name of the key/id column (auto-detected otherwise)")
    p.add_argument("--base", help="base language column to anchor length/placeholder checks")
    p.add_argument("--format", choices=["md", "json", "both"], default="md")
    p.add_argument("--out", help="output path (stem; extension added per format)")
    p.add_argument("--max-findings", type=int, default=40,
                   help="row-level findings to print in markdown")
    args = p.parse_args(argv)

    # The report uses em-dashes and emoji; a Windows console defaults to cp949
    # and would crash on them. Force UTF-8 out, practicing README #1 ourselves.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if not args.csv.exists():
        print(f"file not found: {args.csv}", file=sys.stderr)
        return 2

    rows, enc = read_csv_smart(args.csv)
    headers = list(rows[0].keys()) if rows else []
    rep = audit(rows, headers, enc, str(args.csv), args.key_col, args.base)

    md = to_markdown(rep, args.max_findings)
    js = to_json(rep)

    if args.out:
        stem = Path(args.out)
        if args.format in ("md", "both"):
            stem.with_suffix(".md").write_text(md, encoding="utf-8")
        if args.format in ("json", "both"):
            stem.with_suffix(".json").write_text(js, encoding="utf-8")
        print(f"wrote report to {stem} ({args.format})")
    else:
        print(md if args.format == "md" else js if args.format == "json" else md + "\n\n" + js)

    # non-zero exit if anything was flagged -> usable as a CI gate
    return 1 if rep.metrics.get("total_findings", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
