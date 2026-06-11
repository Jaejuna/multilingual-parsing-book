"""
word_boundary_match.py

Pattern: glossary matcher that respects word boundaries for Latin-script
text but falls back to substring matching for CJK.

Why two strategies?
-------------------
* Latin / European text uses whitespace and punctuation to separate
  words. Matching ``"AI"`` against ``"Said"`` is wrong. ``\\b`` (the
  regex word boundary) cleanly handles this.
* CJK text has no spaces between words. ``\\b`` is anchored on the
  ``\\w`` character class, which only matches ASCII word characters by
  default — so a CJK term followed by punctuation produces a word
  boundary at the punctuation, but ``\\b`` inside CJK characters
  doesn't fire at all. Effectively ``\\b`` is silently disabled for
  CJK, and you get substring behaviour anyway. We just make that
  explicit here.

We decide per-TERM (not per-segment) which strategy to use, based on
whether the term itself contains any CJK characters.
"""

from __future__ import annotations

import re
from typing import Iterable

# CJK Unicode block ranges we care about for "is this term CJK?".
# Hiragana + Katakana + CJK Unified Ideographs + Hangul Syllables.
# This isn't exhaustive (no extension A/B, no Bopomofo) but covers the
# scripts you actually encounter in game localisation.
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def _is_cjk(term: str) -> bool:
    """True if the term contains any CJK character.

    A single CJK character is enough — mixed terms like ``"AI 디렉터"``
    are best handled with substring semantics, because the Korean part
    has no word boundary anyway.
    """
    return bool(_CJK_RE.search(term))


def word_boundary_match(
    text: str,
    terms: Iterable[tuple[str, str]],
    *,
    case_sensitive: bool = False,
    min_len: int = 2,
) -> list[tuple[str, str]]:
    """Match terms against ``text`` with per-term strategy selection.

    * CJK term  → substring match (case-insensitive irrelevant for CJK
      but applied to the rare ASCII portion if any).
    * Latin term → ``re.search(r"\\b{escaped}\\b")`` with optional
      ``re.IGNORECASE``.

    Returns a list of ``(source, target)`` in registration order.
    """

    haystack = text if case_sensitive else text.lower()
    flags = 0 if case_sensitive else re.IGNORECASE

    seen: set[str] = set()
    matches: list[tuple[str, str]] = []

    for src, tgt in terms:
        if not src or len(src) < min_len or src in seen:
            continue

        if _is_cjk(src):
            # CJK: substring is the right semantics. We use the
            # pre-cased haystack for symmetry with the Latin path.
            needle = src if case_sensitive else src.lower()
            hit = needle in haystack
        else:
            # Latin: anchor on \b. We compile per call here for
            # clarity; for large term lists, pre-compile and cache the
            # patterns by ``src``.
            pattern = re.compile(rf"\b{re.escape(src)}\b", flags)
            hit = pattern.search(text) is not None

        if hit:
            matches.append((src, tgt))
            seen.add(src)

    return matches


def longest_match_first(
    matches: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Optional post-filter: drop a match whose source is a substring of
    a longer match's source.

    Useful when both ``"AI"`` and ``"AI Director"`` are registered and
    you only want the longer hit in the prompt. Stable order is
    preserved among kept items.
    """

    # Sort by length desc just to make the "contained-by" check linear.
    by_len = sorted(matches, key=lambda m: -len(m[0]))
    kept: list[tuple[str, str]] = []
    kept_sources: list[str] = []
    for src, tgt in by_len:
        if any(src in longer for longer in kept_sources):
            continue
        kept.append((src, tgt))
        kept_sources.append(src)

    # Restore original registration order — callers usually don't want
    # us to reshuffle their term list silently.
    order = {s: i for i, (s, _) in enumerate(matches)}
    kept.sort(key=lambda m: order[m[0]])
    return kept
