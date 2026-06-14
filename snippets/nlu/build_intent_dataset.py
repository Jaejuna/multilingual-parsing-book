#!/usr/bin/env python3
"""Generate a multilingual intent + slot-filling dataset from templates.

WHY THIS EXISTS  (NLU dataset building)
---------------------------------------
The voice-assistant NLU model needs labeled utterances: each example tagged
with an intent (buy_item) and slot spans ({item: "sword", 8, 13}). Hand-
writing thousands of these per language is the bulk of a Linguistic
Engineer's day, and doing it by hand guarantees inconsistent span offsets
and lopsided class balance. The durable approach is templated synthesis:
write a handful of carriers per intent per language, declare slot value
lists, and expand combinatorially -- with the slot character spans computed
automatically so the labels are always exact.

This is the ASR/NLU-facing complement to the rest of the book: same
multilingual discipline (lang-code columns, CJK-aware), different ML task.

INPUT  (templates.json)
-----------------------
    {
      "slots": {
        "item":  {"en": ["sword", "shield"], "ko": ["검", "방패"]},
        "count": {"en": ["two", "three"],    "ko": ["두 개", "세 개"]}
      },
      "intents": {
        "buy_item": {
          "en": ["buy {count} {item}", "purchase a {item}"],
          "ko": ["{item} {count} 구매", "{item} 사줘"]
        }
      }
    }

OUTPUT  (JSONL, one example per line)
-------------------------------------
    {"lang":"en","intent":"buy_item","text":"buy two sword",
     "slots":[{"name":"count","value":"two","start":4,"end":7},
              {"name":"item","value":"sword","start":8,"end":13}]}

The character spans are computed by locating the filled value in the final
string, so downstream BIO/span tagging is exact -- no manual offset math.

USAGE
-----
    python build_intent_dataset.py templates.json
    python build_intent_dataset.py templates.json --max-per-template 20 --seed 7
    python build_intent_dataset.py templates.json --out dataset.jsonl --stats

Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path

_SLOT_RE = re.compile(r"\{(\w+)\}")


@dataclass
class Example:
    lang: str
    intent: str
    text: str
    slots: list[dict]


def slots_in(template: str) -> list[str]:
    return _SLOT_RE.findall(template)


def fill(template: str, values: dict[str, str]) -> Example | None:
    """Fill a template and compute exact character spans for each slot.

    Returns None if any slot value collides ambiguously (the same surface
    text appears twice), since that would make span offsets unreliable --
    better to drop the example than emit a wrong label.
    """
    text = template
    # Replace left-to-right, tracking where each value lands.
    slots: list[dict] = []
    # Build the final string first via a single pass so offsets are stable.
    def _sub(m: re.Match) -> str:
        return values[m.group(1)]
    final = _SLOT_RE.sub(_sub, template)

    # Locate each filled value. Walk the names in template order, advancing a
    # cursor so repeated values don't all resolve to the first occurrence.
    cursor = 0
    for name in slots_in(template):
        val = values[name]
        idx = final.find(val, cursor)
        if idx < 0:
            return None
        slots.append({"name": name, "value": val, "start": idx, "end": idx + len(val)})
        cursor = idx + len(val)
    return Example(lang="", intent="", text=final, slots=slots)


def generate(
    spec: dict,
    max_per_template: int,
    rng: random.Random,
) -> list[Example]:
    slot_defs = spec.get("slots", {})
    intents = spec.get("intents", {})
    out: list[Example] = []

    for intent, by_lang in intents.items():
        for lang, templates in by_lang.items():
            for template in templates:
                names = slots_in(template)
                # gather candidate values per slot for this language
                value_lists = []
                ok = True
                for n in names:
                    vals = slot_defs.get(n, {}).get(lang)
                    if not vals:
                        ok = False
                        break
                    value_lists.append([(n, v) for v in vals])
                if not ok:
                    continue
                if names:
                    combos = list(itertools.product(*value_lists))
                    rng.shuffle(combos)
                    combos = combos[:max_per_template]
                else:
                    combos = [()]
                for combo in combos:
                    values = {n: v for n, v in combo}
                    ex = fill(template, values)
                    if ex is None:
                        continue
                    ex.lang, ex.intent = lang, intent
                    out.append(ex)
    return out


def stats_report(examples: list[Example]) -> str:
    by_intent = Counter(e.intent for e in examples)
    by_lang = Counter(e.lang for e in examples)
    by_pair = Counter((e.lang, e.intent) for e in examples)
    out: list[str] = []
    w = out.append
    w("# NLU dataset stats\n")
    w(f"- examples: **{len(examples)}**")
    w(f"- intents: {len(by_intent)}  ·  languages: {len(by_lang)}\n")

    w("## Per intent\n| intent | examples |\n|--------|----------|")
    for k, n in by_intent.most_common():
        w(f"| `{k}` | {n} |")
    w("")
    w("## Per language\n| lang | examples |\n|------|----------|")
    for k, n in by_lang.most_common():
        w(f"| `{k}` | {n} |")
    w("")

    # class-balance warning: any (lang,intent) cell far below the mean
    if by_pair:
        mean = sum(by_pair.values()) / len(by_pair)
        thin = [(p, n) for p, n in by_pair.items() if n < mean * 0.5]
        if thin:
            w("## ⚠️ Thin (lang, intent) cells (<50% of mean)\n")
            for (lang, intent), n in sorted(thin, key=lambda x: x[1]):
                w(f"- `{lang}`/`{intent}`: {n} (mean {mean:.1f}) — add carriers")
            w("")
        else:
            w("## ✅ Class balance across (lang, intent) looks even\n")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate a multilingual NLU dataset.")
    p.add_argument("templates", type=Path)
    p.add_argument("--max-per-template", type=int, default=10,
                   help="cap slot-combo expansions per template (default 10)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", help="write JSONL here (otherwise stdout)")
    p.add_argument("--stats", action="store_true", help="print a stats report to stderr")
    args = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if not args.templates.exists():
        print(f"file not found: {args.templates}", file=sys.stderr)
        return 2

    spec = json.loads(args.templates.read_text(encoding="utf-8"))
    examples = generate(spec, args.max_per_template, random.Random(args.seed))

    lines = [json.dumps(asdict(e), ensure_ascii=False) for e in examples]
    if args.out:
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {len(examples)} examples to {args.out}")
    else:
        print("\n".join(lines))

    if args.stats:
        print("\n" + stats_report(examples), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
