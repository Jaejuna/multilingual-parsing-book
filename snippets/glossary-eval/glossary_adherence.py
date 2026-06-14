#!/usr/bin/env python3
"""Measure whether MT output actually honored the glossary, per language.

WHY THIS EXISTS  (the feedback loop)
------------------------------------
Shipping a glossary into a translation pipeline is only half the work. The
question to answer next is: *did the model actually use it?* "We added 420
terms" is an input metric; "the Japanese output applied 64% of the applicable
terms" is the outcome metric that tells product whether the augmenter is
working. This script computes that outcome metric and hands back the specific
misses so the loop can close.

It is the evaluation twin of README #4 (glossary matching): #4 finds terms
to *inject*; this finds whether the injected term *survived* into the
translation.

WHAT IT DOES
------------
Given
  - a glossary CSV   : source term + the required translation per language
  - a segments CSV   : id + source text + the MT output per language
for every segment and every target language it:
  1. detects which glossary source terms occur in the source text
     (word-boundary for Latin scripts, substring for CJK -- README #4.2)
  2. checks whether the required target term appears in the MT output
  3. tallies adherence = applied / applicable, and records each miss

WHAT IT IS NOT
--------------
Not a fluency/adequacy judge -- it does not say whether the translation is
*good*, only whether the mandated terminology is present. Semantic quality
is a separate (LLM-as-judge) tool, deliberately out of scope.

USAGE
-----
    python glossary_adherence.py --glossary g.csv --segments s.csv
    python glossary_adherence.py --glossary g.csv --segments s.csv \
        --case-sensitive --format json --out report.json

CSV shapes (first column is special, the rest are language columns):
    glossary.csv : term,   en, ja, zh-CN
    segments.csv : id, source, en, ja, zh-CN     # 'source' is the 2nd col

Stdlib only. Python 3.10+.
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
# Shared helpers (same lessons as the rest of the book)
# --------------------------------------------------------------------------


def read_csv_smart(path: Path) -> list[dict[str, str]]:
    """utf-8-sig first (strip BOM, README #1.5), cp949 fallback (#1)."""
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
    """True if the string contains Han/Hiragana/Katakana/Hangul.

    CJK has no whitespace word boundaries, so \\b is meaningless there and we
    fall back to substring matching -- exactly the split README #4.2 calls for.
    """
    for ch in text:
        name = unicodedata.name(ch, "")
        if any(tag in name for tag in ("CJK", "HIRAGANA", "KATAKANA", "HANGUL")):
            return True
    return False


def fold(s: str) -> str:
    """Normalize a string for caseless, width-insensitive matching.

    NFKC folds compatibility variants (full-width 'ＡＩ' -> 'AI', half-width
    katakana, '①' -> '1'); casefold is the Unicode-correct caseless fold
    (ß -> ss, Turkish İ/ı) that lower() gets wrong. Both belong on MATCH KEYS,
    not on stored/displayed text -- they are lossy. See Appendix B field notes.
    """
    return unicodedata.normalize("NFKC", s).casefold()


def contains_term(haystack: str, needle: str, case_sensitive: bool) -> bool:
    """Script-aware presence test: \\b for Latin, plain substring for CJK."""
    if not needle:
        return False
    h, n = (haystack, needle) if case_sensitive else (fold(haystack), fold(needle))
    if is_cjk(needle):
        return n in h
    return re.search(rf"\b{re.escape(n)}\b", h) is not None


# --------------------------------------------------------------------------
# Findings model
# --------------------------------------------------------------------------


@dataclass
class Miss:
    segment_id: str
    lang: str
    term: str
    expected: str

    def __str__(self) -> str:
        return f"[{self.lang}] seg {self.segment_id}: '{self.term}' -> expected '{self.expected}', absent"


@dataclass
class LangScore:
    lang: str
    applicable: int = 0   # glossary terms that appeared in the source
    applied: int = 0      # ...whose target term also appeared in the MT output

    @property
    def adherence(self) -> float:
        return self.applied / self.applicable if self.applicable else 0.0


@dataclass
class Report:
    glossary_terms: int
    segments: int
    scores: dict[str, dict] = field(default_factory=dict)
    top_misses: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    misses: list[Miss] = field(default_factory=list)


# --------------------------------------------------------------------------
# Core evaluation
# --------------------------------------------------------------------------


def evaluate(
    glossary: list[dict[str, str]],
    segments: list[dict[str, str]],
    case_sensitive: bool,
) -> Report:
    if not glossary or not segments:
        raise SystemExit("glossary and segments must both be non-empty")

    g_cols = list(glossary[0].keys())
    term_col = g_cols[0]
    g_langs = g_cols[1:]

    s_cols = list(segments[0].keys())
    source_col = s_cols[1]            # id, source, <langs...>
    s_langs = s_cols[2:]

    langs = [l for l in g_langs if l in s_langs]
    if not langs:
        raise SystemExit(
            f"no shared language columns. glossary={g_langs} segments={s_langs}"
        )

    # index glossary: term -> {lang: required target}
    terms: dict[str, dict[str, str]] = {}
    for row in glossary:
        src = (row.get(term_col) or "").strip()
        if src:
            terms[src] = {l: (row.get(l) or "").strip() for l in langs}

    scores = {l: LangScore(lang=l) for l in langs}
    miss_counter: dict[str, Counter] = {l: Counter() for l in langs}
    misses: list[Miss] = []

    for seg in segments:
        seg_id = (seg.get(s_cols[0]) or "").strip()
        source_text = seg.get(source_col) or ""
        # which glossary terms are present in this source segment?
        present = [t for t in terms if contains_term(source_text, t, case_sensitive)]
        if not present:
            continue
        for lang in langs:
            mt = seg.get(lang) or ""
            for t in present:
                expected = terms[t][lang]
                if not expected:
                    continue  # glossary has no target for this lang -> not applicable
                scores[lang].applicable += 1
                if contains_term(mt, expected, case_sensitive):
                    scores[lang].applied += 1
                else:
                    miss_counter[lang][t] += 1
                    misses.append(Miss(seg_id, lang, t, expected))

    return Report(
        glossary_terms=len(terms),
        segments=len(segments),
        scores={l: {**asdict(s), "adherence": round(s.adherence, 4)}
                for l, s in scores.items()},
        top_misses={l: miss_counter[l].most_common(5) for l in langs},
        misses=misses,
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def to_markdown(rep: Report, max_misses: int = 30) -> str:
    out: list[str] = []
    w = out.append
    w("# Glossary adherence report\n")
    w(f"- glossary terms: **{rep.glossary_terms}**")
    w(f"- segments evaluated: **{rep.segments}**\n")

    w("## Adherence by language\n")
    w("| language | applicable | applied | adherence |")
    w("|----------|-----------|---------|-----------|")
    for lang, s in sorted(rep.scores.items(), key=lambda kv: kv[1]["adherence"]):
        flag = " ⚠️" if s["adherence"] < 0.8 else ""
        w(f"| `{lang}` | {s['applicable']} | {s['applied']} | {s['adherence']:.1%}{flag} |")
    w("")

    any_misses = any(m for m in rep.top_misses.values())
    if any_misses:
        w("## Top missed terms (by language)\n")
        for lang, items in rep.top_misses.items():
            if items:
                pretty = ", ".join(f"`{t}`×{n}" for t, n in items)
                w(f"- **{lang}**: {pretty}")
        w("")

    if rep.misses:
        shown = rep.misses[:max_misses]
        w(f"## Misses (first {len(shown)})\n")
        for m in shown:
            w(f"- {m}")
        if len(rep.misses) > len(shown):
            w(f"- … and {len(rep.misses) - len(shown)} more")
        w("")
    else:
        w("## ✅ Every applicable term was applied\n")
    return "\n".join(out)


def to_json(rep: Report) -> str:
    d = asdict(rep)
    d["misses"] = [asdict(m) for m in rep.misses]
    return json.dumps(d, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Measure glossary adherence in MT output.")
    p.add_argument("--glossary", required=True, type=Path)
    p.add_argument("--segments", required=True, type=Path)
    p.add_argument("--case-sensitive", action="store_true")
    p.add_argument("--format", choices=["md", "json", "both"], default="md")
    p.add_argument("--out", help="output path stem (extension added per format)")
    p.add_argument("--max-misses", type=int, default=30)
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    for f in (args.glossary, args.segments):
        if not f.exists():
            print(f"file not found: {f}", file=sys.stderr)
            return 2

    rep = evaluate(
        read_csv_smart(args.glossary),
        read_csv_smart(args.segments),
        args.case_sensitive,
    )
    md, js = to_markdown(rep, args.max_misses), to_json(rep)

    if args.out:
        stem = Path(args.out)
        if args.format in ("md", "both"):
            stem.with_suffix(".md").write_text(md, encoding="utf-8")
        if args.format in ("json", "both"):
            stem.with_suffix(".json").write_text(js, encoding="utf-8")
        print(f"wrote report to {stem} ({args.format})")
    else:
        print(md if args.format == "md" else js if args.format == "json" else md + "\n\n" + js)

    # CI gate: fail if any language fell below 80% adherence
    worst = min((s["adherence"] for s in rep.scores.values()), default=1.0)
    return 1 if worst < 0.8 else 0


if __name__ == "__main__":
    raise SystemExit(main())
