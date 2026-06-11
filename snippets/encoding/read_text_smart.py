"""
read_text_smart.py

Pattern: encoding-aware text reader for server-side file handling.

Why this exists
---------------
Server pipelines often receive CSVs from many sources — modern apps emit
UTF-8, Excel on Korean Windows still writes cp949, and someone will
eventually send a file with a UTF-8 BOM that the standard ``utf-8`` codec
leaves inside the first column header (causing a silent DictReader miss).

This module gives you a single function that "does the right thing" for
the encodings actually seen in practice:

  * UTF-8 with BOM
  * UTF-8 without BOM
  * cp949 / EUC-KR (Korean Excel default)

If you want broader auto-detection (Shift_JIS, GB18030, big5, ...), pull
in ``charset-normalizer`` or ``chardet`` — the optional path is shown at
the bottom.
"""

from __future__ import annotations

from pathlib import Path


# UTF-8 BOM bytes. ``str.startswith`` won't work because the file is bytes
# at this point; we compare bytes directly so we don't accidentally try to
# decode garbage.
_UTF8_BOM = b"\xef\xbb\xbf"


def read_text_smart(path: str | Path) -> str:
    """Read a text file, detecting UTF-8 (with/without BOM) and cp949.

    The detection order matters:

      1. Strip a leading UTF-8 BOM. This is the most common cause of
         "first column key starts with U+FEFF" bugs in csv.DictReader.
      2. Try strict UTF-8. ``errors="strict"`` raises ``UnicodeDecodeError``
         the moment a non-UTF-8 byte appears, which we use as the signal
         to fall back.
      3. Fall back to cp949. Korean Excel "Save As CSV" emits this; it is
         a strict superset of EUC-KR.

    The function never raises on a well-formed file in one of these three
    encodings. If the file is something else (Shift_JIS, GBK, ...), the
    cp949 decode will produce mojibake — that's the expected boundary of
    this helper.
    """

    raw = Path(path).read_bytes()

    # 1) UTF-8 BOM — strip and decode the remainder as UTF-8.
    if raw.startswith(_UTF8_BOM):
        return raw[len(_UTF8_BOM):].decode("utf-8")

    # 2) Strict UTF-8 attempt.
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # 3) cp949 fallback (covers EUC-KR + the Windows extensions Excel uses).
    return raw.decode("cp949")


def read_text_smart_csv_safe(path: str | Path) -> str:
    """Same as :func:`read_text_smart` but ensures a no-BOM result.

    Use this when you're about to feed the text into ``csv.DictReader``
    and don't want to bother with ``encoding="utf-8-sig"`` somewhere
    downstream. The first column header will be free of the U+FEFF
    invisible character.
    """

    text = read_text_smart(path)
    # ``read_text_smart`` already strips the byte BOM, but if the input
    # was decoded via Path.read_text or another path that returned the
    # character form, strip it here too. Cheap, idempotent.
    return text.lstrip("﻿")


# ---------------------------------------------------------------------------
# Optional: broader auto-detection via charset-normalizer
# ---------------------------------------------------------------------------
# Uncomment if you handle Japanese / Chinese / European inputs too. The
# library is pure-Python, MIT-licensed, and produces better guesses than
# legacy ``chardet`` on short inputs.
#
# from charset_normalizer import from_bytes
#
# def read_text_charset_normalized(path: str | Path) -> str:
#     raw = Path(path).read_bytes()
#     if raw.startswith(_UTF8_BOM):
#         return raw[len(_UTF8_BOM):].decode("utf-8")
#     best = from_bytes(raw).best()
#     if best is None:
#         # No confident guess — fall back to UTF-8 with replacement so we
#         # never crash the pipeline on a truly unknown encoding.
#         return raw.decode("utf-8", errors="replace")
#     return str(best)
