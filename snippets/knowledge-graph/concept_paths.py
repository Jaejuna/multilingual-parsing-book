#!/usr/bin/env python3
"""Paths between concepts: BFS, DFS, and weighted shortest path (#11, #19).

WHY THIS EXISTS
---------------
Chapter 19 walks the `broader` chain straight up. But a lexicon is a general
graph — concepts relate sideways (synonyms, shared domain, cross-references), not
just up and down — and the useful question becomes "how far apart are these two
concepts, and by what route?" That is a graph-traversal question, and which
traversal you pick changes the answer:

- **BFS** finds the path with the *fewest hops* (every edge counts as 1). Good for
  "how many relationship steps separate `sword` from `gold`?"
- **DFS** answers reachability/containment ("is `gun` somewhere under `object`?")
  and is the backbone of connected-components and cycle checks.
- **Dijkstra** finds the path of least *total weight* when edges carry a cost
  (a weak/expensive relation should not be treated like a strong one). The
  cheapest route often has *more* hops than the BFS route — that contrast is the
  whole reason both exist.

WHAT'S HERE
-----------
- bfs_path(adj, src, dst)   : fewest-edges path, or None
- dfs_reachable(adj, src)   : every node reachable from src (iterative DFS)
- dijkstra(adj, src)        : least-weight distances + a path reconstructor

`adj` is an undirected adjacency map: {node: [(neighbor, weight), ...]}.

USAGE
-----
    python concept_paths.py                  # demo: BFS vs Dijkstra on a lexicon
    python concept_paths.py --from sword --to gold

Stdlib only (collections, heapq). Python 3.10+.
"""

from __future__ import annotations

import argparse
import heapq
import sys
from collections import deque


def bfs_path(adj: dict[str, list[tuple[str, float]]], src: str, dst: str) -> list[str] | None:
    """Shortest path by edge count, via breadth-first search. The first time BFS
    reaches a node it has done so along a fewest-hops route, so the parent map it
    builds reconstructs an optimal (unweighted) path. O(V + E)."""
    if src == dst:
        return [src]
    parent: dict[str, str | None] = {src: None}
    q = deque([src])
    while q:
        node = q.popleft()
        for nbr, _w in adj.get(node, ()):
            if nbr not in parent:
                parent[nbr] = node
                if nbr == dst:
                    return _rebuild(parent, dst)
                q.append(nbr)
    return None


def dfs_reachable(adj: dict[str, list[tuple[str, float]]], src: str) -> set[str]:
    """Every node reachable from src, via iterative depth-first search (an explicit
    stack, so deep graphs don't blow the recursion limit). The `seen` set is what
    turns DFS from infinite-on-cycles into linear-time connectivity. O(V + E)."""
    seen: set[str] = set()
    stack = [src]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        for nbr, _w in adj.get(node, ()):
            if nbr not in seen:
                stack.append(nbr)
    return seen


def dijkstra(adj: dict[str, list[tuple[str, float]]], src: str):
    """Least-weight distance from src to every node, plus a parent map for path
    reconstruction. A min-heap always expands the closest not-yet-finalized node;
    because all weights are non-negative, the first time a node is popped its
    distance is final (the classic Dijkstra invariant). O(E log V).

    Returns (dist, parent); call path_from(parent, dst) to reconstruct."""
    dist: dict[str, float] = {src: 0.0}
    parent: dict[str, str | None] = {src: None}
    heap: list[tuple[float, str]] = [(0.0, src)]
    done: set[str] = set()
    while heap:
        d, node = heapq.heappop(heap)
        if node in done:
            continue                       # a stale, larger entry — skip it
        done.add(node)
        for nbr, w in adj.get(node, ()):
            nd = d + w
            if nd < dist.get(nbr, float("inf")):
                dist[nbr] = nd
                parent[nbr] = node
                heapq.heappush(heap, (nd, nbr))
    return dist, parent


def path_from(parent: dict[str, str | None], dst: str) -> list[str] | None:
    if dst not in parent:
        return None
    return _rebuild(parent, dst)


def _rebuild(parent: dict[str, str | None], dst: str) -> list[str]:
    path = [dst]
    while parent[path[-1]] is not None:
        path.append(parent[path[-1]])   # type: ignore[arg-type]
    return list(reversed(path))


def make_undirected(edges: list[tuple[str, str, float]]) -> dict[str, list[tuple[str, float]]]:
    adj: dict[str, list[tuple[str, float]]] = {}
    for a, b, w in edges:
        adj.setdefault(a, []).append((b, w))
        adj.setdefault(b, []).append((a, w))
    return adj


# --------------------------------------------------------------------------
# Demo: a small lexicon graph where fewest-hops != cheapest
# --------------------------------------------------------------------------

# weight = "semantic distance": small for tight relations, large for weak ones
DEMO_EDGES = [
    ("object", "item", 1.0),
    ("item", "loot", 1.0),
    ("item", "weapon", 1.0),
    ("weapon", "sword", 1.0),
    ("weapon", "gun", 1.0),
    ("loot", "gold", 5.0),        # direct but weak (expensive) link
    ("loot", "currency", 2.0),
    ("currency", "gold", 1.0),    # the scenic route is cheaper overall
]


def demo() -> str:
    adj = make_undirected(DEMO_EDGES)
    hop_path = bfs_path(adj, "sword", "gold")
    dist, parent = dijkstra(adj, "sword")
    cheap_path = path_from(parent, "gold")
    reachable = dfs_reachable(adj, "object")
    out = ["# Concept paths: BFS vs Dijkstra\n",
           "edges carry a 'semantic distance' weight; loot-gold is a direct but "
           "weak (cost 5) link.\n",
           f"- BFS (fewest hops)   sword -> gold: {' -> '.join(hop_path)}  "
           f"({len(hop_path) - 1} hops)",
           f"- Dijkstra (least cost) sword -> gold: {' -> '.join(cheap_path)}  "
           f"(cost {dist['gold']:.0f}, {len(cheap_path) - 1} hops)",
           f"- DFS reachable from `object`: {', '.join(sorted(reachable))}\n",
           "BFS takes the 4-hop route through the cheap-looking direct edge; "
           "Dijkstra prefers the 5-hop scenic route because its total cost (6) "
           "beats the direct edge's (8). Fewest hops and least cost are different "
           "questions — pick the traversal that matches the one you are asking."]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="BFS / DFS / Dijkstra over a concept graph.")
    p.add_argument("--from", dest="src", help="source concept (demo graph)")
    p.add_argument("--to", dest="dst", help="target concept (demo graph)")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if args.src and args.dst:
        adj = make_undirected(DEMO_EDGES)
        hops = bfs_path(adj, args.src, args.dst)
        dist, parent = dijkstra(adj, args.src)
        print(f"BFS fewest-hops : {hops}")
        print(f"Dijkstra cheapest: {path_from(parent, args.dst)} "
              f"(cost {dist.get(args.dst, float('inf'))})")
        return 0

    print(demo())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
