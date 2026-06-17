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
3. [Per-locale content files — partial frontmatter fallback](#3-per-locale-content-files--partial-frontmatter-fallback)
4. [Glossary matching — the substring pitfall](#4-glossary-matching--the-substring-pitfall)
5. [File downloads — RFC 5987 Content-Disposition](#5-file-downloads--rfc-5987-content-disposition)
6. [Two ORMs, one Postgres — boundary discipline](#6-two-orms-one-postgres--boundary-discipline)

**Part II — dataset engineering for ML**

7. [Auditing a corpus as data](#7-auditing-a-corpus-as-data)
8. [The same metrics, the pandas way](#8-the-same-metrics-the-pandas-way)
9. [Glossary adherence — closing the feedback loop](#9-glossary-adherence--closing-the-feedback-loop)
10. [A/B-testing a matcher change](#10-ab-testing-a-matcher-change)
11. [From glossary to lexicon / knowledge graph](#11-from-glossary-to-lexicon--knowledge-graph)
12. [Language equity — a responsible-AI screen](#12-language-equity--a-responsible-ai-screen)
13. [Building an NLU intent + slot dataset](#13-building-an-nlu-intent--slot-dataset)
14. [The same metrics in SQL](#14-the-same-metrics-in-sql)
15. [Scaling to a corpus that doesn't fit in RAM](#15-scaling-to-a-corpus-that-doesnt-fit-in-ram)
16. [Was the difference real? significance for the A/B](#16-was-the-difference-real-significance-for-the-ab)
17. [Data quality, measured in model accuracy](#17-data-quality-measured-in-model-accuracy)
18. [Similarity matching — catching what exact matching misses](#18-similarity-matching--catching-what-exact-matching-misses)
19. [Reasoning over the lexicon](#19-reasoning-over-the-lexicon)
20. [Merging heterogeneous sources into one corpus](#20-merging-heterogeneous-sources-into-one-corpus)

**Appendix**

- A. [Pre-work checklist](#a-pre-work-checklist)
- B. [Snippet index](#b-snippet-index)
- C. [Debugging commands](#c-debugging-commands)
- D. [References](#d-references)

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
  │     - substring matching                 ← #4 partial-match & boundaries
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

## 3. Per-locale content files — partial frontmatter fallback

> Context: this one is **not** from the eval platform — it's from a
> statically-generated MDX site serving the same posts in `ko` (base) and
> `en`. Same i18n shape, a different layer: here the language code decides
> **which file to load**, not which dictionary key to look up. Same class
> of bug, shipped and fixed.

### 3.1 The layout

One base file per slug, plus optional per-locale override files:

```
content/
  my-post.mdx        ← base (ko): full frontmatter + body + thumbnail
  my-post.en.mdx     ← en override: frontmatter only, often partial
```

The base file is the source of truth. The locale file overrides **some**
frontmatter fields and replaces the body — it is not a standalone post.

### 3.2 Two failures this layout invites

**(a) The variant counted as a separate post.** A naive
`readdir().filter(f => f.endsWith(".mdx"))` lists `my-post.en.mdx` as its
own entry, so the index renders the post twice.

**(b) Blind spread drops inherited fields.** The thumbnail is derived from
the base body; the `.en.mdx` file has no `thumbnail`. Spreading
`{ ...base, ...variant }` over frontmatter is fine — until the variant
carries a key with an empty value, which then overrides the good base
value with nothing. Fields the locale file doesn't own (thumbnail, reading
time) must be recomputed from the **base**, not read off the merged object.

### 3.3 Strategy: list base only, override on request

Two stages, mirroring the alias rule in #2 — exact (locale) wins, base is
the floor:

```ts
const LOCALE_RE = /\.(en|ja|zh)\.mdx$/;       // variant suffixes

// 1) List: base files only — variants are not standalone posts
function getAllPosts(): Post[] {
  return readdirSync(CONTENT)
    .filter((f) => f.endsWith(".mdx") && !LOCALE_RE.test(f))
    .map((f) => parseBase(join(CONTENT, f)));
}

// 2) Resolve one slug for a locale: base is the floor, locale on top
function getPost(slug: string, locale: string): Post {
  const base = parseBase(join(CONTENT, `${slug}.mdx`));   // always exists
  if (locale === DEFAULT_LOCALE) return base;

  const variantPath = join(CONTENT, `${slug}.${locale}.mdx`);
  if (!existsSync(variantPath)) return base;              // fall back to base

  const variant = matter(readFileSync(variantPath, "utf-8"));
  return {
    ...base,                                  // thumbnail, readingTime — from base
    frontmatter: { ...base.frontmatter, ...stripEmpty(variant.data) },
    content: variant.content,                 // body fully replaced, not merged
  };
}
```

### 3.4 `gray-matter` returns `data` and `content` separately

`matter(raw)` yields `{ data, content }` — the frontmatter object and the
raw body string. Only `data` participates in the locale merge; `content`
is the body you hand to the MDX renderer. Keep them apart — never spread
`content` into frontmatter:

```ts
const { data, content } = matter(raw);
```

**Trade-off / gotcha:** spreading `...variant.data` copies only the keys
the variant declares — good for partial override, but an author who writes
`thumbnail:` with a blank value silently erases the base value. Either
`stripEmpty()` the nullish keys before merging (above) or document that
locale files must **omit** inherited fields, never blank them.

→ Runnable: [`snippets/locale-content/resolve-locale-content.ts`](./snippets/locale-content/resolve-locale-content.ts).

---

## 4. Glossary matching — the substring pitfall

### 4.1 Current behavior

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

### 4.2 Improvement options (not all applied)

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

Two jobs are awkward with a flat term list and natural with a **trie**:
typeahead (every term starting with `cool`) and longest-match segmentation of
spaceless scripts (find the longest dictionary term at each position).
[`prefix_index.py`](./snippets/glossary-matching/prefix_index.py) is that trie —
the same structure Aho-Corasick is built on, minus the failure links, so it reads
as the gentler introduction.

### 4.3 Real bug: `case_sensitive` option silently dropped

`GlossaryAugmenter.config_schema` declares a `case_sensitive` option,
but `resource_resolver._spec_for_glossary` only forwards `file_ref` —
the option is set in the UI/Resource layer, then thrown away before
the worker sees it. The augmenter always runs case-insensitive. To fix,
read it from `ResourceVersion.meta` and include it in the spec.

---

## 5. File downloads — RFC 5987 Content-Disposition

### 5.1 The problem

Naive Korean filename in a download header:

```
Content-Disposition: attachment; filename="용어집.csv"
```

| Browser | Result |
|---------|--------|
| Chrome | usually OK (proprietary UTF-8 sniffing) |
| Firefox | mojibake filename (treats value as Latin-1) |
| Safari | may refuse download or save with garbled name |

### 5.2 Fix: dual `filename` + `filename*`

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

### 5.3 Add a BOM when the destination is Excel

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

## 6. Two ORMs, one Postgres — boundary discipline

### 6.1 Structure

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

### 6.2 Principles

- **Migration ownership is exclusive.** A given table is migrated by
  one stack only; the other accesses it read-only via raw SQL.
- **Publish is a boundary.** Job results (FastAPI) → evaluation pool
  (Prisma) is handled by a **Next API route in a Prisma transaction**.
  FastAPI exposes data but does not INSERT into Prisma-owned tables.
- **Quoting matters.** When SQLAlchemy reads Prisma's camelCase
  columns (`"sourceText"`, `"externalId"`) via raw SQL, double-quote
  them — Postgres folds unquoted identifiers to lowercase.

### 6.3 Normalize vs JSON column — decision rule

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

# Part II — dataset engineering for ML

Part I is about *parsing bugs* — one defect, one fix. The work one layer up is
turning messy multilingual inputs into **datasets, metrics, and feedback loops**
that ML systems consume. Part II is that layer, built on the exact same
primitives (encoding discipline #1, lang-code aliases #2, script-aware matching
#4).

Every tool here is **stdlib-only**, prints a Markdown report by default
(`--format json` for machines), exits non-zero on findings (so it works as
a CI gate), and ships a sample with planted defects plus a pytest that
proves the defects are caught. Run the suite from `snippets/`:

```bash
python -m pytest tests/ -q      # 49 tests
```

## 7. Auditing a corpus as data

Part I finds defects one at a time; production never delivers them that way.
A single vendor CSV arrives as cp949 bytes, with mixed `ko`/`ko-KR` headers,
half the Japanese column empty, and three rows where `{player_name}` got
"translated". [`audit_corpus.py`](./snippets/dataset-quality/audit_corpus.py)
makes **one pass** and reports all of it with row-level pointers, so the data
owner fixes the source instead of you patching symptoms downstream.

It computes, over a wide-format corpus (one column per language):

| Check | Ties back to |
|-------|--------------|
| encoding used (utf-8 vs cp949), mojibake cells, U+FFFD | #1 |
| not-NFC cells (jamo-split Hangul) | #1.4 |
| mixed lang-code forms (`ko` vs `ko-KR`) | #2 |
| per-language coverage / missing translations | #4 |
| duplicate keys & source values | — |
| placeholder parity (`{0}`, `%s`, `<br>`) across languages | — |
| length-ratio outliers vs the base column | — |

```bash
python audit_corpus.py sample_corpus.csv          # markdown report, exit 1 if dirty
python audit_corpus.py corpus.csv --format json --out report
```

The auditor practices what the book preaches: it reads with `utf-8-sig`
then falls back to cp949 (#1.5), and reuses the `lang_aliases` rule (#2).

The same corpus metrics are expressed two other ways for contrast — in
pandas (#8) and in SQL (#14) — so picking a tool becomes a deliberate choice
rather than a default.

## 8. The same metrics, the pandas way

Chapter 7 computed corpus quality in the standard library; #14 computes it in
SQL. This is the third view: pandas, the lingua franca of data analysis. The
skill on display is not pandas itself — it is knowing *which* tool a question
wants.

| view | reach for it when | cost |
|------|-------------------|------|
| stdlib (#7) | zero-dependency, streamable drop-in | verbose |
| pandas (here) | exploratory analysis, notebooks, ad-hoc pivots | loads all into RAM |
| SQL (#14) | the data already lives in Postgres | round-trips |

### Vectorized, not looped

The stdlib auditor walks rows with a `Counter`. pandas expresses the same
questions as whole-column operations:

```python
nonempty = cells.replace("", pd.NA).notna()
coverage = nonempty.mean()                       # fill rate per language
dup_keys = df[key].str.strip().duplicated().sum()
```

### Parity is the contract

A tool you can't check against a known-good baseline is useless, so a test
asserts the pandas view returns the *same numbers* as #7 on the same sample
(`test_pandas_parity_with_stdlib_audit`):

```
coverage ja_JP: stdlib=0.875  pandas=0.875
dup_keys:       stdlib=1      pandas=1
len_outliers:   stdlib=2      pandas=2      PARITY: ALL MATCH
```

### The vectorization gotcha

Getting parity took a fix. A blank target cell has length 0, and `0 / base`
is a ratio of 0 — which the outlier rule reads as "absurdly short" and flags.
The stdlib loop skipped absent cells; the vectorized version silently counted
them. The fix maps empty -> `NA` so missing cells drop out of both the median
and the mask:

```python
tgt_len = cells.str.len().replace(0, pd.NA)      # empty -> NA, not 0
```

Lesson: vectorized code is fast but does not handle "missing vs zero vs empty"
for you — that distinction is the heart of real pandas work, not API recall.

### When pandas is the wrong call

- the data doesn't fit in RAM -> a forthcoming scaling chapter uses polars
  (lazy) / duckdb (out-of-core); pandas would OOM
- you need a zero-dependency drop-in -> #7 stdlib
- the data already lives in a warehouse -> #14 SQL

Runnable: [`snippets/pandas/corpus_metrics_pandas.py`](./snippets/pandas/corpus_metrics_pandas.py).

## 9. Glossary adherence — closing the feedback loop

Shipping a glossary is an *input*. The question product actually cares about
is the *outcome*: did the model use it?
[`glossary_adherence.py`](./snippets/glossary-eval/glossary_adherence.py)
takes the source text, the MT output, and the glossary, and for every
segment × language checks whether a glossary term present in the source
produced its mandated translation in the output.

```
| language | applicable | applied | adherence |
| ja       | 7          | 4       | 57.1% ⚠️  |   ← actionable: top misses listed
| zh-CN    | 7          | 6       | 85.7%     |
| ko       | 7          | 7       | 100.0%    |
```

It is the evaluation twin of #4: #4 finds terms to *inject*; this measures
whether the injected term *survived*. Matching is script-aware — `\b` for
Latin, substring for CJK (#4.2). It does **not** judge fluency; that's a
separate LLM-as-judge tool, deliberately out of scope.

## 10. A/B-testing a matcher change

README #4 lists matching strategies in prose, where bad decisions hide.
Before swapping the production matcher you owe product a number.
[`strategy_ab.py`](./snippets/experiments/strategy_ab.py) runs each strategy
over a **labeled gold set** and reports precision / recall / F1:

```
| strategy            | precision | recall | F1     | FP | FN |
| word_boundary 🏆    | 100.0%    | 100.0% | 100.0% | 0  | 0  |
| word_boundary+min3  | 100.0%    | 71.4%  | 83.3%  | 0  | 2  |
| substring           | 63.6%     | 100.0% | 77.8%  | 4  | 0  |
```

The trade-off is now visible: substring over-fires (`AI` inside `Said`); the
min-length guard kills 2-char terms (`AI`, `OK`). This is the "design and
conduct product experiments" muscle: fixed control set, candidate variants,
one metric, one winner.

## 11. From glossary to lexicon / knowledge graph

A flat glossary answers "how do I say X in Japanese" and nothing else. The
moment you need "which terms are in the COMBAT domain" or "given 戦利品, what
concept is that and all its labels", you need a graph.
[`build_lexicon.py`](./snippets/knowledge-graph/build_lexicon.py) lifts the
flat CSV into a concept graph with SKOS/lexinfo-style triples:

```
concept:loot skos:prefLabel "loot"@en .
concept:loot skos:prefLabel "戦利品"@ja .
concept:loot dct:subject domain:Combat .
concept:loot skos:broader concept:item .
```

…plus a reverse index: any surface form in any language → its concept →
every other label. That cross-lingual lookup is exactly what a lexicon-backed
NLU layer or glossary augmenter consumes.

```bash
python build_lexicon.py glossary_lex.csv --lookup 戦利品   # -> concept 'loot' + all labels
python build_lexicon.py glossary_lex.csv --format triples --out lexicon
```

## 12. Language equity — a responsible-AI screen

Aggregate quality numbers hide disparity: "92% coverage" can mean every
language is at 92%, or English at 100% and five languages at 70%.
[`coverage_bias.py`](./snippets/responsible-ai/coverage_bias.py) disaggregates
by language and flags inequity with label-free proxies — coverage,
copy-through (target == source), and too-short stubs — then reports the
spread (gap from best, coefficient of variation):

```
- coverage gap (best − worst): 50.0% ⚠️
## ⚠️ Underserved languages (>10pp below best)
- `th` — coverage 50.0%; prioritize for data collection
```

The sample spans RTL (Arabic, Hebrew) and Indic (Hindi) columns. The tool
also **caveats its own metric**: char-length comparison over-flags dense
scripts (CJK), so `too-short` is a within-script screen, not a verdict —
metric self-awareness is part of responsible-AI review.

## 13. Building an NLU intent + slot dataset

The voice-assistant NLU model needs labeled utterances: each tagged with an
intent and slot spans. Hand-writing thousands per language guarantees
inconsistent offsets and lopsided class balance.
[`build_intent_dataset.py`](./snippets/nlu/build_intent_dataset.py) expands
templates × slot values combinatorially and **computes the character spans
automatically**, so labels are always exact — including CJK:

```jsonl
{"lang":"en","intent":"buy_item","text":"buy two sword",
 "slots":[{"name":"count","value":"two","start":4,"end":7},
          {"name":"item","value":"sword","start":8,"end":13}]}
```

It also reports class balance and warns on thin `(lang, intent)` cells, the
ASR/NLU-facing complement to the rest of the book.

Once spans arrive from several annotators or a rule-tagger plus a model, they
overlap, and two kinds of overlap mean opposite things: same-label overlaps are
one mention split in two (merge them), different-label overlaps are a genuine
disagreement (surface them, never merge).
[`merge_spans.py`](./snippets/nlu/merge_spans.py) settles both with one
sort-and-sweep over the spans (O(n log n), not all-pairs) — the same interval
pattern used for calendars and genome ranges, here cleaning annotation data.

## 14. The same metrics in SQL

When the data already landed in Postgres, you don't export — you query.
[`quality_metrics.sql`](./snippets/sql/quality_metrics.sql) expresses the
Part II metrics against the #6 evaluation schema: glossary adherence by
language, per-language coverage with gap-from-best (window functions),
lang-code form drift (#2), a mojibake screen (#1), copy-through detection,
and augmenter health from the JSONB `augmenter_log`. Prisma's camelCase
columns are double-quoted throughout (#6.2).

## 15. Scaling to a corpus that doesn't fit in RAM

The Part II tools load the whole corpus with `rows = list(reader)` and match
terms with a `for term in terms` loop. Both are fine for a vendor CSV and both
fall over once the corpus reaches millions of rows. This chapter replaces each
with a technique that scales — and, in the spirit of the book, proves it with
numbers rather than adjectives.

### Time: Aho-Corasick vs the naive loop

The naive loop rescans every term for every segment — O(terms × text). The
Aho-Corasick automaton compiles all terms once and makes a single pass —
O(text + matches), independent of the term count.

```
| terms | naive (ms) | AC search (ms) | search speed-up | counts agree |
|    50 |        8.5 |           12.1 |            0.7x | yes |
|   200 |       46.0 |           18.0 |            2.6x | yes |
|  1000 |      150.7 |           14.6 |           10.3x | yes |
|  5000 |      912.7 |           26.6 |           34.4x | yes |
```

Note the honest crossover: at 50 terms the naive loop *wins* — the automaton's
build/overhead isn't amortized yet. By 5,000 terms Aho-Corasick is ~34× faster
and its search time stays roughly flat. Reach for it only past the crossover,
not reflexively. Runnable: [`snippets/benchmark/bench_matching.py`](./snippets/benchmark/bench_matching.py).

### Memory: Welford vs load-everything

Most checks stream trivially: read a row, bump a counter, discard the row. The
holdout is the length-ratio outlier (#7), which needs a *median* — and a median
needs every value at once. Welford's online algorithm sidesteps that: it keeps
a running mean and variance in O(1) memory, so outliers are flagged by z-score
in a single pass.

```
| rows      | load peak (KB) | stream peak (KB) |
|    10,000 |            725 |                0 |
|   100,000 |          3,129 |                0 |
| 1,000,000 |         31,691 |                0 |
```

Load-everything peak memory climbs with the row count; the streaming peak is
flat — three floats, regardless of how many rows go by. Runnable:
[`snippets/benchmark/stream_vs_load.py`](./snippets/benchmark/stream_vs_load.py).

The one check that genuinely cannot be O(1) is **exact duplicate detection** —
it must remember every key seen. Be honest about that boundary: accept
O(distinct keys) memory, sort externally first, or use an approximate structure
(a Bloom filter). Don't pretend a stateful check is stateless.

### Beyond one machine: columnar engines

When you want real analytic queries (group-bys, joins, percentiles) over data
that won't fit in RAM, stop hand-rolling streaming aggregators and reach for a
columnar engine that streams from disk — DuckDB (SQL over files) or polars
(lazy DataFrame). This is the production escalation of #8 (pandas) and #14 (SQL):

```python
duckdb.execute('''
  SELECT lang, AVG(CASE WHEN trim(text) <> '' THEN 1.0 ELSE 0 END) AS coverage
  FROM read_csv(?, header=true) GROUP BY lang''', [path])   # streams from disk
```

[`snippets/scale/out_of_core.py`](./snippets/scale/out_of_core.py) computes the
same coverage three ways — stdlib stream, DuckDB, polars lazy — and asserts they
agree. DuckDB/polars are optional heavy dependencies (like `pyahocorasick` in
#4); the stdlib path always runs.

### Three streaming primitives worth owning

Once the data won't fit in memory, a few classic algorithms keep coming up; each
holds bounded memory regardless of stream size, and each is one short file:

- **Top-K with a bounded heap** — "which terms are most often left
  untranslated?" without sorting the whole corpus. A size-K min-heap is O(K)
  memory, O(N log K) time:
  [`scale`-adjacent `dataset-quality/top_terms.py`](./snippets/dataset-quality/top_terms.py).
- **K-way merge of sorted shards** — the merge half of an external sort: combine
  shards you can't concatenate in RAM, via a heap with one slot per shard
  ([`scale/merge_shards.py`](./snippets/scale/merge_shards.py)).
- **Reservoir sampling** — a uniform spot-check sample in one pass, without
  knowing the stream length up front
  ([`scale/reservoir_sample.py`](./snippets/scale/reservoir_sample.py)).

### Decision rule

| situation | reach for |
|-----------|-----------|
| past the term-count crossover | Aho-Corasick (#4) |
| streaming stats, one pass | Welford z-score (not median) |
| bigger than RAM + analytic queries | DuckDB / polars |
| still fits in RAM | the simpler #7 / #8 tools |

## 16. Was the difference real? significance for the A/B

Chapter 10 produced a clean table — `word_boundary` 100%, `substring` 78% — and
stopped there. But a table is a point estimate, and at 13 gold judgements that
gap can be luck. This chapter adds the two things that turn "we A/B'd it" into a
defensible claim: a significance test and a confidence interval. Run it with
`strategy_ab.py --significance`.

### McNemar: are the two strategies actually different?

Comparing two strategies on the *same* items is a paired problem, so a plain
two-sample test is the wrong tool. McNemar looks only at the *discordant* items
— where one strategy is right and the other wrong — and asks whether that split
could be a coin flip. We use the exact binomial form because the chi-square
approximation is unreliable at the small n a hand-labelled gold set usually has.

### The same gap, two sample sizes

n = 13 (chapter 10's set):

```
word_boundary vs the runner-up
discordant: 2 vs 0  ->  exact p = 0.50  ->  NOT significant
```

n = 120 (the same kinds of cases, more of them):

```
word_boundary vs substring
discordant: 54 vs 0  ->  exact p < 0.0001  ->  significant
```

Identical-looking winners, opposite conclusions. The only thing that changed is
how much evidence there was. This is why "we ran an A/B" is not a result until
it carries an n and a p.

### Bootstrap CI: how wide is each number?

The p-value answers "different or not"; a confidence interval answers "how sure
is each estimate". Resample the gold items with replacement, recompute F1 each
time, and take the middle 95%:

```
| substring F1 | 95% CI         | n   |
|        71.0% | [62.9%, 77.6%] | 120 |   tight — trustworthy
|        77.8% | [50.0%, 95.2%] |  13 |   spans 45 points — tells you nothing
```

A wide interval is the honest signal to collect more data before deciding.

### Takeaway

Report a winner with three things, never one: the point estimate, a confidence
interval, and a paired significance test. The first is what juniors report; all
three are what makes the call defensible. Runnable:
[`snippets/experiments/strategy_ab.py`](./snippets/experiments/strategy_ab.py) `--significance`.

## 17. Data quality, measured in model accuracy

Every other chapter treats data as the product; this one connects it to what
consumes it — a model. "Clean your labels" is advice until you can price it.
[`data_quality_impact.py`](./snippets/data-model/data_quality_impact.py) trains an
actual classifier — a standard-library multinomial Naive Bayes, just smoothed
word counts in log space — on training data corrupted by a known amount of label
noise, and scores it against a **clean** test set:

```
| train label noise | test accuracy | drop from clean |
|                0% |         81.8% |            0.0% |
|               40% |         81.3% |            0.4% |
|               45% |         74.9% |            6.9% |
|               50% |         46.7% |           35.1% |
```

The shape is the lesson, and it is not "more noise = linearly worse". Naive Bayes
aggregates counts over the whole training set, so moderate label noise mostly
averages out — accuracy barely moves up to ~40% — then collapses toward chance as
the labels approach 50% wrong. The data-quality budget is real but nuanced: a
robust model tolerates some noise, and this curve says how much, in points of
accuracy. (The test set is never corrupted — we measure how bad *training* data
hurts a model judged against the truth.) It also earns the book a real, if tiny,
ML model so the data→model link isn't hand-waved.

## 18. Similarity matching — catching what exact matching misses

Chapter 4's matchers are exact: `cooldown` matches `cooldown` and nothing else.
Real input brings `cool-down`, `cooldwn` (typo), `cooldowns` (plural). The move
that recovers them is to stop comparing strings and compare *representations* —
turn each term into a vector and measure cosine similarity.
[`fuzzy_match.py`](./snippets/matching-similarity/fuzzy_match.py) does it with
character n-gram TF-IDF: no model, no dependency, but a genuine vector space.

```
| query     | exact? | best fuzzy | score |
| cooldown  | hit    | cooldown   | 1.00  |
| cool-down | miss   | cooldown   | 0.86  |
| cooldwn   | miss   | cooldown   | 0.78  |
| cooldowns | miss   | cooldown   | 0.96  |
| banana    | miss   | cooldown   | 0.00  |
```

Exact matching fires only on the first row; fuzzy recovers the variants and still
rejects the unrelated word. Why character n-grams, not word embeddings: they are
robust to typos and morphology, need no pretrained model, and survive the mixed
scripts this book deals with — at the cost of capturing *surface* similarity, not
*meaning*. `big` and `large` are synonyms with no shared characters, so this will
not link them; that is where real embeddings earn their weight. Know which problem
you have. This is the on-ramp from rules (#4) to learned representations.

When you want a *hard* answer rather than a similarity score — "accept this
candidate only if it is within one edit of a real term" —
[`edit_distance.py`](./snippets/matching-similarity/edit_distance.py) computes
Levenshtein distance with the classic two-row DP, plus a *bounded* variant that
bails as soon as the distance provably exceeds the budget (so screening a big
term list stays cheap). Cosine ranks; edit distance gates.

## 19. Reasoning over the lexicon

Chapter 11 built a concept graph but only read the direct `broader` edge. The
questions that make a knowledge graph worth having are transitive: what are *all*
the ancestors of `loot`, what falls under `object`, and which concepts does a
sentence mention? [`build_lexicon.py`](./snippets/knowledge-graph/build_lexicon.py)
now answers them.

```
$ build_lexicon.py glossary_lex.csv --ancestors loot
loot ⊂ item ⊂ object

$ build_lexicon.py glossary_lex.csv --link "collect the loot then check cooldown"
  'cooldown' -> concept:cooldown  {...}
  'loot'     -> concept:loot       {...}
```

- `ancestors`/`descendants` walk the `broader` chain **transitively**, with a
  cycle guard — that transitive ancestry is what lets a concept inherit its
  parent's domain
- `find_cycles()` rejects a broken `a → b → a` hierarchy (otherwise traversal
  never terminates)
- `link()` is **entity linking**: it scans free text for glossary surface forms
  (word boundary for Latin #4, substring for CJK) and resolves each to its
  concept, longest surface first so "AI Director" beats "AI"
- `topo_order()` (`--topo`) returns the concepts broader-before-narrower via a
  topological sort (Kahn's algorithm) — the order you need to propagate a domain
  *down* the hierarchy or validate that a child never contradicts its parent;
  it degrades to a total order even when `find_cycles()` finds a loop

Transitive reasoning plus entity linking turn the flat lexicon of #11 into
something you can actually query and connect to running text.

## 20. Merging heterogeneous sources into one corpus

A dataset is rarely one tidy file; it is a folder of exports — one saved cp949 by
Excel (#1), one with `ko_KR` headers and another with `ko-KR` (#2), overlapping
keys, and the same key translated two different ways in two files. Concatenating
blindly yields mojibake, split columns, duplicates, and silent conflicts.
[`build_corpus.py`](./snippets/multi-source/build_corpus.py) merges properly and,
crucially, **reports**:

```
- source2_cp949.csv: decoded cp949  ⚠️ legacy, contributed 2 cells
- merged languages: ko-KR, en-US, en, zh-CN
- conflicts: 2
| key    | lang  | kept | from    | dropped | from    |
| attack | ko-KR | 공격 | source1 | 어택    | source2 |
```

It is Part I's capstone: encoding fallback (#1, utf-8-sig then cp949), lang-code
canonicalization (#2, `ko_KR`/`KO-KR` → `ko-KR`), plus the two things a merge
needs — **conflict detection** (same key + language, different value; first
source wins, the loser is reported, never silently dropped) and **provenance**
(which source each value came from). Base `ko` and locale `ko-KR` stay distinct
columns by default — collapsing them loses region (#2's `zh-CN` vs `zh-TW`
trade-off); `--merge-base` folds them when you accept that.

`build_corpus.py` keys rows by an *exact* id. When the same entity appears under
cosmetic variants (`Cooldown`, `cool-down`, `Cooldown `) and is tied together only
indirectly (two records share an external id, not a name),
[`cluster_duplicates.py`](./snippets/multi-source/cluster_duplicates.py) groups
them with **union-find**: every shared signal unions two records, and the
connected components that remain are your deduplicated entities — capturing the
transitive `A~B~C` case that exact-match dedup splits into fragments.

---

# Appendix

## A. Pre-work checklist

### A.1 Before ingesting multilingual text

- [ ] Where is the decode step (browser? server? both)?
- [ ] What's the assumed encoding? Are non-UTF-8 inputs possible?
- [ ] BOM handling?
- [ ] NFC/NFD normalization required? (macOS filenames, Hangul jamo)

### A.2 When handling lang codes

- [ ] Do base (`ko`) and locale (`ko-KR`) forms appear in the same
      code path?
- [ ] Is there an alias / normalization policy?
- [ ] Do you need to preserve region (`zh-CN` vs `zh-TW`)?
- [ ] Casing? Hyphen vs underscore?

### A.3 When writing a term matcher

- [ ] CJK vs Latin word-boundary semantics?
- [ ] `min_len` guard against single-character noise?
- [ ] Longest-match-first ordering?
- [ ] Pre-normalization (whitespace, HTML tags)?
- [ ] Performance: at terms × segments > 1M, reconsider data
      structure.

### A.4 When implementing upload/download

- [ ] Upload: read raw bytes and detect encoding (NEVER `File.text()`
      on user-controlled CSV).
- [ ] Download: include `filename*=UTF-8''...` in
      `Content-Disposition`.
- [ ] BOM the export if Excel is the likely consumer.
- [ ] Match Content-Type on presigned PUT requests.

### A.5 When crossing two DB boundaries

- [ ] Is migration ownership explicit?
- [ ] When does cached state become stale?
- [ ] Is publish/sync idempotent (safe to re-run)?
- [ ] If a transaction straddles both stacks, how is partial failure
      handled?

### A.6 When storing evaluation / debug data

- [ ] Does this benefit from normalization (indexing, analytics)?
- [ ] How big is the per-row payload? (KB per row → reconsider)
- [ ] What's the retention policy?
- [ ] Any PII?

### A.7 When resolving per-locale content files

- [ ] Are locale variants (`*.en.mdx`) excluded from the base listing?
- [ ] Does a missing variant fall back to the base file?
- [ ] Frontmatter merged field-by-field, body replaced wholesale?
- [ ] Are base-derived fields (thumbnail, reading time) recomputed from
      the base, not read off the merged object?
- [ ] Do blank values in a variant clobber good base values?

---

## B. Snippet index

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
| `*.en.mdx` shows up as a duplicate post / EN post loses its thumbnail | [`locale-content/resolve-locale-content.ts`](./snippets/locale-content/resolve-locale-content.ts) |
| Matcher catches "AI" inside "Said" | [`glossary-matching/word_boundary_match.py`](./snippets/glossary-matching/word_boundary_match.py) |
| Term loop too slow with thousands of terms | [`glossary-matching/aho_corasick_match.py`](./snippets/glossary-matching/aho_corasick_match.py) |
| Hangul filename garbled in Firefox/Safari downloads | [`download/content-disposition-rfc5987.ts`](./snippets/download/content-disposition-rfc5987.ts) |
| Large CSV export times out | [`download/streaming-csv-export.ts`](./snippets/download/streaming-csv-export.ts) |
| Quick triage: "what encoding is this file?" | [`debug/inspect-file-encoding.ps1`](./snippets/debug/inspect-file-encoding.ps1) / [`.sh`](./snippets/debug/inspect-file-encoding.sh) |
| Term loop too slow at scale — prove the crossover | [`benchmark/bench_matching.py`](./snippets/benchmark/bench_matching.py) |
| Corpus won't fit in RAM — streaming (O(1)) vs load | [`benchmark/stream_vs_load.py`](./snippets/benchmark/stream_vs_load.py) |
| Bigger than RAM + analytic queries (DuckDB/polars) | [`scale/out_of_core.py`](./snippets/scale/out_of_core.py) |
| Reproduce the bugs hit building this book | [`debug/error_cases.py`](./snippets/debug/error_cases.py) |
| Price label noise in points of model accuracy | [`data-model/data_quality_impact.py`](./snippets/data-model/data_quality_impact.py) |
| Match term variants/typos with no model | [`matching-similarity/fuzzy_match.py`](./snippets/matching-similarity/fuzzy_match.py) |
| Accept a typo only within N edits of a real term | [`matching-similarity/edit_distance.py`](./snippets/matching-similarity/edit_distance.py) |
| Autocomplete terms by prefix / segment spaceless text | [`glossary-matching/prefix_index.py`](./snippets/glossary-matching/prefix_index.py) |
| Rank top-K untranslated terms without sorting everything | [`dataset-quality/top_terms.py`](./snippets/dataset-quality/top_terms.py) |
| Merge sorted corpus shards too big for RAM | [`scale/merge_shards.py`](./snippets/scale/merge_shards.py) |
| Uniform spot-check sample from a stream of unknown size | [`scale/reservoir_sample.py`](./snippets/scale/reservoir_sample.py) |
| Merge overlapping annotation spans / flag label conflicts | [`nlu/merge_spans.py`](./snippets/nlu/merge_spans.py) |
| Transitive ancestors / entity-link free text to concepts | [`knowledge-graph/build_lexicon.py`](./snippets/knowledge-graph/build_lexicon.py) |
| Order a concept hierarchy broader-before-narrower | [`knowledge-graph/build_lexicon.py`](./snippets/knowledge-graph/build_lexicon.py) `--topo` |
| Merge messy multi-source CSVs (encoding/lang-code/conflicts) | [`multi-source/build_corpus.py`](./snippets/multi-source/build_corpus.py) |
| Collapse duplicate records that link only transitively | [`multi-source/cluster_duplicates.py`](./snippets/multi-source/cluster_duplicates.py) |

### Field notes — bugs hit building this book

Every gotcha in this book was shipped, hit, and fixed; building Part II was no
exception, and several bugs we hit were the book's *own* lessons biting back.
Each is reproduced runnably (broken behaviour next to the fix) in
[`snippets/debug/error_cases.py`](./snippets/debug/error_cases.py).

1. **Console that proved #1.** `UnicodeEncodeError: 'cp949' codec can't encode '—'`
   when a tool printed its report — the Windows console is cp949. Fix:
   `sys.stdout.reconfigure(encoding="utf-8")`, now in every Part II CLI.
2. **A mojibake regex that missed mojibake.** The first pattern matched only
   `Ã`/`Â` leads; Korean-UTF-8-as-latin1 starts at `ë` (U+00EB). Fix: match the
   real signature, lead U+00C2–00F4 + continuation U+0080–00BF.
3. **dataclasses + importlib.** Loading a tool by path raised `AttributeError:
   'NoneType' object has no attribute '__dict__'` — `@dataclass` resolves
   annotations via `sys.modules[cls.__module__]`. Fix: register in
   `sys.modules` before `exec_module`.
4. **pandas counted blanks as outliers.** A blank cell's length 0 gives ratio 0
   → false "too-short" (#8). Fix: map empty → `NA` before the ratio.
5. **polars read "" as null.** A boolean built from null skews the group mean.
   Fix: `fill_null("")` first.
6. **An LCG that wasn't random enough.** Its *low* bit correlated with a
   6-language cycle → degenerate 0/1 coverage. Fix: use a *high* bit.
7. **Piping between two Python processes.** Surrogate-escaped bytes leaked
   across the pipe (`'\udceb' ... surrogates not allowed`). Fix: one process, or
   `PYTHONIOENCODING` for both — encoding boundaries (#1) exist between your own
   processes too.
8. **`.lower()` is not caseless matching (found in review).** Every matcher
   case-folded with `.lower()`, which is wrong outside Latin: German `STRASSE`
   ≠ `straße`, Turkish `İ`/`ı`, Greek final sigma. Fix: `str.casefold()`, the
   Unicode caseless fold — now used in every matcher (#4, #9, #18, Aho-Corasick).
9. **Full-width and half-width variants silently miss (#7).** A glossary `AI`
   never matched the full-width `ＡＩ` an IME or legacy system emits, nor
   half-width katakana. Fix: NFKC-normalize match keys before comparing
   (`unicodedata.normalize("NFKC", s)`); the matchers now fold NFKC + casefold
   together. NFKC is lossy, so do it on match keys, not stored/displayed text.

---

## C. Debugging commands

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

## D. References

- [WHATWG Encoding Standard](https://encoding.spec.whatwg.org/) —
  `TextDecoder` supported encodings.
- [RFC 5987](https://datatracker.ietf.org/doc/html/rfc5987) —
  Content-Disposition with non-ASCII filenames.
- [BCP 47](https://datatracker.ietf.org/doc/html/rfc5646) — language
  tag specification.
- [Unicode UAX #15](https://unicode.org/reports/tr15/) — Normalization
  Forms (NFC / NFD).
- [SKOS](https://www.w3.org/TR/skos-reference/) — Simple Knowledge
  Organization System (prefLabel / altLabel / broader), used in #10.
- [lemon / lexinfo](https://lemon-model.net/) — lexicon model for
  ontologies (part-of-speech and lexical metadata), used in #10.
