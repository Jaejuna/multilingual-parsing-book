"""
mojibake_trace.py

Pattern: trace mojibake back to its source by trying every plausible
(true-encoding, wrong-decoding) pair and printing the candidates.

This is the interactive cousin of ``encoding/mojibake_recover.py``:
the former is meant to be imported as a function; this one is a CLI
for "I have a broken string on my screen, what happened?".

Usage
-----
    $ python mojibake_trace.py "ë³´íµ"
    [latin-1 was wrongly used to decode bytes that were utf-8]
      → 보통

    $ python mojibake_trace.py --all "ë³´íµ"
    (prints every pair that produces a non-trivial string)

Reading the output
------------------
Pick the candidate whose recovered text looks like a real word in your
target language. If multiple candidates look plausible, the ``--all``
mode helps you compare and choose.

The script also tries a few WIDER pairs (shift_jis, gb18030, big5) for
Japanese/Chinese content, in case the project isn't Korean-only.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from itertools import product


# Encodings to try as "what the consumer wrongly decoded the bytes as".
_WRONG = ["latin-1", "cp1252", "cp949", "utf-8", "shift_jis"]

# Encodings to try as "what the bytes really were".
_TRUE = ["utf-8", "cp949", "shift_jis", "gb18030", "big5"]


def _printable(text: str) -> str:
    """Replace control characters so the candidate list stays readable."""
    return "".join(
        ch if unicodedata.category(ch)[0] != "C" else "·" for ch in text
    )


def _is_meaningful(text: str) -> bool:
    """Filter heuristic for the default (non-``--all``) mode.

    A candidate is "meaningful" if more than half of its characters are
    in a CJK Unicode block — i.e. recovering Korean / Japanese /
    Chinese from European mojibake. Tweaked by eye for translation-eval
    workloads; relax for other domains.
    """
    if not text:
        return False

    def _cjk(ch: str) -> bool:
        return (
            "぀" <= ch <= "ヿ"  # hiragana + katakana
            or "㐀" <= ch <= "鿿"  # CJK Unified Ideographs
            or "가" <= ch <= "힯"  # Hangul Syllables
        )

    cjk_count = sum(1 for ch in text if _cjk(ch))
    return cjk_count >= max(1, len(text) // 2)


def trace(mojibake: str, *, show_all: bool) -> list[tuple[str, str, str]]:
    """Return the candidate list.

    Each element is ``(wrong, true, recovered)`` — meaning "if the
    consumer decoded the bytes as ``wrong``, and the bytes were really
    ``true``, the original text was ``recovered``".
    """

    out: list[tuple[str, str, str]] = []
    for wrong, true in product(_WRONG, _TRUE):
        if wrong == true:
            continue
        try:
            raw = mojibake.encode(wrong)
            recovered = raw.decode(true)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if recovered == mojibake:
            continue
        if not show_all and not _is_meaningful(recovered):
            continue
        out.append((wrong, true, recovered))

    # Sort so longest meaningful CJK strings come first. They tend to
    # be the right answer.
    out.sort(key=lambda c: (-sum(1 for _ in c[2]), c[0], c[1]))
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", help="The mojibake string to recover")
    parser.add_argument(
        "--all",
        action="store_true",
        dest="show_all",
        help="Print every candidate, not just the CJK-looking ones",
    )
    args = parser.parse_args(argv[1:])

    candidates = trace(args.text, show_all=args.show_all)
    if not candidates:
        print(
            "no plausible recovery found — the input may not be mojibake, "
            "or the source encoding isn't in the candidate list.",
            file=sys.stderr,
        )
        return 1

    for wrong, true, recovered in candidates:
        print(
            f"[{wrong} was wrongly used to decode bytes that were {true}]"
        )
        print(f"  → {_printable(recovered)}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
