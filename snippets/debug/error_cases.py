#!/usr/bin/env python3
"""Runnable catalog of real bugs hit while building this book.

WHY THIS EXISTS
---------------
The book's premise is that every gotcha was shipped, hit, and fixed. Building
Part II was no exception, and several bugs we hit were the book's own lessons
biting back. This script is the runnable companion to Appendix E: each case
reproduces the BROKEN behaviour (its symptom) next to the FIX, so the lesson
is demonstrable, not just narrated. Run it and watch each trap fire and clear.

    python error_cases.py            # run every case
    python error_cases.py --case 4   # run one

Most cases are stdlib-only. Case 5 (polars) needs an optional dep and self-skips
if absent; the rest are stdlib-only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import io
import sys
import unicodedata


def case_1_console_cp949() -> tuple[str, str, str]:
    """Printing an em-dash to a cp949 console raises UnicodeEncodeError (#1)."""
    text = "report — done"
    try:
        text.encode("cp949")               # what a cp949 console attempts
        broke = "no error on this console"
    except UnicodeEncodeError as e:
        broke = f"UnicodeEncodeError: {e}"
    # fix: encode/emit as UTF-8 (what sys.stdout.reconfigure does for us)
    fixed = text.encode("utf-8").decode("utf-8")
    return ("console cp949 can't encode em-dash (#1)", broke, f"utf-8 OK: {fixed!r}")


def case_2_mojibake_regex() -> tuple[str, str, str]:
    """A too-narrow regex misses Korean-UTF-8-as-latin1 mojibake (#1)."""
    import re
    moji = "보통".encode("utf-8").decode("latin-1")   # -> 'ë³´í\x86µ'
    narrow = re.compile(r"[ÃÂ][\x80-\xbf]")            # original, too narrow
    correct = re.compile("[Â-ô][-¿]+")
    broke = f"narrow regex match on {moji!r}: {bool(narrow.search(moji))}"
    fixed = f"correct regex match: {bool(correct.search(moji))}"
    return ("mojibake regex that missed mojibake (#1)", broke, fixed)


def case_3_dataclass_importlib() -> tuple[str, str, str]:
    """Loading a module with a @dataclass by path fails if not in sys.modules."""
    import importlib.util
    src = "from dataclasses import dataclass\n@dataclass\nclass C:\n    x: int = 0\n"
    def load(register: bool):
        spec = importlib.util.spec_from_loader("ec_demo", loader=None)
        mod = importlib.util.module_from_spec(spec)
        if register:
            sys.modules["ec_demo"] = mod
        exec(compile(src, "ec_demo", "exec"), mod.__dict__)
        return mod
    try:
        load(register=False)
        broke = "no error (python version tolerant?)"
    except Exception as e:
        broke = f"{type(e).__name__}: {e}"
    finally:
        sys.modules.pop("ec_demo", None)
    load(register=True)
    sys.modules.pop("ec_demo", None)
    return ("dataclass + importlib needs sys.modules registration",
            broke, "registering in sys.modules before exec: OK")


def case_4_pandas_blank_outlier() -> tuple[str, str, str]:
    """A blank cell of length 0 becomes a false 'too-short' length outlier (#8)."""
    base_len = 10
    target_lengths = [9, 11, 0, 10]          # the 0 is an empty cell
    # broken: 0/base = ratio 0 -> flagged as < 0.25x median
    ratios = [t / base_len for t in target_lengths]
    med = sorted(ratios)[len(ratios) // 2] or 1
    broke_outliers = sum(1 for r in ratios if r / med < 0.25 or r / med > 4)
    # fixed: treat empty (0) as missing, exclude from the comparison
    present = [t for t in target_lengths if t > 0]
    ratios2 = [t / base_len for t in present]
    med2 = sorted(ratios2)[len(ratios2) // 2] or 1
    fixed_outliers = sum(1 for r in ratios2 if r / med2 < 0.25 or r / med2 > 4)
    return ("pandas counted blank cells as length outliers (#8)",
            f"with blanks counted: {broke_outliers} outlier(s)",
            f"blanks excluded (-> NA): {fixed_outliers} outlier(s)")


def case_5_polars_empty_null() -> tuple[str, str, str]:
    """polars reads an empty CSV cell as null, skewing a boolean mean (#8)."""
    try:
        import polars as pl
    except ImportError:
        return ("polars reads '' as null (#8)", "(polars not installed)", "skipped")
    csv = "lang,text\nen,word\nen,\nen,word\n"
    df = pl.read_csv(io.StringIO(csv))
    broke = df.select((pl.col("text") != "").mean()).item()        # null-skewed
    fixed = df.select((pl.col("text").fill_null("") != "").mean()).item()
    return ("polars reads empty cell as null (#8)",
            f"without fill_null, filled-rate = {broke}",
            f"with fill_null('') = {fixed:.3f} (2/3 as expected)")


def case_6_lcg_low_bit() -> tuple[str, str, str]:
    """An LCG's low bit correlates with a fixed-period cycle; use a high bit."""
    def lcg_stream(n, bit_shift):
        state = 999
        out = []
        for i in range(n):
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            if i % 6 == 0:                       # one "language" slot, every 6th
                out.append((state >> bit_shift) & 1)
        return out
    low = lcg_stream(600, 0)                      # low bit
    high = lcg_stream(600, 20)                    # high bit
    return ("LCG low bit correlates with a cycle",
            f"low-bit mean over a 6-cycle slot: {sum(low)/len(low):.2f} (degenerate)",
            f"high-bit mean: {sum(high)/len(high):.2f} (~0.5, usable)")


def case_7_cross_process_pipe() -> tuple[str, str, str]:
    """Surrogate-escaped bytes leak across a pipe between two processes (#1)."""
    raw = "보통".encode("utf-8")
    # a downstream process decoding with errors='surrogateescape' then re-encoding
    smuggled = raw.decode("ascii", errors="surrogateescape")
    try:
        smuggled.encode("utf-8")                  # strict utf-8 rejects surrogates
        broke = "no error"
    except UnicodeEncodeError as e:
        broke = f"UnicodeEncodeError: {e}"
    fixed = smuggled.encode("utf-8", errors="surrogateescape").decode("utf-8")
    return ("surrogate bytes leak across a process pipe (#1)",
            broke, f"surrogateescape round-trip: {fixed!r}")


def case_8_casefold_not_lower() -> tuple[str, str, str]:
    """Caseless matching with .lower() mishandles non-Latin case. Found in
    review: every matcher used .lower(); switched to .casefold()."""
    a, b = "STRASSE", "straße"          # German: should match caselessly
    lo = (a.lower() == b.lower())
    cf = (a.casefold() == b.casefold())
    return ("caseless matching: .lower() vs .casefold() (ß, Turkish İ)",
            f".lower(): {a.lower()!r} == {b.lower()!r} -> {lo}",
            f".casefold(): {a.casefold()!r} == {b.casefold()!r} -> {cf}")


def case_9_nfkc_width() -> tuple[str, str, str]:
    """Full-width/half-width variants won't match until NFKC-normalized. IME and
    legacy systems emit 'ＡＩ' (full-width); a glossary 'AI' misses it (#7)."""
    a, b = "ＡＩ", "AI"                  # full-width vs ASCII
    raw = (a == b)
    nfkc = (unicodedata.normalize("NFKC", a) == unicodedata.normalize("NFKC", b))
    return ("full-width vs ASCII needs NFKC before matching",
            f"raw: {a!r} == {b!r} -> {raw}",
            f"NFKC: normalize({a!r})={unicodedata.normalize('NFKC', a)!r} -> {nfkc}")


CASES = [case_1_console_cp949, case_2_mojibake_regex, case_3_dataclass_importlib,
         case_4_pandas_blank_outlier, case_5_polars_empty_null,
         case_6_lcg_low_bit, case_7_cross_process_pipe, case_8_casefold_not_lower,
         case_9_nfkc_width]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Reproduce the book's field-notes bugs.")
    p.add_argument("--case", type=int, help="run only case N (1-based)")
    args = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    cases = [CASES[args.case - 1]] if args.case else CASES
    for i, fn in enumerate(cases, start=args.case or 1):
        title, broke, fixed = fn()
        print(f"\n[{i}] {title}")
        print(f"    broken: {broke}")
        print(f"    fixed : {fixed}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
