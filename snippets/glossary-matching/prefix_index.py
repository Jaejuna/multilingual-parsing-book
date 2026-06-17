#!/usr/bin/env python3
"""Prefix index (trie): typeahead and longest-prefix term lookup.

WHY THIS EXISTS
---------------
Two everyday glossary jobs are awkward with a flat list and great with a trie:

1. Typeahead. An editor types `cool` and wants every glossary term starting with
   it. Scanning the whole list per keystroke is O(terms x len); a trie walks
   straight to the branch and yields matches in O(len(prefix) + hits).

2. Longest-prefix segmentation. Scripts without spaces (Japanese, Chinese, Thai)
   need you to find, at a given position, the *longest* glossary term that starts
   there — the building block of dictionary-based word segmentation. A trie does
   this in one downward walk, remembering the deepest terminal it passed.

A trie is also what Aho-Corasick (see aho_corasick_match.py) is built on; this is
the same data structure without the failure links, so it reads as the gentler
introduction.

WHEN NOT TO
-----------
For a handful of terms a sorted list + bisect is simpler. The trie earns its keep
when the term set is large, queried often (typeahead), or you need
longest-prefix-at-a-position rather than whole-string membership.

USAGE
-----
    python prefix_index.py                       # demo: prefix queries + segmentation
    python prefix_index.py --prefix cool
    python prefix_index.py --segment "戦利品をアイテム化する"

Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata


def fold(s: str) -> str:
    """NFKC + casefold so lookups are width- and case-insensitive. The original
    surface is kept alongside each terminal so we can return it verbatim."""
    return unicodedata.normalize("NFKC", s).casefold()


class Trie:
    """Character trie over folded keys. Each node is a dict of child chars; a
    terminal node stores the original (unfolded) term so results read naturally."""

    def __init__(self) -> None:
        self.children: dict[str, "Trie"] = {}
        self.term: str | None = None     # original surface form, set at terminals

    def insert(self, term: str) -> None:
        node = self
        for ch in fold(term):
            node = node.children.setdefault(ch, Trie())
        node.term = term

    def __contains__(self, term: str) -> bool:
        node = self._walk(fold(term))
        return node is not None and node.term is not None

    def _walk(self, folded: str) -> "Trie | None":
        node = self
        for ch in folded:
            nxt = node.children.get(ch)
            if nxt is None:
                return None
            node = nxt
        return node

    def keys_with_prefix(self, prefix: str) -> list[str]:
        """Every stored term whose folded form starts with `prefix` (typeahead),
        sorted for a stable display order."""
        node = self._walk(fold(prefix))
        if node is None:
            return []
        out: list[str] = []
        self._collect(node, out)
        return sorted(out)

    @staticmethod
    def _collect(node: "Trie", out: list[str]) -> None:
        if node.term is not None:
            out.append(node.term)
        for child in node.children.values():
            Trie._collect(child, out)

    def longest_prefix_of(self, text: str, start: int = 0) -> str | None:
        """The longest stored term that is a prefix of text[start:] (in folded
        space). Returns the original surface form, or None if nothing matches."""
        node = self
        best: str | None = None
        folded = fold(text)
        for ch in folded[start:]:
            node = node.children.get(ch)
            if node is None:
                break
            if node.term is not None:
                best = node.term
        return best

    def segment(self, text: str) -> list[str]:
        """Greedy longest-match (maximum-matching) tokenization: at each position
        take the longest term in the trie, else emit one character and advance.
        Note folding can change length (e.g. full-width digits), so we advance by
        the folded match length over a folded copy to keep indices aligned."""
        folded = fold(text)
        tokens: list[str] = []
        i = 0
        while i < len(folded):
            node = self
            match_len = 0
            j = i
            while j < len(folded) and folded[j] in node.children:
                node = node.children[folded[j]]
                j += 1
                if node.term is not None:
                    match_len = j - i
            if match_len:
                tokens.append(folded[i:i + match_len])
                i += match_len
            else:
                tokens.append(folded[i])
                i += 1
        return tokens


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------

GLOSSARY = ["cool", "cooldown", "cooldown timer", "coop", "respawn", "loot",
            "戦利品", "アイテム", "アイテム化"]
PREFIX_QUERIES = ["cool", "coo", "re", "zz"]
SEGMENT_SAMPLES = ["戦利品をアイテム化する", "cooldown timer ready"]


def demo() -> str:
    t = Trie()
    for term in GLOSSARY:
        t.insert(term)

    out = ["# Prefix index (trie)\n",
           f"glossary: {', '.join(GLOSSARY)}\n",
           "## Typeahead — keys with prefix\n",
           "| prefix | matches |",
           "|--------|---------|"]
    for q in PREFIX_QUERIES:
        hits = t.keys_with_prefix(q)
        out.append(f"| `{q}` | {', '.join(f'`{h}`' for h in hits) or '—'} |")

    out.append("\n## Longest-prefix segmentation (dictionary maximum-matching)\n")
    for s in SEGMENT_SAMPLES:
        out.append(f"- `{s}` -> {' | '.join(t.segment(s))}")
    out.append(
        "\nTypeahead walks straight to the branch; segmentation takes the longest "
        "term at each position (`アイテム化`, not `アイテム`), falling back to single "
        "characters between known terms. Out-of-glossary runs survive as char tokens.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Trie-based prefix index and segmentation.")
    p.add_argument("--prefix", help="list demo-glossary terms with this prefix")
    p.add_argument("--segment", help="segment text by longest dictionary match")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if args.prefix or args.segment:
        t = Trie()
        for term in GLOSSARY:
            t.insert(term)
        if args.prefix:
            print(t.keys_with_prefix(args.prefix))
        if args.segment:
            print(" | ".join(t.segment(args.segment)))
        return 0

    print(demo())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
