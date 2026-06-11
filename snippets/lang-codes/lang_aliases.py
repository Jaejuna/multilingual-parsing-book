"""
lang_aliases.py

Pattern: bridge "locale" lang codes (``ko-KR``) with "base" lang codes
(``ko``) so a lookup keyed on one form still hits when the data is
keyed on the other.

The problem
-----------
Two systems in the same pipeline disagree on tag form:

  * Glossary CSV header: ``ko-KR,en-US,fr-FR,ja-JP,...``   (BCP-47 locale)
  * Job snapshot:        ``source_lang="ko", target_lang="ja"``  (ISO 639-1 base)

A glossary lookup keyed on the exact string returns 0 matches, even
though the data is there. The fix is to register / look up under BOTH
forms.

Why not "just normalise to base"?
---------------------------------
Because Chinese needs region distinction: ``zh-CN`` and ``zh-TW`` are
different writing systems. Collapsing both to ``zh`` would silently
overwrite one with the other. We keep the exact form AND add the base
as a fallback alias instead.

Trade-off: if the SAME CSV defines ``zh-CN`` and ``zh-TW`` and a job
asks for plain ``zh``, the base-alias lookup is ambiguous — whichever
was registered last wins. Document this and prefer full locale codes in
job snapshots when you can.
"""

from __future__ import annotations


def lang_aliases(lang: str) -> list[str]:
    """Expand a language tag to ``[exact, base]`` (or just ``[exact]``).

    Examples
    --------
    >>> lang_aliases("ko-KR")
    ['ko-KR', 'ko']
    >>> lang_aliases("ko_KR")      # underscore variant normalised to hyphen
    ['ko-KR', 'ko']
    >>> lang_aliases("ko")         # already base — no extra alias
    ['ko']
    >>> lang_aliases("")           # defensive — empty stays empty
    ['']
    """

    # Normalise the underscore form Android/Java resources sometimes use
    # so ``ko_KR`` and ``ko-KR`` behave identically.
    norm = lang.replace("_", "-")

    # ``split("-", 1)`` keeps everything after the first hyphen attached
    # to the second element — we only care about the first piece.
    base = norm.split("-", 1)[0]

    if base and base != norm:
        return [norm, base]
    return [norm]


def register_with_aliases(
    store: dict[str, dict],
    lang: str,
    value: dict,
) -> None:
    """Insert ``value`` under every alias of ``lang``.

    Used at "prepare time" — when we know the lang tag from the source
    data (e.g. CSV header) and want any later lookup form to find it.

    Note: this MUTATES ``store``. The last write wins if two locales
    share the same base (``zh-CN`` then ``zh-TW`` both write to ``zh``).
    For glossary use that is acceptable as a fallback; for anything
    where region matters, only use the exact key.
    """

    for key in lang_aliases(lang):
        store[key] = value


def lookup_with_aliases(
    store: dict[str, dict],
    lang: str,
) -> dict | None:
    """Look up ``lang`` in ``store``, trying exact form first then base.

    Returns ``None`` if neither alias is present, so callers can do an
    explicit "not configured for this language" branch.
    """

    for key in lang_aliases(lang):
        if key in store:
            return store[key]
    return None
