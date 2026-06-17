#!/usr/bin/env python3
"""Backtracking: generate constrained slot combinations without the full product (#13).

WHY THIS EXISTS
---------------
The intent-dataset builder (#13) fills templates from slot values. The naive way
to enumerate multi-slot utterances is the full Cartesian product — every value of
every slot crossed with every other. That explodes, and most of it is junk: you
rarely want an utterance that puts the same entity in two slots ("trade sword for
sword"), or that pairs values a domain rule forbids.

Backtracking builds each combination one slot at a time and *prunes* the moment a
partial assignment violates a constraint — abandoning a whole subtree of dead
combinations instead of generating and filtering them. It is the standard tool for
"enumerate all valid configurations": the same shape as N-queens, Sudoku, or
subset/permutation generation, here producing clean training utterances.

WHAT'S HERE
-----------
- expand(slots, constraint)         : all complete assignments passing `constraint`
- count_nodes(slots, constraint)    : assignments AND search-tree nodes visited,
                                      so you can see pruning beat the product

`slots` is an ordered dict {slot_name: [candidate values]}. `constraint` takes a
partial assignment (dict) and returns False to prune that branch early.

USAGE
-----
    python constraint_expand.py                  # demo: distinct-value utterances
    python constraint_expand.py --json

Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Callable


Assignment = dict[str, str]


def expand(slots: dict[str, list[str]], constraint: Callable[[Assignment], bool]) -> list[Assignment]:
    """All complete assignments of values to slots that satisfy `constraint`.

    Depth-first over the slot order: at each slot try every candidate, recurse only
    while the partial assignment still passes the constraint, and undo the choice
    on the way back up (the 'backtrack'). Pruning at a partial assignment skips its
    entire subtree."""
    names = list(slots)
    results: list[Assignment] = []
    partial: Assignment = {}

    def recurse(depth: int) -> None:
        if depth == len(names):
            results.append(dict(partial))
            return
        name = names[depth]
        for value in slots[name]:
            partial[name] = value
            if constraint(partial):          # prune: only descend if still valid
                recurse(depth + 1)
            del partial[name]                # undo before trying the next value

    if constraint({}):
        recurse(0)
    return results


def count_nodes(slots: dict[str, list[str]], constraint: Callable[[Assignment], bool]) -> tuple[int, int, int]:
    """Return (valid_assignments, nodes_visited, full_product_size) to make the
    pruning visible: nodes_visited is how many partial assignments backtracking
    even looked at, vs the product it would have enumerated blindly."""
    names = list(slots)
    product = 1
    for n in names:
        product *= len(slots[n])
    visited = 0
    valid = 0
    partial: Assignment = {}

    def recurse(depth: int) -> None:
        nonlocal visited, valid
        if depth == len(names):
            valid += 1
            return
        for value in slots[names[depth]]:
            partial[names[depth]] = value
            visited += 1
            if constraint(partial):
                recurse(depth + 1)
            del partial[names[depth]]

    if constraint({}):
        recurse(0)
    return valid, visited, product


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------

DEMO_SLOTS = {
    "give":    ["sword", "gold", "potion"],
    "receive": ["sword", "gold", "potion"],
    "with":    ["Ada", "Bjorn"],
}


def all_distinct(partial: Assignment) -> bool:
    """Prune any trade where give == receive (can't trade a thing for itself).
    Checking on the *partial* assignment is what kills the subtree early."""
    vals = [v for k, v in partial.items() if k in ("give", "receive")]
    return len(vals) == len(set(vals))


def demo(as_json: bool) -> str:
    valid, visited, product = count_nodes(DEMO_SLOTS, all_distinct)
    combos = expand(DEMO_SLOTS, all_distinct)
    if as_json:
        return json.dumps({"valid": valid, "nodes_visited": visited,
                           "full_product": product, "combinations": combos},
                          ensure_ascii=False, indent=2)
    out = ["# Backtracking: constrained slot combinations\n",
           f"slots: { {k: v for k, v in DEMO_SLOTS.items()} }\n",
           f"constraint: give != receive\n",
           f"- full Cartesian product would be: **{product}** combinations",
           f"- backtracking visited only: **{visited}** tree nodes",
           f"- valid combinations produced: **{valid}**\n",
           "Sample valid utterances:"]
    for c in combos[:6]:
        out.append(f"- trade {c['give']} for {c['receive']} with {c['with']}")
    out.append(
        "\nPruning give==receive at the second slot discards a third of the tree "
        "before the third slot is ever chosen — backtracking generates the valid "
        "set directly instead of building the full product and filtering.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Backtracking combination generator with pruning.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    print(demo(args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
