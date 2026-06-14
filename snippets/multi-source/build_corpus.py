#!/usr/bin/env python3
"""Merge heterogeneous multilingual CSVs into one clean corpus, with provenance.

WHY THIS EXISTS
---------------
A real dataset is rarely one tidy file. It is a folder of exports from different
tools: one saved cp949 by Excel (#1), one with `ko_KR` headers and another with
`ko-KR` (#2), overlapping keys, and the same key translated two different ways in
two files. Concatenating them blindly yields mojibake, split columns, duplicates,
and silent conflicts. This tool does the merge properly — normalize, align,
dedupe, resolve, and *report* — so the combined corpus is trustworthy and you can
trace every value back to its source.

It is the capstone of Part I: it uses the encoding lesson (#1, utf-8-sig then
cp949), the lang-code lesson (#2, canonicalize spelling), and adds the two things
a merge needs: conflict detection and provenance.

WHAT IT DOES
------------
1. read each CSV with encoding fallback (#1); report which encoding was used
2. canonicalize language-column spelling (`ko_KR`/`KO-KR` -> `ko-KR`) (#2)
3. align rows across files by the key column
4. for each (key, language) take the first non-empty value in source order;
   record every value's source (provenance)
5. flag CONFLICTS: same (key, language), different non-empty values across files
6. write the unified wide CSV + print a merge report

CONFLICT POLICY: first source in the argument list wins; the losing value is
reported, never silently dropped. (Order your sources by trust.)

NOTE on `ko` vs `ko-KR`: spelling is canonicalized, but base (`ko`) and locale
(`ko-KR`) are kept as DISTINCT columns — collapsing them loses region (#2's
zh-CN vs zh-TW trade-off). Pass --merge-base to fold base into the locale.

USAGE
-----
    python build_corpus.py raw/*.csv -o clean.csv
    python build_corpus.py a.csv b.csv c.csv --key key --merge-base

Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import defaultdict
from pathlib import Path


def read_csv_smart(path: Path) -> tuple[list[dict[str, str]], str]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        raise SystemExit(f"could not decode {path}")
    return [dict(r) for r in csv.DictReader(io.StringIO(text))], (
        "utf-8" if enc == "utf-8-sig" else enc)


def canon_lang(col: str, merge_base: bool) -> str:
    """Canonicalize spelling: 'ko_KR'/'KO-KR' -> 'ko-KR'. Keep base vs locale
    distinct unless merge_base, which folds 'ko' into its locale form later."""
    norm = col.replace("_", "-")
    parts = norm.split("-")
    lang = parts[0].lower()
    if len(parts) == 1:
        return lang
    return lang + "-" + "-".join(p.upper() for p in parts[1:])


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------


def merge(sources: list[Path], key_col: str | None, merge_base: bool):
    # value[(key, lang)] = (winning_value, winning_source)
    value: dict[tuple[str, str], tuple[str, str]] = {}
    provenance: dict[str, int] = defaultdict(int)   # source -> contributed cells
    conflicts: list[dict] = []
    encodings: dict[str, str] = {}
    all_langs: list[str] = []
    keys_in_order: list[str] = []
    seen_keys: set[str] = set()

    for path in sources:
        rows, enc = read_csv_smart(path)
        encodings[path.name] = enc
        if not rows:
            continue
        headers = list(rows[0].keys())
        kc = key_col if (key_col and key_col in headers) else headers[0]
        for h in headers:
            if h == kc:
                continue
            cl = canon_lang(h, merge_base)
            if cl not in all_langs:
                all_langs.append(cl)

        for row in rows:
            k = (row.get(kc) or "").strip()
            if not k:
                continue
            if k not in seen_keys:
                seen_keys.add(k)
                keys_in_order.append(k)
            for h in headers:
                if h == kc:
                    continue
                lang = canon_lang(h, merge_base)
                val = (row.get(h) or "").strip()
                if not val:
                    continue
                cell = (k, lang)
                if cell not in value:
                    value[cell] = (val, path.name)
                    provenance[path.name] += 1
                elif value[cell][0] != val:
                    # same slot, different value -> conflict (first source wins)
                    conflicts.append({
                        "key": k, "lang": lang,
                        "kept": value[cell][0], "kept_from": value[cell][1],
                        "dropped": val, "dropped_from": path.name,
                    })

    if merge_base:
        value, all_langs = _fold_base(value, all_langs)

    return {
        "value": value, "provenance": dict(provenance), "conflicts": conflicts,
        "encodings": encodings, "langs": all_langs, "keys": keys_in_order,
    }


def _fold_base(value, all_langs):
    """Fold base 'ko' into its locale 'ko-KR' when both exist (last resort)."""
    locales = {l.split("-")[0]: l for l in all_langs if "-" in l}
    new_value = {}
    for (k, lang), v in value.items():
        target = locales.get(lang, lang) if "-" not in lang else lang
        new_value.setdefault((k, target), v)
    langs = [l for l in all_langs if "-" in l or l not in locales]
    return new_value, langs


def write_corpus(result, out_path: Path) -> None:
    langs = result["langs"]
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["key"] + langs)
        for k in result["keys"]:
            w.writerow([k] + [result["value"].get((k, l), ("", ""))[0] for l in langs])


def report(result) -> str:
    out = ["# Merge report\n"]
    out.append("## Sources & encoding\n")
    for name, enc in result["encodings"].items():
        flag = "  ⚠️ legacy" if enc == "cp949" else ""
        out.append(f"- `{name}`: decoded {enc}{flag}, contributed "
                   f"{result['provenance'].get(name, 0)} cells")
    out.append(f"\n- merged languages: {', '.join(f'`{l}`' for l in result['langs'])}")
    out.append(f"- unique keys: {len(result['keys'])}")
    out.append(f"- conflicts: **{len(result['conflicts'])}**\n")
    if result["conflicts"]:
        out.append("## Conflicts (first source kept)\n")
        out.append("| key | lang | kept | from | dropped | from |")
        out.append("|-----|------|------|------|---------|------|")
        for c in result["conflicts"][:40]:
            out.append(f"| {c['key']} | `{c['lang']}` | {c['kept']} | {c['kept_from']} "
                       f"| {c['dropped']} | {c['dropped_from']} |")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Merge heterogeneous multilingual CSVs.")
    p.add_argument("sources", nargs="+", type=Path, help="input CSVs, in trust order")
    p.add_argument("--key", help="key column name (default: first column)")
    p.add_argument("--merge-base", action="store_true",
                   help="fold base lang (ko) into its locale (ko-KR)")
    p.add_argument("-o", "--out", type=Path, help="write the unified CSV here")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    missing = [s for s in args.sources if not s.exists()]
    if missing:
        print(f"file(s) not found: {missing}", file=sys.stderr)
        return 2

    result = merge(args.sources, args.key, args.merge_base)
    if args.out:
        write_corpus(result, args.out)
        print(f"wrote {args.out} ({len(result['keys'])} keys, "
              f"{len(result['langs'])} languages)\n")
    print(report(result))
    return 1 if result["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
