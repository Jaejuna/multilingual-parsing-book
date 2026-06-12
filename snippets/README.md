# Snippets

Pattern-organized, runnable code for the situations described in
[`../README.md`](../README.md). Each file is self-contained — you can
drop a single snippet into another project without grabbing the rest.

## Layout

```
snippets/
├── encoding/                   # bytes ↔ string conversions
│   ├── read-text-smart.browser.ts   # UTF-8 / BOM / cp949 auto-detect (browser)
│   ├── read_text_smart.py           # same, server-side Python
│   ├── csv-export-with-bom.ts       # emit Excel-compatible UTF-8 CSV
│   └── mojibake_recover.py          # importable mojibake → original recovery
├── lang-codes/                 # locale tag normalization & lookup
│   ├── lang_aliases.py              # ko-KR ↔ ko alias bridging (Python)
│   ├── lang-aliases.ts              # same (TypeScript)
│   └── normalize_lang.py            # BCP-47 canonical form
├── locale-content/             # per-locale content file resolution
│   └── resolve-locale-content.ts    # list base + partial frontmatter fallback
├── glossary-matching/          # term lookup strategies
│   ├── substring_match.py           # simplest "term in text" loop
│   ├── word_boundary_match.py       # \b for Latin, substring for CJK
│   └── aho_corasick_match.py        # bulk matching for large term sets
├── download/                   # HTTP response patterns
│   ├── content-disposition-rfc5987.ts   # filename* for non-ASCII names
│   └── streaming-csv-export.ts          # ReadableStream-based CSV
└── debug/                      # one-shot diagnostic tools
    ├── inspect-file-encoding.ps1    # PowerShell encoding inspector
    ├── inspect-file-encoding.sh     # bash equivalent
    └── mojibake_trace.py            # CLI: "I see mojibake, what was it?"
```

## How to read these

* **Every file starts with a docstring** explaining *why* the pattern
  exists, *when* to reach for it, and *when not to*. Read that first —
  the code itself is usually the boring part.
* **Comments are in English** so the snippets are portable across teams.
* **No hidden dependencies** unless explicitly noted in the docstring
  (e.g. `aho_corasick_match.py` mentions the optional
  `pyahocorasick` C-extension and ships a pure-Python fallback).

## Quick reference

| Symptom in production | Snippet to copy |
|-----------------------|-----------------|
| Korean CSV uploaded via browser becomes mojibake on S3 | [`encoding/read-text-smart.browser.ts`](./encoding/read-text-smart.browser.ts) |
| Same issue but on a Python backend reading a local file | [`encoding/read_text_smart.py`](./encoding/read_text_smart.py) |
| Exported CSV opens broken in Excel on Korean Windows | [`encoding/csv-export-with-bom.ts`](./encoding/csv-export-with-bom.ts) |
| You see `ë³´íµ`-style text and want to know its origin | [`debug/mojibake_trace.py`](./debug/mojibake_trace.py) |
| Glossary CSV uses `ko-KR` but Job snapshot uses `ko` (0 matches) | [`lang-codes/lang_aliases.py`](./lang-codes/lang_aliases.py) |
| Multiple lang tag spellings polluting your DB | [`lang-codes/normalize_lang.py`](./lang-codes/normalize_lang.py) |
| `*.en.mdx` shows up as a duplicate post / EN post loses its thumbnail | [`locale-content/resolve-locale-content.ts`](./locale-content/resolve-locale-content.ts) |
| Glossary matcher catches "AI" inside "Said" in English text | [`glossary-matching/word_boundary_match.py`](./glossary-matching/word_boundary_match.py) |
| Term loop is too slow with thousands of terms | [`glossary-matching/aho_corasick_match.py`](./glossary-matching/aho_corasick_match.py) |
| Hangul filename garbled in Firefox/Safari downloads | [`download/content-disposition-rfc5987.ts`](./download/content-disposition-rfc5987.ts) |
| Large CSV export times out on the platform | [`download/streaming-csv-export.ts`](./download/streaming-csv-export.ts) |
| "Why is this file broken?" — quick triage | [`debug/inspect-file-encoding.ps1`](./debug/inspect-file-encoding.ps1) / [`.sh`](./debug/inspect-file-encoding.sh) |

## Conventions

* **TypeScript files** use modern ES module syntax. Drop into a Next.js
  / Vite / Node 18+ project without transpilation tweaks.
* **Python files** target 3.10+ (uses `|` union syntax in type hints).
  Backport to 3.8 by replacing `str | None` with `Optional[str]` and
  the new `list[str]` with `List[str]`.
* **Shell scripts** use `#!/usr/bin/env bash` and `set -euo pipefail`.
  PowerShell variants use comment-based help so `Get-Help` works.
* **No silent failures.** Where a pattern degrades (e.g. encoding
  fallback), the docstring spells out the boundary.
