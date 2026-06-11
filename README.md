# multilingual-parsing-book(i18n) book

A field-tested collection of gotchas and patterns from running a game
localization evaluation platform — every problem documented here was
shipped as a bug, hit in production, and fixed. The intent is portable
knowledge: each section is self-contained so you can read just the
chapter that matches the bug you're chasing.

> Korean version: [README.ko.md](./README.ko.md).
> Runnable code: [`snippets/`](./snippets/) — one pattern per file, English comments.

## Table of contents

0. [System at a glance — where things break](#0-system-at-a-glance--where-things-break)
1. [Encoding — the Korean-Excel CSV trap](#1-encoding--the-korean-excel-csv-trap)
2. [Language codes — locale vs base mismatch](#2-language-codes--locale-vs-base-mismatch)
3. [Glossary matching — the substring pitfall](#3-glossary-matching--the-substring-pitfall)
4. [File downloads — RFC 5987 Content-Disposition](#4-file-downloads--rfc-5987-content-disposition)
5. [Two ORMs, one Postgres — boundary discipline](#5-two-orms-one-postgres--boundary-discipline)
6. [Pre-work checklist](#6-pre-work-checklist)
7. [Snippet index](#7-snippet-index)
8. [Debugging commands](#8-debugging-commands)
9. [References](#9-references)

---

## 0. System at a glance — where things break

```
Browser (Next.js / TS)
  ├─ File upload (CSV / XLSX / TMX)         ← encoding mojibake (#1)
  ├─ Header mapping + rewriteCSV             ← lang-code form mismatch (#2)
  └─ S3 PUT (presigned URL)
        ↓
S3 (raw file storage)
        ↓
FastAPI worker (Python)
  ├─ S3 download → cache (utf-8-sig preferred)
  ├─ Augmenters (Glossary / Retrieval / Graph)
  │     - substring matching                 ← #3 partial-match & boundaries
  │     - lang-code lookup                   ← #2 again
  ├─ Prompt assembly ({{GLOSSARY}} etc.)
  └─ Engine call (Vertex / Cortex / ...)
        ↓
JobRow.augmenter_log  (FastAPI-owned table)
        ↓
Publish (Next API route, Prisma transaction)
        ↓
TextSegment / Translation / GlossaryMatch  (Prisma-owned tables)
        ↓
Evaluation UI (EvaluationCard)
```

**Four boundaries cause virtually every i18n bug:**

1. Browser byte→string decoding
2. S3 ↔ worker encoding
3. Lang-code normalization
4. FastAPI ↔ Prisma DB ownership

Almost every "Korean is garbled" / "glossary not applying" report
traces back to one of these four.

---

## 1. Encoding — the Korean-Excel CSV trap

### 1.1 `File.text()` always decodes as UTF-8

The Web API `Blob.text()` (and therefore `File.text()`) takes **no
arguments**. Decoding is always UTF-8. That's fine for files produced
by modern tooling, but Excel on Korean Windows still saves CSV as
**cp949** (a.k.a. EUC-KR / windows-949) by default. Force-decoding
those bytes as UTF-8 produces mojibake — silently.

```ts
// ❌ cp949 bytes force-decoded as UTF-8 → mojibake → uploaded to S3 as-is
file.text().then((txt) => {
  const rewritten = rewriteCSV(txt, ...);
  const blob = new Blob([rewritten], { type: "text/csv" });
  // S3 now stores broken bytes forever
});

// ✅ Read raw bytes, then pick a decoder based on what's actually there
async function readTextSmart(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  const bytes = new Uint8Array(buf);
  // BOM
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    return new TextDecoder("utf-8").decode(bytes.slice(3));
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return new TextDecoder("euc-kr").decode(bytes);  // cp949 ≈ windows-949 ≈ euc-kr
  }
}
```

`TextDecoder` supports `utf-8`, `utf-16`, `euc-kr`, `shift_jis`, `big5`,
`gb18030`, `windows-1252`, and others as WHATWG-standard labels. No
library needed.

→ Runnable: [`snippets/encoding/read-text-smart.browser.ts`](./snippets/encoding/read-text-smart.browser.ts).

### 1.2 `new Blob([string])` always produces UTF-8 bytes

Once the JS string is correct (cp949 properly decoded above), `new
Blob([str])` re-serializes it as UTF-8 unconditionally (MDN-guaranteed).
So the rule is simple: **as long as the decode step is right, the
upload is always valid UTF-8.**

### 1.3 BOM is for viewer compatibility, not correctness

A file can be valid UTF-8 in S3 and still open broken in Excel. Why:

| Viewer | UTF-8 without BOM |
|--------|-------------------|
| VSCode | usually auto-detects; occasionally guesses cp1252 |
| Notepad (Windows 11) | mostly OK |
| **Excel (Korean Windows)** | **always reads as the system code page (cp949) → mojibake** |

Fix: prepend a UTF-8 BOM (`﻿`) at export time.

```ts
const withBom = "﻿" + rewritten;
const blob = new Blob([withBom], { type: "text/csv;charset=utf-8" });
```

Server-side parsers handle BOM transparently (Python's `utf-8-sig`,
JS `TextDecoder('utf-8')`), so this is a one-way improvement.

→ Runnable: [`snippets/encoding/csv-export-with-bom.ts`](./snippets/encoding/csv-export-with-bom.ts).

### 1.4 Mojibake diagnostic table

| What you see | True encoding | Wrongly decoded as |
|--------------|---------------|---------------------|
| `ë³´íµ`, `ì¬ì©` | UTF-8 | Latin-1 / cp1252 |
| `?쒕쾲`, `?낅뜲?댄듃` | UTF-8 | cp949 |
| Jamo split (`ㅂㅗㅌㅗㅇ`) | NFD | consumer expects NFC |
| Stray ``/`?` at start | UTF-8 + BOM | UTF-8 (BOM not stripped) |

Reproduce in Python:

```python
bytes.fromhex("EBB3B4ED86B5").decode("utf-8")    # → "보통"
bytes.fromhex("EBB3B4ED86B5").decode("latin-1")  # → "ë³´íµ"  (mojibake)
```

→ CLI: [`snippets/debug/mojibake_trace.py`](./snippets/debug/mojibake_trace.py).

### 1.5 Use `utf-8-sig` on Python servers

```python
with path.open(encoding="utf-8-sig") as fh:  # auto-strips BOM
    reader = csv.DictReader(fh)
```

Plain `utf-8` leaves the BOM inside the first column name (`'﻿ko-KR'`),
which silently breaks `DictReader` key lookups.

→ Runnable: [`snippets/encoding/read_text_smart.py`](./snippets/encoding/read_text_smart.py).

---

## 2. Language codes — locale vs base mismatch

### 2.1 The reality

Localization tooling, translation engines, and evaluation systems each
use different lang-code spellings, and they mix freely in the same
codebase.

| Form | Examples | Where you see it |
|------|----------|-------------------|
| ISO 639-1 (base) | `ko`, `en`, `ja`, `zh` | API short codes |
| BCP 47 (locale) | `ko-KR`, `en-US`, `zh-CN`, `zh-TW`, `pt-BR` | CSV headers |
| Underscore variant | `ko_KR`, `pt_BR` | Java/Android resources |
| Uppercase variant | `EN`, `JA` | legacy systems |

Within one system: **the CSV uses `ko-KR`, the Job snapshot uses `ko`**.

### 2.2 The typical failure

A glossary augmenter returns zero matches, reported as "glossary not
applying." Root cause is a key lookup miss:

```python
# CSV load — header is "ko-KR", so the key is "ko-KR"
self._terms["ko-KR"] = {...}

# Lookup — Job context carries the short "ko"
src_dict = self._terms.get(ctx.source_lang, {})  # get("ko") → {}
```

Unit tests had used `ko` on both sides and passed; real data exposed
the gap.

### 2.3 Strategy: register both forms (alias)

Uniformly normalizing to the prefix loses information (`zh-CN` vs
`zh-TW`). So we register **both** the exact form and the base prefix.

```python
def lang_aliases(lang: str) -> list[str]:
    """'ko-KR' → ['ko-KR', 'ko']; 'ko' → ['ko']"""
    norm = lang.replace("_", "-")
    base = norm.split("-", 1)[0]
    return [norm, base] if base and base != norm else [norm]

# At register time
for key in lang_aliases("ko-KR"):
    self._terms.setdefault(key, {})

# At lookup time — exact first, base as fallback
for cand in lang_aliases(ctx.source_lang):
    if cand in self._terms:
        src_dict = self._terms[cand]
        break
```

**Trade-off:** if `zh-CN` and `zh-TW` coexist and the context asks
just for `zh`, the base alias is ambiguous — last write wins.
Document this; prefer full locale codes in job snapshots when possible.

→ Runnable: [`snippets/lang-codes/lang_aliases.py`](./snippets/lang-codes/lang_aliases.py)
  and [`.ts`](./snippets/lang-codes/lang-aliases.ts) twin.

### 2.4 Recommended test matrix

```
csv "ko-KR"   × ctx "ko"     → must match
csv "ko"      × ctx "ko-KR"  → must match
csv "ko-KR"   × ctx "ko-KR"  → must match (regression)
csv "ko"      × ctx "ko"     → must match (regression)
csv "zh-CN" + "zh-TW" × ctx "zh-CN" → exactly CN data
csv "zh-CN" + "zh-TW" × ctx "zh"    → either (last write wins, document!)
```

---

## 3. Glossary matching — the substring pitfall

### 3.1 Current behavior

```python
haystack = text if case_sensitive else text.lower()
for src, lang_map in src_dict.items():
    needle = src if case_sensitive else src.lower()
    if needle in haystack:        # plain substring
        matched.append({"source": src, "target": lang_map[target]})
```

**Pros:** fast, simple, works for CJK (where word boundaries don't
exist anyway). **Cons:** in English, `"AI"` matches inside `"Said"` and
`"again"`. Both `"AI"` and `"AI Director"` fire on the same haystack.

### 3.2 Improvement options (not all applied)

1. **Per-language strategy:** `\b{term}\b` for Latin scripts,
   substring for CJK.
2. **`min_len` guard:** drop single-character terms — they generate
   pure noise.
3. **Longest-match-first:** if `"AI Director"` matched, suppress the
   `"AI"` substring hit.
4. **Normalization:** collapse `<br>`, newlines, multi-spaces into
   single spaces before searching.
5. **Aho-Corasick:** for thousands of terms × thousands of segments,
   single-pass O(N+M) automaton.

→ Runnable: [`snippets/glossary-matching/substring_match.py`](./snippets/glossary-matching/substring_match.py),
  [`word_boundary_match.py`](./snippets/glossary-matching/word_boundary_match.py),
  [`aho_corasick_match.py`](./snippets/glossary-matching/aho_corasick_match.py).

### 3.3 Real bug: `case_sensitive` option silently dropped

`GlossaryAugmenter.config_schema` declares a `case_sensitive` option,
but `resource_resolver._spec_for_glossary` only forwards `file_ref` —
the option is set in the UI/Resource layer, then thrown away before
the worker sees it. The augmenter always runs case-insensitive. To fix,
read it from `ResourceVersion.meta` and include it in the spec.

---

## 4. File downloads — RFC 5987 Content-Disposition

### 4.1 The problem

Naive Korean filename in a download header:

```
Content-Disposition: attachment; filename="용어집.csv"
```

| Browser | Result |
|---------|--------|
| Chrome | usually OK (proprietary UTF-8 sniffing) |
| Firefox | mojibake filename (treats value as Latin-1) |
| Safari | may refuse download or save with garbled name |

### 4.2 Fix: dual `filename` + `filename*`

RFC 5987 defines a way to spell out the encoding explicitly:

```ts
function contentDisposition(name: string): string {
  const fallback = name.replace(/[^\x20-\x7e]/g, "_");
  const encoded = encodeURIComponent(name).replace(/['()*]/g, (c) =>
    "%" + c.charCodeAt(0).toString(16).toUpperCase(),
  );
  return `attachment; filename="${fallback}"; filename*=UTF-8''${encoded}`;
}
```

- `filename="..."` — ASCII-safe fallback for old clients.
- `filename*=UTF-8''<percent-encoded>` — modern clients prefer this.

→ Runnable: [`snippets/download/content-disposition-rfc5987.ts`](./snippets/download/content-disposition-rfc5987.ts).

### 4.3 Add a BOM when the destination is Excel

Exported CSV/XLSX is almost always opened in Excel. Prepend a BOM so
Korean-Windows Excel reads the file as UTF-8 instead of cp949:

```ts
const csv = "﻿" + buildCsv(rows);
return new Response(csv, {
  headers: {
    "Content-Type": "text/csv; charset=utf-8",
    "Content-Disposition": contentDisposition(`${name}.csv`),
  },
});
```

For large exports, stream instead of building the body in memory.

→ Runnable: [`snippets/download/streaming-csv-export.ts`](./snippets/download/streaming-csv-export.ts).

---

## 5. Two ORMs, one Postgres — boundary discipline

### 5.1 Structure

The same Postgres instance hosts tables owned by two stacks:

```
Postgres
├── FastAPI side (SQLAlchemy, raw migration.sql)
│   ├── translation_jobs
│   ├── job_rows
│   ├── job_events
│   └── resources / resource_versions
└── Next side (Prisma, prisma migrate)
    ├── text_segments
    ├── translations
    ├── evaluations
    ├── glossary_matches  ← normalization for evaluation surface
    └── ...
```

### 5.2 Principles

- **Migration ownership is exclusive.** A given table is migrated by
  one stack only; the other accesses it read-only via raw SQL.
- **Publish is a boundary.** Job results (FastAPI) → evaluation pool
  (Prisma) is handled by a **Next API route in a Prisma transaction**.
  FastAPI exposes data but does not INSERT into Prisma-owned tables.
- **Quoting matters.** When SQLAlchemy reads Prisma's camelCase
  columns (`"sourceText"`, `"externalId"`) via raw SQL, double-quote
  them — Postgres folds unquoted identifiers to lowercase.

### 5.3 Normalize vs JSON column — decision rule

> Normalize when the result is **small + naturally decomposable + has
> analytic value**. Otherwise keep a reference or don't store it.

Applied to augmenter contributions:

| Resource | Unit | Storage choice |
|----------|------|----------------|
| Glossary | `{source, target}` small pair × N | normalized (`glossary_matches`) |
| Retrieval (TM) | long sentence pair × top_k | not stored, or S3 ref |
| Graph | large text blob | not stored, or S3 ref |
| Style guide | identical for every row | reference Resource version only |

Normalized tables earn their keep through FK indexes and group-by
analytics. JSON blobs are for retention, not querying. Don't apply
the same shape everywhere — large payloads will inflate the DB
disproportionately.

---

## 6. Pre-work checklist

### 6.1 Before ingesting multilingual text

- [ ] Where is the decode step (browser? server? both)?
- [ ] What's the assumed encoding? Are non-UTF-8 inputs possible?
- [ ] BOM handling?
- [ ] NFC/NFD normalization required? (macOS filenames, Hangul jamo)

### 6.2 When handling lang codes

- [ ] Do base (`ko`) and locale (`ko-KR`) forms appear in the same
      code path?
- [ ] Is there an alias / normalization policy?
- [ ] Do you need to preserve region (`zh-CN` vs `zh-TW`)?
- [ ] Casing? Hyphen vs underscore?

### 6.3 When writing a term matcher

- [ ] CJK vs Latin word-boundary semantics?
- [ ] `min_len` guard against single-character noise?
- [ ] Longest-match-first ordering?
- [ ] Pre-normalization (whitespace, HTML tags)?
- [ ] Performance: at terms × segments > 1M, reconsider data
      structure.

### 6.4 When implementing upload/download

- [ ] Upload: read raw bytes and detect encoding (NEVER `File.text()`
      on user-controlled CSV).
- [ ] Download: include `filename*=UTF-8''...` in
      `Content-Disposition`.
- [ ] BOM the export if Excel is the likely consumer.
- [ ] Match Content-Type on presigned PUT requests.

### 6.5 When crossing two DB boundaries

- [ ] Is migration ownership explicit?
- [ ] When does cached state become stale?
- [ ] Is publish/sync idempotent (safe to re-run)?
- [ ] If a transaction straddles both stacks, how is partial failure
      handled?

### 6.6 When storing evaluation / debug data

- [ ] Does this benefit from normalization (indexing, analytics)?
- [ ] How big is the per-row payload? (KB per row → reconsider)
- [ ] What's the retention policy?
- [ ] Any PII?

---

## 7. Snippet index

Every pattern above has a runnable equivalent in
[`snippets/`](./snippets/). Pick a single file and drop it into another
project — they're self-contained.

| Symptom | Snippet |
|---------|---------|
| Korean CSV uploaded via browser → mojibake on S3 | [`encoding/read-text-smart.browser.ts`](./snippets/encoding/read-text-smart.browser.ts) |
| Same, Python backend reading a local file | [`encoding/read_text_smart.py`](./snippets/encoding/read_text_smart.py) |
| Exported CSV opens broken in Excel | [`encoding/csv-export-with-bom.ts`](./snippets/encoding/csv-export-with-bom.ts) |
| Mojibake on screen, want to know its origin | [`debug/mojibake_trace.py`](./snippets/debug/mojibake_trace.py) |
| Glossary CSV uses `ko-KR`, Job uses `ko` (0 matches) | [`lang-codes/lang_aliases.py`](./snippets/lang-codes/lang_aliases.py) |
| Multiple lang-tag spellings polluting the DB | [`lang-codes/normalize_lang.py`](./snippets/lang-codes/normalize_lang.py) |
| Matcher catches "AI" inside "Said" | [`glossary-matching/word_boundary_match.py`](./snippets/glossary-matching/word_boundary_match.py) |
| Term loop too slow with thousands of terms | [`glossary-matching/aho_corasick_match.py`](./snippets/glossary-matching/aho_corasick_match.py) |
| Hangul filename garbled in Firefox/Safari downloads | [`download/content-disposition-rfc5987.ts`](./snippets/download/content-disposition-rfc5987.ts) |
| Large CSV export times out | [`download/streaming-csv-export.ts`](./snippets/download/streaming-csv-export.ts) |
| Quick triage: "what encoding is this file?" | [`debug/inspect-file-encoding.ps1`](./snippets/debug/inspect-file-encoding.ps1) / [`.sh`](./snippets/debug/inspect-file-encoding.sh) |

---

## 8. Debugging commands

### Suspect a file encoding

```powershell
# PowerShell — first bytes
$bytes = [System.IO.File]::ReadAllBytes("path.csv")
$bytes[0..20] | ForEach-Object { "{0:X2}" -f $_ }
# 0xEF 0xBB 0xBF              → UTF-8 BOM
# 0xE0~ 3-byte sequences      → UTF-8 Korean
# 0x80~0xFE 2-byte sequences  → cp949 Korean
```

```bash
# Unix
file path.csv                              # libmagic guess
hexdump -C path.csv | head -2              # first bytes
iconv -f cp949 -t utf-8 in.csv > out.csv   # convert
```

→ Pre-built scripts: [`snippets/debug/inspect-file-encoding.ps1`](./snippets/debug/inspect-file-encoding.ps1) and [`.sh`](./snippets/debug/inspect-file-encoding.sh).

### Glossary matches came back empty

```sql
-- Job's source/target lang
SELECT id, config_snapshot->>'source_lang'
FROM translation_jobs ORDER BY created_at DESC LIMIT 5;

SELECT DISTINCT target_lang FROM job_rows WHERE job_id = '<id>';

-- Was the augmenter actually called?
SELECT augmenter_log FROM job_rows WHERE job_id = '<id>' LIMIT 5;
-- "requested": ["glossary"] present but "glossary_terms": [] → zero matches
```

Then compare those tags to the CSV header lang codes. If they're in
different forms (`ko` vs `ko-KR`), see #2.

### Reverse-engineering mojibake

```python
mojibake = "ë³´íµ"
for enc in ["latin-1", "cp1252", "cp949", "utf-8"]:
    try:
        recovered = mojibake.encode(enc).decode("utf-8")
        print(enc, "→", recovered)
    except Exception:
        pass
```

A meaningful word in the target language identifies the original
encoding. CLI version: [`snippets/debug/mojibake_trace.py`](./snippets/debug/mojibake_trace.py).

---

## 9. References

- [WHATWG Encoding Standard](https://encoding.spec.whatwg.org/) —
  `TextDecoder` supported encodings.
- [RFC 5987](https://datatracker.ietf.org/doc/html/rfc5987) —
  Content-Disposition with non-ASCII filenames.
- [BCP 47](https://datatracker.ietf.org/doc/html/rfc5646) — language
  tag specification.
- [Unicode UAX #15](https://unicode.org/reports/tr15/) — Normalization
  Forms (NFC / NFD).
