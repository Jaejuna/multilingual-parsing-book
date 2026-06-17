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

    # ---- reasoning: a flat table can't answer these; a graph can -----------

    def ancestors(self, cid: str) -> list[str]:
        """Transitive `broader` chain: loot -> item -> object. A flat glossary
        only knows the direct parent; following the chain (with a cycle guard)
        is what makes domain/type inheritance possible."""
        out: list[str] = []
        seen = {cid}
        cur = self.concepts.get(cid)
        while cur and cur.broader and cur.broader not in seen:
            out.append(cur.broader)
            seen.add(cur.broader)
            cur = self.concepts.get(cur.broader)
        return out

    def descendants(self, cid: str) -> list[str]:
        """Everything transitively narrower than `cid` (inverse of broader)."""
        children = {c.id: [k for k, v in self.concepts.items() if v.broader == c.id]
                    for c in self.concepts.values()}
        out: list[str] = []
        stack = list(children.get(cid, []))
        seen: set[str] = set()
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            out.append(n)
            stack.extend(children.get(n, []))
        return out

    def find_cycles(self) -> list[list[str]]:
        """Detect broken broader-chains that loop (a -> b -> a). A real KG must
        reject these or traversal never terminates."""
        cycles: list[list[str]] = []
        for start in self.concepts:
            path: list[str] = []
            seen: set[str] = set()
            cur: str | None = start
            while cur and cur in self.concepts:
                if cur in seen:
                    if cur == start:
                        cycles.append(path + [cur])
                    break
                seen.add(cur)
                path.append(cur)
                cur = self.concepts[cur].broader
        # dedupe rotations
        uniq: list[list[str]] = []
        for c in cycles:
            if not any(set(c) == set(u) for u in uniq):
                uniq.append(c)
        return uniq

    def topo_order(self) -> list[str]:
        """Concepts ordered broader-before-narrower (a topological sort), via
        Kahn's algorithm. Anything you compute by inheriting down the hierarchy —
        propagating a domain to all narrower concepts, validating that a child
        never contradicts its parent — needs the parents processed first. The
        edge is broader -> concept (object -> item -> loot).

        Kahn: start from in-degree-0 nodes (no broader, or broader outside the
        graph), repeatedly emit one and decrement its children's in-degree. If any
        node never reaches in-degree 0, the remainder sits in a cycle and is
        appended last so the result is still a total order (find_cycles() reports
        the offenders separately). Ties broken by id for determinism."""
        children: dict[str, list[str]] = {cid: [] for cid in self.concepts}
        indeg: dict[str, int] = {cid: 0 for cid in self.concepts}
        for c in self.concepts.values():
            if c.broader and c.broader in self.concepts:
                children[c.broader].append(c.id)
                indeg[c.id] += 1
        ready = sorted(cid for cid, d in indeg.items() if d == 0)
        order: list[str] = []
        while ready:
            node = ready.pop(0)
            order.append(node)
            for kid in children[node]:
                indeg[kid] -= 1
                if indeg[kid] == 0:
                    ready.append(kid)
            ready.sort()   # keep the frontier ordered so the output is stable
        if len(order) < len(self.concepts):
            order.extend(sorted(set(self.concepts) - set(order)))  # cycle remnant
        return order

    def link(self, text: str) -> list[tuple[str, str]]:
        """Entity linking: find glossary surface forms in free text and resolve
        them to concepts. Returns (concept_id, matched_surface) pairs. Latin
        forms need a word boundary (#4); CJK forms match as substrings."""
        import re as _re
        hits: list[tuple[str, str]] = []
        low = text.lower()
        seen_ids: set[str] = set()
        # longest surface first so "AI Director" wins over "ai"
        for surface in sorted(self.surface_index, key=len, reverse=True):
            cid = self.surface_index[surface]
            if cid in seen_ids:
                continue
            is_cjk = any(ord(ch) > 0x2E7F for ch in surface)
            found = (surface in low) if is_cjk else \
                bool(_re.search(rf"\b{_re.escape(surface)}\b", low))
            if found:
                hits.append((cid, surface))
                seen_ids.add(cid)
        return hits

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
    p.add_argument("--ancestors", help="print the transitive broader chain of a concept id")
    p.add_argument("--descendants", help="print everything transitively narrower than a concept id")
    p.add_argument("--topo", action="store_true",
                   help="print concepts in broader-before-narrower (topological) order")
    p.add_argument("--link", help="entity-link: find glossary concepts mentioned in free text")
    p.add_argument("--format", choices=["stats", "json", "triples"], default="stats")
    p.add_argument("--out", help="output path stem (extension added per format)")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if not args.glossary.exists():
        print(f"file not found: {args.glossary}", file=sys.stderr)
        return 2

    g, warnings = build(read_csv_smart(args.glossary))

    cycles = g.find_cycles()
    if cycles:
        for c in cycles:
            print(f"⚠️ broader cycle: {' -> '.join(c)}", file=sys.stderr)

    if args.lookup:
        print(lookup_markdown(g, args.lookup))
        return 0

    if args.ancestors:
        chain = g.ancestors(args.ancestors)
        print(f"{args.ancestors} ⊂ " + " ⊂ ".join(chain) if chain
              else f"{args.ancestors}: no broader concept")
        return 0

    if args.descendants:
        kids = g.descendants(args.descendants)
        print(f"narrower than {args.descendants}: {', '.join(kids) or '(none)'}")
        return 0

    if args.topo:
        print(" -> ".join(g.topo_order()))
        return 0

    if args.link:
        hits = g.link(args.link)
        print(f"text: {args.link!r}")
        for cid, surface in hits:
            labels = g.concepts[cid].labels
            print(f"  '{surface}' -> concept:{cid}  {labels}")
        if not hits:
            print("  (no glossary concepts found)")
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
