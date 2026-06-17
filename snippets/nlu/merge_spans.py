#!/usr/bin/env python3
"""Merge and de-conflict annotation spans by a sort-and-sweep (#13).

WHY THIS EXISTS
---------------
Slot-filling data (see build_intent_dataset.py) is a list of labeled character
spans: (start, end, label). When spans come from several annotators, several
passes, or a rule-based pre-tagger plus a model, they overlap. Two kinds of
overlap mean very different things:

- Same label, touching/overlapping  -> the same mention split in two; MERGE them
  into one span so downstream training sees one slot, not two fragments.
- Different labels, overlapping      -> a genuine CONFLICT (is "AI Director" an
  ORG or a TITLE?); these must be surfaced, never silently merged away.

The efficient way to find every overlap is not to compare all pairs (O(n^2)) but
to sort the spans by start position and sweep left to right (O(n log n)), keeping
only the current run. That sweep is the same interval-merge pattern used for
calendars, genome intervals, and memory ranges — here it cleans annotation data.

WHAT'S HERE
-----------
- merge_spans(spans)   : merge same-label overlaps; return cleaned spans
- find_conflicts(spans): list overlapping pairs with different labels
- Span                 : a (start, end, label) record with overlap helpers

USAGE
-----
    python merge_spans.py            # demo over overlapping annotations
    python merge_spans.py --json     # machine-readable

Exit code is non-zero when label conflicts remain, so a labeling pipeline can
gate on it. Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    start: int
    end: int          # exclusive, like Python slicing
    label: str

    def overlaps(self, other: "Span") -> bool:
        # half-open intervals overlap iff each starts before the other ends
        return self.start < other.end and other.start < self.end

    def adjacent_or_overlaps(self, other: "Span") -> bool:
        # touching (end == start) counts as joinable for same-label merging
        return self.start <= other.end and other.start <= self.end


def merge_spans(spans: list[Span]) -> list[Span]:
    """Merge spans that share a label and touch or overlap, via a single sweep.

    Sort by (start, end); walk left to right holding the current run per label.
    A new span that touches/overlaps the run's last span of its label extends it;
    otherwise it starts a fresh span. Different-label overlaps are left intact
    here — merging them would hide a conflict — and reported by find_conflicts."""
    ordered = sorted(spans, key=lambda s: (s.start, s.end))
    merged: list[Span] = []
    last_by_label: dict[str, int] = {}      # label -> index in `merged`
    for sp in ordered:
        idx = last_by_label.get(sp.label)
        if idx is not None and merged[idx].adjacent_or_overlaps(sp):
            prev = merged[idx]
            merged[idx] = Span(prev.start, max(prev.end, sp.end), sp.label)
        else:
            last_by_label[sp.label] = len(merged)
            merged.append(sp)
    return sorted(merged, key=lambda s: (s.start, s.end))


def find_conflicts(spans: list[Span]) -> list[tuple[Span, Span]]:
    """Overlapping pairs with different labels, found by the same sweep. Keep an
    'active' set of spans whose end is still ahead of the current start; any
    active span with a different label is a conflict. O(n log n + overlaps)."""
    ordered = sorted(spans, key=lambda s: (s.start, s.end))
    active: list[Span] = []
    conflicts: list[tuple[Span, Span]] = []
    for sp in ordered:
        active = [a for a in active if a.end > sp.start]   # drop the finished ones
        for a in active:
            if a.label != sp.label and a.overlaps(sp):
                conflicts.append((a, sp))
        active.append(sp)
    return conflicts


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------

DEMO_TEXT = "buy the AI Director skin now"
DEMO_SPANS = [
    Span(8, 10, "ORG"),     # "AI"          ┐ same label, overlapping -> merge
    Span(8, 19, "ORG"),     # "AI Director" ┘ into one ORG span
    Span(11, 19, "TITLE"),  # "Director"      different label over ORG -> conflict
    Span(20, 24, "ITEM"),   # "skin"          clean, stands alone
]


def demo(as_json: bool) -> tuple[str, int]:
    merged = merge_spans(DEMO_SPANS)
    conflicts = find_conflicts(DEMO_SPANS)
    if as_json:
        payload = {
            "merged": [vars(s) for s in merged],
            "conflicts": [[vars(a), vars(b)] for a, b in conflicts],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2), len(conflicts)

    def show(s: Span) -> str:
        return f"[{s.start:>2},{s.end:>2}) {s.label:<6} {DEMO_TEXT[s.start:s.end]!r}"

    out = [f"# Span merge + conflict detection\n", f"text: {DEMO_TEXT!r}\n",
           f"input spans: {len(DEMO_SPANS)}  ->  merged spans: {len(merged)}\n",
           "## Merged (same-label overlaps joined)\n"]
    out += [f"- {show(s)}" for s in merged]
    out.append(f"\n## Label conflicts: {len(conflicts)}\n")
    for a, b in conflicts:
        out.append(f"- {show(a)}  vs  {show(b)}")
    out.append(
        "\nThe two ORG spans for 'AI' and 'AI Director' collapse into one; the "
        "TITLE 'Director' overlapping that ORG is reported, not silently dropped — "
        "that is a human decision, not something the merger should guess.")
    return "\n".join(out), len(conflicts)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Merge annotation spans and flag conflicts.")
    p.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    text, n_conflicts = demo(args.json)
    print(text)
    return 1 if n_conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
