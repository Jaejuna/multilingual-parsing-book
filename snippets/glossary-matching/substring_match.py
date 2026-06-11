"""
substring_match.py

Pattern: simplest possible glossary matcher — case-insensitive substring
``in`` check, one source term at a time.

When this is the right tool
---------------------------
* Most terms are CJK (Korean, Japanese, Chinese) — there are no word
  boundaries to worry about, so ``\\b`` regex doesn't help anyway.
* Term list is small (hundreds, not tens of thousands).
* False positives like "AI" matching inside "Said" are tolerable, OR
  you have a UI surface where reviewers can spot them.

When it's NOT the right tool
----------------------------
* You're matching in Latin-script text where word boundaries matter
  (``re`` with ``\\b`` is a better baseline — see word_boundary_match.py).
* You have tens of thousands of terms and per-segment latency matters.
  At that scale switch to an Aho-Corasick automaton — see
  aho_corasick_match.py.

Output shape
------------
Returns a list of ``(source_term, target_term)`` pairs preserving the
order the terms were registered. Duplicates are not emitted — even if a
term appears multiple times in the haystack, it shows up once in the
result.
"""

from __future__ import annotations

from typing import Iterable


def substring_match(
    text: str,
    terms: Iterable[tuple[str, str]],
    *,
    case_sensitive: bool = False,
    min_len: int = 2,
) -> list[tuple[str, str]]:
    """Find every glossary term that appears as a substring of ``text``.

    Parameters
    ----------
    text :
        The segment to search in. Pass the raw source text, not a
        translated one — this matcher answers "which terms WOULD apply"
        before/during translation.
    terms :
        Iterable of ``(source_term, target_term)`` tuples. Source terms
        are searched for, target terms are returned alongside so callers
        don't have to re-look-them-up.
    case_sensitive :
        Default False because Latin-script glossaries usually want
        ``server`` to hit ``Server``. CJK is not affected by casing.
    min_len :
        Single-character terms generate huge amounts of noise (one
        Hangul jamo lurking in random words). Two characters is the
        safe minimum for CJK; raise to 3 for Latin-only glossaries.

    Returns
    -------
    List of ``(source, target)`` in registration order, deduplicated.
    """

    # Pre-compute the haystack once. The per-term loop only touches the
    # already-cased version, which matters when ``text`` is long.
    haystack = text if case_sensitive else text.lower()

    seen: set[str] = set()
    matches: list[tuple[str, str]] = []

    for src, tgt in terms:
        if not src or len(src) < min_len:
            continue
        if src in seen:
            # ``terms`` may legitimately contain duplicates if it was
            # concatenated from multiple sources; skip them so the
            # output is clean.
            continue
        needle = src if case_sensitive else src.lower()
        if needle in haystack:
            matches.append((src, tgt))
            seen.add(src)

    return matches
