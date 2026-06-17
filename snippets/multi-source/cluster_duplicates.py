#!/usr/bin/env python3
"""Cluster duplicate records across sources with union-find (#2, #20).

WHY THIS EXISTS
---------------
The multi-source merge (build_corpus.py) keys rows by an exact id. Real exports
are not that kind: the same entity shows up as `Cooldown`, `cool-down`, and
`cooldown ` (trailing space) in three files, and a fourth file ties two of them
together by sharing an external id. "Group by exact name" splits one entity into
three; what you want is the *transitive closure* — if A links to B and B links to
C, then A, B, C are one entity, even if A and C never share a signal directly.

That transitive grouping is exactly what union-find (disjoint-set union) computes,
in near-linear time. Each record starts in its own set; every shared signal unions
two sets; the connected components that remain are your deduplicated entities.

HOW LINKING WORKS
-----------------
Each record contributes one or more *signals* (blocking keys): its normalized
surface form, plus any explicit ids it carries. Two records that share any signal
are unioned. This keeps the demo deterministic and stdlib-only; in production a
signal could just as well be "within 1 edit distance" (see edit_distance.py) or a
shared phone/email — the union-find machinery is unchanged.

USAGE
-----
    python cluster_duplicates.py                  # demo over planted duplicates
    python cluster_duplicates.py records.csv --id-col id --signal-cols name,ext_id

Exit code is non-zero when any cluster holds more than one record, so this
doubles as a pre-merge CI gate that fails when duplicates slip in.

Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path


def fold(s: str) -> str:
    """NFKC + casefold + collapse internal whitespace, so 'cool-down', 'Cooldown'
    and 'cooldown ' normalize toward the same signal. Hyphens are dropped because
    they are the most common cosmetic split in this kind of data."""
    base = unicodedata.normalize("NFKC", s).casefold().replace("-", "")
    return " ".join(base.split())


class DisjointSet:
    """Union-find with path compression and union by rank — the two optimizations
    that make a sequence of operations effectively O(n)."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.rank: dict[str, int] = {}

    def add(self, x: str) -> None:
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0

    def find(self, x: str) -> str:
        # path compression: point every node we pass straight at the root
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # union by rank: hang the shorter tree under the taller one
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


# --------------------------------------------------------------------------
# Clustering
# --------------------------------------------------------------------------


def cluster(records: list[dict], id_col: str, signal_cols: list[str]) -> list[list[dict]]:
    """Return clusters (lists of records) joined by any shared signal. A record's
    signals are the folded values of its signal columns; two records sharing a
    signal land in the same set via union-find's transitive closure."""
    dsu = DisjointSet()
    by_id: dict[str, dict] = {}
    signal_to_ids: dict[str, list[str]] = defaultdict(list)

    for rec in records:
        rid = (rec.get(id_col) or "").strip()
        if not rid:
            continue
        by_id[rid] = rec
        dsu.add(rid)
        for col in signal_cols:
            val = fold(rec.get(col, ""))
            if val:
                signal_to_ids[f"{col}={val}"].append(rid)

    # every pair sharing a signal gets unioned (chain through the first id)
    for ids in signal_to_ids.values():
        first = ids[0]
        for other in ids[1:]:
            dsu.union(first, other)

    groups: dict[str, list[dict]] = defaultdict(list)
    for rid, rec in by_id.items():
        groups[dsu.find(rid)].append(rec)
    # largest clusters first; stable within a cluster by id
    clusters = [sorted(g, key=lambda r: r.get(id_col, "")) for g in groups.values()]
    clusters.sort(key=lambda g: (-len(g), g[0].get(id_col, "")))
    return clusters


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------

DEMO_RECORDS = [
    {"id": "r1", "name": "Cooldown",   "ext_id": "X-100"},
    {"id": "r2", "name": "cool-down",  "ext_id": ""},        # links to r1 by name
    {"id": "r3", "name": "Cooldown ",  "ext_id": "X-200"},   # links to r1 by name...
    {"id": "r4", "name": "CD timer",   "ext_id": "X-200"},   # ...and to r3 by ext_id
    {"id": "r5", "name": "Respawn",    "ext_id": "X-300"},
    {"id": "r6", "name": "loot",       "ext_id": ""},
]


def report(clusters: list[list[dict]], id_col: str, signal_cols: list[str]) -> str:
    dupes = [c for c in clusters if len(c) > 1]
    out = ["# Duplicate clustering (union-find)\n",
           f"records: {sum(len(c) for c in clusters)}  ->  "
           f"entities: {len(clusters)}  (clusters with duplicates: {len(dupes)})\n"]
    for i, c in enumerate(clusters, start=1):
        ids = ", ".join(r.get(id_col, "") for r in c)
        names = ", ".join(repr(r.get(signal_cols[0], "")) for r in c)
        tag = "  <- merged" if len(c) > 1 else ""
        out.append(f"- entity {i}: [{ids}] {names}{tag}")
    out.append(
        "\nr1-r4 collapse into one entity even though r1 and r4 share no signal "
        "directly: r1~r2~r3 by name, r3~r4 by ext_id. That transitive join is the "
        "whole point of union-find; exact-match dedup would leave four fragments.")
    return "\n".join(out)


def read_csv_smart(path: Path) -> list[dict]:
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Cluster duplicate records via union-find.")
    p.add_argument("records", nargs="?", type=Path, help="CSV of records (omit for demo)")
    p.add_argument("--id-col", default="id")
    p.add_argument("--signal-cols", default="name,ext_id",
                   help="comma-separated columns to link on")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    signal_cols = [c.strip() for c in args.signal_cols.split(",") if c.strip()]
    if args.records:
        records = read_csv_smart(args.records)
    else:
        records = DEMO_RECORDS
        signal_cols = ["name", "ext_id"]

    clusters = cluster(records, args.id_col, signal_cols)
    print(report(clusters, args.id_col, signal_cols))
    return 1 if any(len(c) > 1 for c in clusters) else 0


if __name__ == "__main__":
    raise SystemExit(main())
