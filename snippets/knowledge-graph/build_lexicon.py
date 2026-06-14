#!/usr/bin/env python3
"""Promote a flat glossary CSV into a multilingual lexicon / mini-ontology.

WHY THIS EXISTS  (knowledge graph + lexicon)
--------------------------------------------
A glossary CSV is a spreadsheet: one row, one term, N translation columns.
It answers "how do I say X in Japanese" and nothing else. The moment
product asks "which terms belong to the COMBAT domain", "what are the
synonyms of this concept", or "given the surface form 戦利品, what concept
is that and what are all its cross-lingual labels", the flat table falls
over. Those are graph questions.

This script lifts the flat glossary into a small concept graph -- the
shape a Knowledge Graph / ontology integration actually consumes:

    Concept ──hasLabel──▶ "loot"@en, "戦利品"@ja, "전리품"@ko
       │ inDomain ▶ Combat
       │ pos ▶ noun
       ├─ broader ▶ Concept(item)
       └─ synonym ▶ Concept(plunder)

It then exports the graph as triples (N-Triples-ish) and JSON, and offers
a reverse index: any surface form in any language → its concept → every
other label. That reverse index is exactly what a lexicon-backed NLU or
glossary augmenter needs.

INPUT  (one row per concept)
----------------------------
    id, en, ko, ja, zh-CN, domain, pos, synonyms, broader
    loot, loot, 전리품, 戦利品, 战利品, Combat, noun, plunder|spoils, item

- id        : stable concept id
- <lang>    : label in each language (any BCP-47 column is treated as a label)
- domain    : optional ontology domain/category
- pos       : optional part of speech
- synonyms  : optional '|'-separated alternate surface forms (same concept)
- broader   : optional id of a broader/parent concept (hypernym)

USAGE
-----
    python build_lexicon.py glossary.csv
    python build_lexicon.py glossary.csv --format triples --out lexicon
    python build_lexicon.py glossary.csv --lookup 戦利品

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

# Columns that are metadata, not language labels.
_META_COLS = {"id", "domain", "pos", "synonyms", "broader", "note"}
_LANG_RE = re.compile(r"^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})*$")


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


# --------------------------------------------------------------------------
# Graph model
# --------------------------------------------------------------------------


@dataclass
class Concept:
    id: str
    labels: dict[str, str] = field(default_factory=dict)   # lang -> label
    domain: str | None = None
    pos: str | None = None
    synonyms: list[str] = field(default_factory=list)
    broader: str | None = None


@dataclass
class Graph:
    concepts: dict[str, Concept] = field(default_factory=dict)
    # reverse: normalized surface form -> concept id
    surface_index: dict[str, str] = field(default_factory=dict)

    def add(self, c: Concept) -> None:
        self.concepts[c.id] = c
        for label in list(c.labels.values()) + c.synonyms:
            key = label.strip().lower()
            if key:
                self.surface_index[key] = c.id

    def lookup(self, surface: str) -> Concept | None:
        cid = self.surface_index.get(surface.strip().lower())
        return self.concepts.get(cid) if cid else None

    def triples(self) -> list[tuple[str, str, str]]:
        """Flatten to (subject, predicate, object) triples."""
        out: list[tuple[str, str, str]] = []
        for c in self.concepts.values():
            s = f"concept:{c.id}"
            out.append((s, "rdf:type", "Concept"))
            for lang, label in c.labels.items():
                out.append((s, "skos:prefLabel", f'"{label}"@{lang}'))
            for syn in c.synonyms:
                out.append((s, "skos:altLabel", f'"{syn}"'))
            if c.domain:
                out.append((s, "dct:subject", f"domain:{c.domain}"))
            if c.pos:
                out.append((s, "lexinfo:pos", c.pos))
            if c.broader:
                out.append((s, "skos:broader", f"concept:{c.broader}"))
        return out


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


def build(rows: list[dict[str, str]]) -> tuple[Graph, list[str]]:
    if not rows:
        raise SystemExit("empty glossary")
    cols = list(rows[0].keys())
    lang_cols = [c for c in cols if c not in _META_COLS and _LANG_RE.match(c)]

    g = Graph()
    warnings: list[str] = []
    for row in rows:
        cid = (row.get("id") or "").strip()
        if not cid:
            # fall back to the first language label as the id
            cid = next((row.get(l, "").strip() for l in lang_cols if row.get(l)), "")
        if not cid:
            warnings.append("row with no id and no labels skipped")
            continue
        labels = {l: row[l].strip() for l in lang_cols if (row.get(l) or "").strip()}
        syns = [s.strip() for s in (row.get("synonyms") or "").split("|") if s.strip()]
        g.add(Concept(
            id=cid,
            labels=labels,
            domain=(row.get("domain") or "").strip() or None,
            pos=(row.get("pos") or "").strip() or None,
            synonyms=syns,
            broader=(row.get("broader") or "").strip() or None,
        ))

    # validate broader references resolve
    for c in g.concepts.values():
        if c.broader and c.broader not in g.concepts:
            warnings.append(f"concept '{c.id}' broader -> '{c.broader}' (unresolved)")
    return g, warnings


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def stats_markdown(g: Graph, warnings: list[str]) -> str:
    out: list[str] = []
    w = out.append
    langs = sorted({l for c in g.concepts.values() for l in c.labels})
    domains: dict[str, int] = {}
    for c in g.concepts.values():
        if c.domain:
            domains[c.domain] = domains.get(c.domain, 0) + 1
    n_broader = sum(1 for c in g.concepts.values() if c.broader)
    n_syn = sum(len(c.synonyms) for c in g.concepts.values())

    w("# Lexicon graph\n")
    w(f"- concepts: **{len(g.concepts)}**")
    w(f"- languages: {', '.join(f'`{l}`' for l in langs)}")
    w(f"- triples: **{len(g.triples())}**")
    w(f"- broader (hypernym) edges: {n_broader}")
    w(f"- synonym surface forms: {n_syn}")
    w(f"- surface forms indexed (cross-lingual lookup): {len(g.surface_index)}\n")
    if domains:
        w("## Domains\n")
        for d, n in sorted(domains.items(), key=lambda kv: -kv[1]):
            w(f"- `{d}`: {n} concept(s)")
        w("")
    if warnings:
        w("## ⚠️ Warnings\n")
        for x in warnings:
            w(f"- {x}")
        w("")
    return "\n".join(out)


def lookup_markdown(g: Graph, surface: str) -> str:
    c = g.lookup(surface)
    if not c:
        return f"'{surface}' — not found in lexicon"
    out = [f"# Lookup: '{surface}' → concept `{c.id}`\n"]
    out.append("Cross-lingual labels:")
    for lang, label in c.labels.items():
        out.append(f"- `{lang}`: {label}")
    if c.synonyms:
        out.append(f"\nSynonyms: {', '.join(c.synonyms)}")
    if c.domain:
        out.append(f"\nDomain: {c.domain}")
    if c.broader and c.broader in g.concepts:
        parent = g.concepts[c.broader]
        out.append(f"Broader: {c.broader} ({parent.labels})")
    return "\n".join(out)


def triples_text(g: Graph) -> str:
    return "\n".join(f"{s} {p} {o} ." for s, p, o in g.triples())


def graph_json(g: Graph) -> str:
    return json.dumps(
        {
            "concepts": {cid: asdict(c) for cid, c in g.concepts.items()},
            "surface_index": g.surface_index,
        },
        ensure_ascii=False,
        indent=2,
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build a multilingual lexicon graph from a glossary.")
    p.add_argument("glossary", type=Path)
    p.add_argument("--lookup", help="resolve a surface form (any language) to its concept")
    p.add_argument("--format", choices=["stats", "json", "triples"], default="stats")
    p.add_argument("--out", help="output path stem (extension added per format)")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if not args.glossary.exists():
        print(f"file not found: {args.glossary}", file=sys.stderr)
        return 2

    g, warnings = build(read_csv_smart(args.glossary))

    if args.lookup:
        print(lookup_markdown(g, args.lookup))
        return 0

    rendered = {
        "stats": stats_markdown(g, warnings),
        "json": graph_json(g),
        "triples": triples_text(g),
    }[args.format]

    if args.out:
        ext = {"stats": ".md", "json": ".json", "triples": ".nt"}[args.format]
        Path(args.out).with_suffix(ext).write_text(rendered, encoding="utf-8")
        print(f"wrote {Path(args.out).with_suffix(ext)}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
