"""
normalize_lang.py

Pattern: turn a messy lang tag string into a canonical form before
storing or comparing.

When to use this vs. ``lang_aliases``
-------------------------------------
* ``lang_aliases`` keeps both the exact and base forms so region info
  (zh-CN vs zh-TW) is preserved.
* ``normalize_lang`` collapses to a SINGLE canonical string. Use this
  when you actually want a one-to-one mapping — e.g. as a database
  column you want to ``GROUP BY``, or to dedupe a list of detected
  languages.

The canonical form chosen here matches BCP-47 style:

  * hyphen, not underscore
  * lowercase language subtag
  * UPPERCASE region subtag (if present)
  * Title-case script subtag (if present, e.g. ``zh-Hans``)
"""

from __future__ import annotations


def normalize_lang(lang: str) -> str:
    """Canonicalise a lang tag.

    Examples
    --------
    >>> normalize_lang("ko_kr")
    'ko-KR'
    >>> normalize_lang("EN-us")
    'en-US'
    >>> normalize_lang("zh-hans-cn")
    'zh-Hans-CN'
    >>> normalize_lang("ko")
    'ko'
    >>> normalize_lang(" en ")          # surrounding whitespace stripped
    'en'
    >>> normalize_lang("")
    ''
    """

    if not lang:
        return ""

    parts = lang.strip().replace("_", "-").split("-")
    out: list[str] = []
    for i, p in enumerate(parts):
        if i == 0:
            # Language subtag: always lowercase.
            out.append(p.lower())
        elif len(p) == 4 and p.isalpha():
            # Script subtag (4 letters): Title case — e.g. "Hans", "Latn".
            out.append(p.title())
        elif len(p) == 2 and p.isalpha():
            # Region subtag (2 letters): UPPERCASE — "KR", "US".
            out.append(p.upper())
        elif len(p) == 3 and p.isdigit():
            # Numeric region subtag (UN M.49 code): leave as digits.
            out.append(p)
        else:
            # Variant or unknown — keep as-is (lowercased to be safe).
            out.append(p.lower())
    return "-".join(out)


def base_lang(lang: str) -> str:
    """Return just the language subtag — e.g. ``zh-Hans-CN`` → ``zh``.

    Useful when you need a primary-key-style lookup and don't care
    about region or script. Pair with ``normalize_lang`` if you want
    the result independent of input casing/separator.
    """

    if not lang:
        return ""
    return normalize_lang(lang).split("-", 1)[0]
