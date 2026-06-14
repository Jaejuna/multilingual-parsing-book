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
8. [Glossary adherence — closing the feedback loop](#8-glossary-adherence--closing-the-feedback-loop)
9. [A/B-testing a matcher change](#9-ab-testing-a-matcher-change)
10. [From glossary to lexicon / knowledge graph](#10-from-glossary-to-lexicon--knowledge-graph)
11. [Language equity — a responsible-AI screen](#11-language-equity--a-responsible-ai-screen)
12. [Building an NLU intent + slot dataset](#12-building-an-nlu-intent--slot-dataset)
13. [The same metrics in SQL](#13-the-same-metrics-in-sql)

**Appendix**

14. [Pre-work checklist](#14-pre-work-checklist)
15. [Snippet index](#15-snippet-index)
16. [Debugging commands](#16-debugging-commands)
17. [References](#17-references)

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

Part I is about *parsing bugs* — one defect, one fix. A Linguistic Engineer
on a voice-assistant team spends most of the day a layer up: turning messy
multilingual inputs into **datasets, metrics, and feedback loops** that ML
systems consume. Part II is that layer, built on the exact same primitives
(encoding discipline #1, lang-code aliases #2, script-aware matching #4).

Every tool here is **stdlib-only**, prints a Markdown report by default
(`--format json` for machines), exits non-zero on findings (so it works as
a CI gate), and ships a sample with planted defects plus a pytest that
proves the defects are caught. Run the suite from `snippets/`:

```bash
python -m pytest tests/ -q      # 16 tests
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

**Three views of the same metrics.** Knowing *which tool to reach for* is
the actual skill, so these metrics are computed three ways:

| view | when to use it | file |
|------|----------------|------|
| stdlib (`csv` + `Counter`) | zero-dependency, streamable drop-in | this chapter |
| pandas (`groupby`/`melt`) | exploratory analysis, notebooks | [`snippets/pandas/corpus_metrics_pandas.py`](./snippets/pandas/corpus_metrics_pandas.py) |
| SQL (window functions) | the data already lives in Postgres | [#13](#13-the-same-metrics-in-sql) |

The pandas view is parity-tested against this one (same numbers on the same
sample). For data that doesn't fit in RAM, a forthcoming scaling chapter
reaches for polars/duckdb instead — pandas loads everything into memory.

## 8. Glossary adherence — closing the feedback loop

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

## 9. A/B-testing a matcher change

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

## 10. From glossary to lexicon / knowledge graph

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

## 11. Language equity — a responsible-AI screen

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

## 12. Building an NLU intent + slot dataset

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

## 13. The same metrics in SQL

When the data already landed in Postgres, you don't export — you query.
[`quality_metrics.sql`](./snippets/sql/quality_metrics.sql) expresses the
Part II metrics against the #6 evaluation schema: glossary adherence by
language, per-language coverage with gap-from-best (window functions),
lang-code form drift (#2), a mojibake screen (#1), copy-through detection,
and augmenter health from the JSONB `augmenter_log`. Prisma's camelCase
columns are double-quoted throughout (#6.2).

---

# Appendix

## 14. Pre-work checklist

### 14.1 Before ingesting multilingual text

- [ ] Where is the decode step (browser? server? both)?
- [ ] What's the assumed encoding? Are non-UTF-8 inputs possible?
- [ ] BOM handling?
- [ ] NFC/NFD normalization required? (macOS filenames, Hangul jamo)

### 14.2 When handling lang codes

- [ ] Do base (`ko`) and locale (`ko-KR`) forms appear in the same
      code path?
- [ ] Is there an alias / normalization policy?
- [ ] Do you need to preserve region (`zh-CN` vs `zh-TW`)?
- [ ] Casing? Hyphen vs underscore?

### 14.3 When writing a term matcher

- [ ] CJK vs Latin word-boundary semantics?
- [ ] `min_len` guard against single-character noise?
- [ ] Longest-match-first ordering?
- [ ] Pre-normalization (whitespace, HTML tags)?
- [ ] Performance: at terms × segments > 1M, reconsider data
      structure.

### 14.4 When implementing upload/download

- [ ] Upload: read raw bytes and detect encoding (NEVER `File.text()`
      on user-controlled CSV).
- [ ] Download: include `filename*=UTF-8''...` in
      `Content-Disposition`.
- [ ] BOM the export if Excel is the likely consumer.
- [ ] Match Content-Type on presigned PUT requests.

### 14.5 When crossing two DB boundaries

- [ ] Is migration ownership explicit?
- [ ] When does cached state become stale?
- [ ] Is publish/sync idempotent (safe to re-run)?
- [ ] If a transaction straddles both stacks, how is partial failure
      handled?

### 14.6 When storing evaluation / debug data

- [ ] Does this benefit from normalization (indexing, analytics)?
- [ ] How big is the per-row payload? (KB per row → reconsider)
- [ ] What's the retention policy?
- [ ] Any PII?

### 14.7 When resolving per-locale content files

- [ ] Are locale variants (`*.en.mdx`) excluded from the base listing?
- [ ] Does a missing variant fall back to the base file?
- [ ] Frontmatter merged field-by-field, body replaced wholesale?
- [ ] Are base-derived fields (thumbnail, reading time) recomputed from
      the base, not read off the merged object?
- [ ] Do blank values in a variant clobber good base values?

---

## 15. Snippet index

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

---

## 16. Debugging commands

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

## 17. References

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
