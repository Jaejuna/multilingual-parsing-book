"""
aho_corasick_match.py

Pattern: bulk glossary matching using an Aho-Corasick automaton when the
naive ``term in text`` loop becomes too slow.

When you need this
------------------
The simple loop in substring_match.py is O(terms × segment_length) per
segment. For a glossary with ~500 terms and ~10k segments, that's
roughly fine. Once you cross ~5k terms × ~50k segments the loop starts
to dominate worker runtime. Aho-Corasick handles all terms in a single
pass — O(segment_length + matches).

We provide two implementations:

  1. ``pyahocorasick``  — fastest, C extension. ``pip install pyahocorasick``.
  2. A pure-Python fallback — no dependency, slower but still better
     than naive substring for large term lists.

If neither is acceptable, stick with substring_match.py.

This module does NOT try to be word-boundary-aware. If you need that
in Latin scripts, post-filter the matches by re-checking each hit's
surroundings, or build a separate automaton from "\\b<term>\\b"-shaped
boundary tokens.
"""

from __future__ import annotations

from typing import Iterable, Iterator


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_matcher(
    terms: Iterable[tuple[str, str]],
    *,
    case_sensitive: bool = False,
    min_len: int = 2,
) -> "Matcher":
    """Construct a matcher from ``(source, target)`` pairs.

    Building the automaton is expensive — do it ONCE at startup (or at
    the beginning of a Job in a worker pipeline), then call
    ``matcher.find(text)`` for every segment.
    """

    # Filter + lower-case the keys up front so the automaton sees clean
    # input. The original ``src``/``tgt`` are kept as the payload so the
    # caller's original casing is preserved in the output.
    pairs: list[tuple[str, tuple[str, str]]] = []
    seen: set[str] = set()
    for src, tgt in terms:
        if not src or len(src) < min_len or src in seen:
            continue
        seen.add(src)
        key = src if case_sensitive else src.lower()
        pairs.append((key, (src, tgt)))

    try:
        import ahocorasick  # type: ignore[import-not-found]
    except ImportError:
        return _PurePythonMatcher(pairs, case_sensitive=case_sensitive)
    return _AhocorasickMatcher(pairs, case_sensitive=case_sensitive)


class Matcher:
    """Interface for both backend implementations."""

    def find(self, text: str) -> list[tuple[str, str]]:
        """Return matches in registration order, deduplicated by source."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Fast backend: pyahocorasick (C extension)
# ---------------------------------------------------------------------------


class _AhocorasickMatcher(Matcher):
    def __init__(
        self,
        pairs: list[tuple[str, tuple[str, str]]],
        *,
        case_sensitive: bool,
    ) -> None:
        import ahocorasick  # type: ignore[import-not-found]

        self._case_sensitive = case_sensitive
        self._automaton = ahocorasick.Automaton()
        for key, payload in pairs:
            self._automaton.add_word(key, payload)
        # ``make_automaton`` finalises the failure links — REQUIRED before
        # ``iter`` will produce hits. Forgetting this is the #1 footgun.
        self._automaton.make_automaton()

    def find(self, text: str) -> list[tuple[str, str]]:
        haystack = text if self._case_sensitive else text.lower()
        seen: set[str] = set()
        out: list[tuple[str, str]] = []
        # ``iter`` yields (end_index, payload). We don't need positions
        # for the glossary use case, just the payloads.
        for _end, (src, tgt) in self._automaton.iter(haystack):
            if src in seen:
                continue
            seen.add(src)
            out.append((src, tgt))
        return out


# ---------------------------------------------------------------------------
# Slow backend: pure-Python trie walk
# ---------------------------------------------------------------------------


class _PurePythonMatcher(Matcher):
    """A minimal Aho-Corasick automaton in pure Python.

    Not as fast as the C extension, but still asymptotically O(N) in
    segment length per call. Useful when you can't add dependencies.

    Implementation notes:
      * ``goto`` is a list-of-dicts mapping (state, char) -> next state.
      * ``fail`` is the failure function (state -> fallback state).
      * ``output`` collects payloads that terminate at each state.
    """

    def __init__(
        self,
        pairs: list[tuple[str, tuple[str, str]]],
        *,
        case_sensitive: bool,
    ) -> None:
        self._case_sensitive = case_sensitive
        self._goto: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._output: list[list[tuple[str, str]]] = [[]]
        for key, payload in pairs:
            self._add(key, payload)
        self._build_failure_links()

    # ---- trie construction --------------------------------------------------

    def _add(self, key: str, payload: tuple[str, str]) -> None:
        state = 0
        for ch in key:
            nxt = self._goto[state].get(ch)
            if nxt is None:
                nxt = len(self._goto)
                self._goto.append({})
                self._fail.append(0)
                self._output.append([])
                self._goto[state][ch] = nxt
            state = nxt
        self._output[state].append(payload)

    def _build_failure_links(self) -> None:
        # BFS over the trie to fill ``fail`` and propagate outputs.
        from collections import deque

        queue: deque[int] = deque()
        for ch, s in self._goto[0].items():
            self._fail[s] = 0
            queue.append(s)

        while queue:
            r = queue.popleft()
            for ch, u in self._goto[r].items():
                queue.append(u)
                # Walk failure links until we find a state that has a
                # transition on ``ch``, or hit root.
                state = self._fail[r]
                while state != 0 and ch not in self._goto[state]:
                    state = self._fail[state]
                self._fail[u] = self._goto[state].get(ch, 0)
                if self._fail[u] == u:
                    # Edge case: avoid self-loop for single-char terms
                    # ending at root.
                    self._fail[u] = 0
                # Propagate the suffix link's outputs so we don't miss
                # nested matches like "AI" inside "AI Director".
                self._output[u].extend(self._output[self._fail[u]])

    # ---- search -------------------------------------------------------------

    def find(self, text: str) -> list[tuple[str, str]]:
        haystack = text if self._case_sensitive else text.lower()
        state = 0
        seen: set[str] = set()
        out: list[tuple[str, str]] = []
        for ch in haystack:
            while state != 0 and ch not in self._goto[state]:
                state = self._fail[state]
            state = self._goto[state].get(ch, 0)
            if self._output[state]:
                for src, tgt in self._output[state]:
                    if src in seen:
                        continue
                    seen.add(src)
                    out.append((src, tgt))
        return out


# ---------------------------------------------------------------------------
# Convenience: one-shot helper
# ---------------------------------------------------------------------------


def find_terms(
    text: str,
    terms: Iterable[tuple[str, str]],
    *,
    case_sensitive: bool = False,
    min_len: int = 2,
) -> list[tuple[str, str]]:
    """Build a matcher and search once. Use ONLY for tests or one-off
    scripts — building the automaton per call defeats the entire point
    of using Aho-Corasick in production.
    """

    matcher = build_matcher(
        terms, case_sensitive=case_sensitive, min_len=min_len
    )
    return matcher.find(text)


def iter_terms(
    texts: Iterable[str],
    terms: Iterable[tuple[str, str]],
    *,
    case_sensitive: bool = False,
    min_len: int = 2,
) -> Iterator[tuple[int, list[tuple[str, str]]]]:
    """Stream matches for many segments using a single shared automaton.

    Yields ``(index, matches)`` pairs in input order. The shared
    automaton is what makes this fast — don't rebuild per segment.
    """

    matcher = build_matcher(
        terms, case_sensitive=case_sensitive, min_len=min_len
    )
    for i, text in enumerate(texts):
        yield i, matcher.find(text)
