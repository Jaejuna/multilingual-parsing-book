"""
mojibake_recover.py

Pattern: recover the original text from mojibake by guessing the
"mis-decode pair" that produced it.

When you should reach for this
------------------------------
You see broken-looking text like ``ë³´íµ`` or ``?쒕쾲`` and want to know:

  (a) what encoding the bytes ACTUALLY were, and
  (b) what encoding the consumer mistakenly used to decode them.

Most real-world mojibake comes from one of a handful of (true, used)
pairs. This script tries them all and prints any that yield readable
output.

Common pairs in Korean projects
-------------------------------
+----------------+----------------+----------------------------------+
| Looks like     | True encoding  | Wrongly decoded as               |
+================+================+==================================+
| ``ë³´íµ``      | UTF-8          | latin-1 / cp1252                 |
| ``?쒕쾲``       | UTF-8          | cp949                            |
| ``ÇÑ±¹¾î``     | cp949 / EUC-KR | latin-1                          |
| (extra ``ï»¿``) | UTF-8 + BOM    | latin-1 (BOM rendered literally) |
+----------------+----------------+----------------------------------+

Usage
-----
    $ python mojibake_recover.py "ë³´íµ"
    latin-1 -> utf-8 : 보통

If multiple candidates print, the one that looks like a real word in
your target language is the answer.
"""

from __future__ import annotations

import sys
from itertools import product


# Encodings to try as "what the consumer wrongly decoded the bytes as".
# In other words: the input string we receive was produced by
# ``raw_bytes.decode(WRONG)`` somewhere upstream.
WRONG_ENCODINGS = ["latin-1", "cp1252", "cp949", "utf-8"]

# Encodings to try as "what the bytes really were".
# I.e. once we have the raw bytes back via ``s.encode(WRONG)``, we want
# to know which decoder produces sensible text.
TRUE_ENCODINGS = ["utf-8", "cp949", "shift_jis", "gb18030", "big5"]


def recover(mojibake: str) -> list[tuple[str, str, str]]:
    """Return a list of (wrong, true, recovered_text) triples.

    Each triple represents one plausible explanation: "if the upstream
    decoded these bytes as ``wrong``, the true encoding was ``true``,
    and the original text was ``recovered_text``."

    We filter out:
      * round-trips that don't change the text (the trivial identity)
      * recoveries that raise — those pairs are simply incompatible
    """

    results: list[tuple[str, str, str]] = []

    for wrong, true in product(WRONG_ENCODINGS, TRUE_ENCODINGS):
        if wrong == true:
            continue
        try:
            # Step 1: reverse the consumer's wrong decode to get raw bytes
            # back. Errors here mean the input contains characters that
            # don't fit in ``wrong``, so the pair is impossible.
            raw = mojibake.encode(wrong)
            # Step 2: decode those bytes with the candidate true encoding.
            recovered = raw.decode(true)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue

        if recovered == mojibake:
            # No transformation happened — not useful.
            continue

        results.append((wrong, true, recovered))

    return results


def _looks_korean(text: str) -> bool:
    """Tiny heuristic: does the recovered text contain Hangul?"""
    return any("가" <= ch <= "힣" for ch in text)


def _looks_japanese(text: str) -> bool:
    """Tiny heuristic: does the recovered text contain Hiragana/Katakana/Kanji?"""
    return any(
        "぀" <= ch <= "ヿ"   # hiragana + katakana
        or "一" <= ch <= "鿿"  # CJK Unified Ideographs (also Chinese)
        for ch in text
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python mojibake_recover.py <mojibake-string>", file=sys.stderr)
        return 2

    mojibake = argv[1]
    candidates = recover(mojibake)

    if not candidates:
        print("no plausible recovery found", file=sys.stderr)
        return 1

    # Sort with the most likely-looking candidates first so a human eye
    # lands on the right answer immediately.
    candidates.sort(
        key=lambda c: (
            0 if _looks_korean(c[2]) or _looks_japanese(c[2]) else 1,
            len(c[2]),
        ),
    )

    for wrong, true, text in candidates:
        print(f"{wrong:<10} -> {true:<10} : {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
