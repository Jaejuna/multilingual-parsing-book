# 다국어 파싱 플레이북

> 영어 버전: [README.md](./README.md) (primary)
> 실행 가능한 코드: [`snippets/`](./snippets/) — 패턴별 파일 분리, 영어 주석

게임 로컬라이제이션 평가 플랫폼(translation-eval) 작업 중 다국어 텍스트·CSV·용어집(TB)·번역
파이프라인을 다루며 정리한 함정과 해결 패턴을 한곳에 모은 문서. 다른 프로젝트로 가지고 가도
독립적으로 읽힐 수 있도록 self-contained 형태로 정리.

## 목차

0. [시스템 한눈에 — 어디서 무엇이 깨지는가](#0-시스템-한눈에--어디서-무엇이-깨지는가)
1. [인코딩 — 한국 Excel CSV 함정](#1-인코딩-encoding--한국-excel-csv-함정)
2. [언어 코드 — locale vs base 불일치](#2-언어-코드-lang-code--locale-vs-base-불일치)
3. [locale별 콘텐츠 파일 — frontmatter 부분 폴백](#3-locale별-콘텐츠-파일--frontmatter-부분-폴백)
4. [용어집 매칭 — substring 의 함정](#4-용어집-매칭-glossary-matching--substring-의-함정)
5. [파일 다운로드 — RFC 5987 Content-Disposition](#5-파일-다운로드--rfc-5987-content-disposition)
6. [두 ORM, 한 Postgres — 경계 다루기](#6-두-orm-한-postgres--경계-다루기)

**Part II — ML 을 위한 데이터셋 엔지니어링**

7. [코퍼스를 데이터로 감사하기](#7-코퍼스를-데이터로-감사하기)
8. [같은 지표를 pandas 방식으로](#8-같은-지표를-pandas-방식으로)
9. [용어집 준수율 — 피드백 루프 닫기](#9-용어집-준수율--피드백-루프-닫기)
10. [매처 변경을 A/B 테스트하기](#10-매처-변경을-ab-테스트하기)
11. [용어집에서 렉시콘 / 지식 그래프로](#11-용어집에서-렉시콘--지식-그래프로)
12. [언어 형평성 — 책임 있는 AI 스크린](#12-언어-형평성--책임-있는-ai-스크린)
13. [NLU intent + slot 데이터셋 구축](#13-nlu-intent--slot-데이터셋-구축)
14. [같은 지표를 SQL 로](#14-같은-지표를-sql-로)
15. [RAM 에 안 들어가는 코퍼스로 확장하기](#15-ram-에-안-들어가는-코퍼스로-확장하기)
16. [그 차이는 진짜였나? A/B 의 유의성](#16-그-차이는-진짜였나-ab-의-유의성)

**부록 (Appendix)**

- A. [작업 시 주의사항 체크리스트](#a-작업-시-주의사항-체크리스트)
- B. [스니펫 인덱스](#b-스니펫-인덱스)
- C. [디버깅 명령 모음](#c-디버깅-명령-모음)
- D. [참고 자료](#d-참고-자료)
- E. [자주 쓰는 코드 조각](#e-자주-쓰는-코드-조각)
- F. [실제로 부딪힌 이슈 타임라인 (translation-eval)](#f-실제로-부딪힌-이슈-타임라인-translation-eval)
- G. [안 한 것 / 미해결](#g-안-한-것--미해결)

---

## 0. 시스템 한눈에 — 어디서 무엇이 깨지는가

```
브라우저 (Next.js / TS)
  ├─ File 업로드 (CSV/XLSX/TMX)         ← 인코딩 깨짐 #1
  ├─ 헤더 매핑 + rewriteCSV               ← lang 코드 표기 충돌 #2
  └─ S3 PUT (presigned URL)
        ↓
S3 (raw 파일 보관)
        ↓
FastAPI worker (Python)
  ├─ S3 다운로드 → cache (utf-8-sig 권장)
  ├─ Augmenter (Glossary/Retrieval/Graph)
  │     - substring 매칭                 ← #4 부분문자열·단어경계
  │     - lang 코드 lookup              ← #2 다시 등장
  ├─ Prompt assemble ({{GLOSSARY}} 등 치환)
  └─ Engine 호출 (Vertex/Cortex/...)
        ↓
JobRow.augmenter_log (FastAPI 소유 테이블)
        ↓
Publish (Next API route, Prisma transaction)
        ↓
TextSegment / Translation / GlossaryMatch (Prisma 소유 테이블)
        ↓
평가 UI (EvaluationCard)
```

핵심 경계 4곳: **(브라우저 디코딩)**, **(S3 ↔ 워커 인코딩)**, **(lang 코드 정규화)**,
**(FastAPI ↔ Prisma DB 경계)**. 거의 모든 한글 깨짐·매칭 실패가 이 네 곳에서 터진다.

### 코드 스니펫

본 문서의 각 패턴에 대응하는 **runnable 스니펫**은 [`snippets/`](./snippets/) 트리에
파일로 분리되어 있다. 영어 주석 + self-contained 형태라 다른 프로젝트로 그대로
복사 가능. 카테고리별 index 는 [`snippets/README.md`](./snippets/README.md) 참고.

```
snippets/
├── encoding/           — 바이트 ↔ 문자열 변환 (BOM, cp949 fallback, CSV export)
├── lang-codes/         — locale ↔ base alias, BCP-47 정규화
├── locale-content/     — locale별 콘텐츠 파일 resolve + frontmatter 부분 폴백
├── glossary-matching/  — substring / word-boundary / Aho-Corasick
├── download/           — RFC 5987 Content-Disposition, 스트리밍 CSV
└── debug/              — 인코딩 inspector, mojibake CLI
```

---

## 1. 인코딩 (Encoding) — 한국 Excel CSV 함정

### 1.1 `File.text()` 는 무조건 UTF-8

브라우저 Web API 의 `Blob.text()` / `File.text()` 는 **인자가 없다**. 디코딩은 항상
UTF-8 로 강제된다. 한국 Excel 기본 저장 인코딩이 **cp949(EUC-KR)** 라는 사실과
정면 충돌.

```ts
// ❌ cp949 CSV가 UTF-8로 강제 디코딩 → mojibake → 그대로 S3로 업로드
file.text().then((txt) => {
  const rewritten = rewriteCSV(txt, ...);
  const blob = new Blob([rewritten], { type: "text/csv" });
  // 깨진 바이트가 S3에 영구 저장됨
});

// ✅ 원시 바이트를 받아 인코딩 감지 후 디코딩
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
    return new TextDecoder("euc-kr").decode(bytes);  // cp949 == windows-949 == euc-kr 호환
  }
}
```

`TextDecoder` 는 다음을 표준으로 지원한다: `utf-8`, `utf-16`, `euc-kr`, `shift_jis`,
`big5`, `gb18030`, `windows-1252` 등. 라이브러리 없이도 충분.

### 1.2 `new Blob([string])` 는 항상 UTF-8 출력

위 fallback 으로 euc-kr → JS string 으로 만들고, `new Blob([str])` 으로 다시 직렬화하면
**자동으로 UTF-8 바이트**가 된다 (MDN 보장). 따라서 "디코딩만 올바르면 업로드 결과는
무조건 UTF-8" 이라는 흐름이 성립.

### 1.3 BOM 은 뷰어 호환용이지 정합성 문제가 아니다

S3 에 올라간 파일이 정상 UTF-8 인데도 Excel 에서 열면 깨질 수 있다. 이유:

| 뷰어 | BOM 없는 UTF-8 한글 |
|------|---------------------|
| VSCode | 자동 감지 (대체로 OK, 가끔 cp1252 오탐) |
| 메모장 (Win11) | 보통 OK |
| **Excel (한국 윈도우)** | **무조건 시스템 codepage(cp949)로 읽음 → 깨짐** |

해결: 업로드 직전에 `﻿` (BOM) 한 글자를 앞에 붙인다.

```ts
const withBom = "﻿" + rewritten;
const blob = new Blob([withBom], { type: "text/csv;charset=utf-8" });
```

서버 측 파서는 `utf-8-sig` (Python) 또는 `TextDecoder('utf-8')` 가 BOM 을 자동 제거하니
영향 없음.

### 1.4 mojibake 패턴 진단표

깨진 문자열을 보고 원인을 역추적할 때:

| 보이는 모양 | 원래 인코딩 | 잘못 읽은 인코딩 |
|------------|------------|------------------|
| `ë³´íµ`, `ì¬ì©` | UTF-8 | Latin-1 / cp1252 |
| `?쒕쾲`, `?낅뜲?댄듃` | UTF-8 | cp949 |
| `ㅂㅗㅌㅗㅇ` (자모 분리) | NFD | NFC 만 기대 |
| 첫 3바이트 BOM이 글자로 보임 (`` 또는 `?`) | UTF-8+BOM | UTF-8 (BOM 제거 안 함) |

Python 으로 확인:

```python
bytes.fromhex("EBB3B4ED86B5").decode("utf-8")       # → "보통"
bytes.fromhex("EBB3B4ED86B5").decode("latin-1")     # → "ë³´íµ" (mojibake 재현)
```

### 1.5 Python 서버는 `utf-8-sig` 를 기본으로

```python
with path.open(encoding="utf-8-sig") as fh:   # BOM 자동 제거
    reader = csv.DictReader(fh)
```

`utf-8` 만 쓰면 BOM 이 첫 컬럼명 안에 끼어들어 (`'﻿ko-KR'`) DictReader 키 조회가
미묘하게 실패한다.

---

## 2. 언어 코드 (Lang Code) — locale vs base 불일치

### 2.1 사실 관계

게임 로컬라이제이션 도구·번역 엔진·평가 시스템마다 언어 코드 표기가 다르다.

| 표기 | 예 | 어디서 자주 보임 |
|------|-----|-----------------|
| ISO 639-1 (base) | `ko`, `en`, `ja`, `zh` | API 짧은 코드 |
| BCP 47 (locale) | `ko-KR`, `en-US`, `zh-CN`, `zh-TW`, `pt-BR` | CSV 헤더 |
| underscore 변종 | `ko_KR`, `pt_BR` | Java/Android 자원 |
| 대문자 변종 | `EN`, `JA` | 레거시 |

같은 시스템 안에서도 **CSV 는 `ko-KR`, Job snapshot 은 `ko`** 처럼 섞인다.

### 2.2 매칭 실패의 전형

용어집 augmenter 가 매칭 0건 반환 → "글로서리 적용 안 된다" 라고 보고가 들어옴.
근본 원인은 키 조회 실패였음:

```python
# CSV 로드 — 헤더가 "ko-KR" 이라 키도 "ko-KR" 로 저장
self._terms["ko-KR"] = {...}

# 매칭 — Job context 는 짧은 "ko"
src_dict = self._terms.get(ctx.source_lang, {})  # get("ko") → {}
```

테스트 코드만 봤을 때는 `ko` 로 만든 CSV 와 `ko` 로 호출하는 ctx 가 일치해 통과해
**버그가 안 보였다**. 실데이터는 locale 코드를 쓰면서 어긋남.

### 2.3 alias 등록 전략

키를 일률 정규화(prefix 만 남기기)하면 `zh-CN` 과 `zh-TW` 충돌. 그래서 **둘 다 등록**.

```python
def _lang_aliases(lang: str) -> list[str]:
    """'ko-KR' → ['ko-KR', 'ko']; 'ko' → ['ko']"""
    norm = lang.replace("_", "-")
    base = norm.split("-", 1)[0]
    return [norm, base] if base and base != norm else [norm]

# 등록 시
for key in _lang_aliases("ko-KR"):
    self._terms.setdefault(key, {})

# 조회 시 — exact 우선, base 폴백
for cand in _lang_aliases(ctx.source_lang):
    if cand in self._terms:
        src_dict = self._terms[cand]
        break
```

**트레이드오프**: `zh-CN` 과 `zh-TW` 둘 다 있을 때 ctx 가 `zh` 만 보내면 마지막 등록된 쪽이
이긴다. 명시성 떨어지지만 "안 매칭되는 것" 보다 낫다고 판단. ctx 가 풀 locale 을 보내는
게 정공법.

### 2.4 테스트 케이스 권장 매트릭스

```
csv "ko-KR"   × ctx "ko"     → 매칭돼야 함
csv "ko"      × ctx "ko-KR"  → 매칭돼야 함
csv "ko-KR"   × ctx "ko-KR"  → 매칭 (regression)
csv "ko"      × ctx "ko"     → 매칭 (regression)
csv "zh-CN" 와 "zh-TW" 공존 × ctx "zh-CN" → 정확히 CN 데이터만
csv "zh-CN" 와 "zh-TW" 공존 × ctx "zh"    → 둘 중 하나 (last write wins, 문서화 필요)
```

---

## 3. locale별 콘텐츠 파일 — frontmatter 부분 폴백

> 맥락: 이 장만 translation-eval 이 아니라 **MDX 정적 사이트**(같은 글을 `ko`(base)와
> `en` 으로 서빙)에서 나온 것이다. i18n 형태는 같지만 레이어가 다르다 — 여기서는 언어
> 코드가 "어떤 dict 키를 조회하느냐" 가 아니라 **"어떤 파일을 로드하느냐"** 를 결정한다.
> 같은 부류의 버그를 실제로 밟고 고쳤다.

### 3.1 파일 구조

slug 당 base 파일 하나 + locale별 override 파일(선택):

```
content/
  my-post.mdx        ← base (ko): 전체 frontmatter + 본문 + 썸네일
  my-post.en.mdx     ← en override: frontmatter만, 보통 일부만
```

base 파일이 source of truth. locale 파일은 frontmatter 의 **일부**만 덮어쓰고 본문을
교체한다 — 독립된 글이 아니다.

### 3.2 이 구조가 부르는 두 가지 실패

**(a) 변종이 별개 글로 카운트됨.** 단순한
`readdir().filter(f => f.endsWith(".mdx"))` 는 `my-post.en.mdx` 를 별도 엔트리로
잡아서 목록에 글이 두 번 뜬다.

**(b) blind spread 가 상속 필드를 날림.** 썸네일은 base 본문에서 추출되는데 `.en.mdx`
파일엔 `thumbnail` 키가 없다. frontmatter 를 `{ ...base, ...variant }` 로 펼치는 건
괜찮지만 — 변종이 빈 값을 가진 키를 들고 있으면 멀쩡한 base 값을 빈 값으로 덮어쓴다.
locale 파일이 소유하지 않는 필드(썸네일, 읽기 시간)는 merge 결과가 아니라 **base** 에서
다시 계산해야 한다.

### 3.3 전략: base만 나열, 요청 시 override

#2 의 alias 전략과 같은 결 — exact(locale) 우선, base 가 바닥값:

```ts
const LOCALE_RE = /\.(en|ja|zh)\.mdx$/;       // 변종 접미사

// 1) 목록: base 파일만 — 변종은 독립 글이 아님
function getAllPosts(): Post[] {
  return readdirSync(CONTENT)
    .filter((f) => f.endsWith(".mdx") && !LOCALE_RE.test(f))
    .map((f) => parseBase(join(CONTENT, f)));
}

// 2) 한 slug 을 locale 로 resolve: base 가 바닥, locale 이 위에
function getPost(slug: string, locale: string): Post {
  const base = parseBase(join(CONTENT, `${slug}.mdx`));   // 항상 존재
  if (locale === DEFAULT_LOCALE) return base;

  const variantPath = join(CONTENT, `${slug}.${locale}.mdx`);
  if (!existsSync(variantPath)) return base;              // 변종 없으면 base 폴백

  const variant = matter(readFileSync(variantPath, "utf-8"));
  return {
    ...base,                                  // 썸네일, readingTime — base 에서
    frontmatter: { ...base.frontmatter, ...stripEmpty(variant.data) },
    content: variant.content,                 // 본문은 통째로 교체, merge 아님
  };
}
```

### 3.4 `gray-matter` 는 `data` 와 `content` 를 따로 반환

`matter(raw)` 는 `{ data, content }` 를 준다 — frontmatter 객체와 raw 본문 문자열.
locale merge 에는 `data` 만 참여하고 `content` 는 MDX 렌더러에 넘길 본문이다. 둘을
섞지 말 것 — `content` 를 frontmatter 에 spread 하면 안 된다:

```ts
const { data, content } = matter(raw);
```

**트레이드오프/함정**: `...variant.data` 펼치기는 변종이 선언한 키만 복사한다 — 부분
override 엔 좋지만, 작성자가 `thumbnail:` 를 빈 값으로 적으면 base 값이 조용히 지워진다.
위처럼 merge 전에 `stripEmpty()` 로 빈 키를 걷어내거나, locale 파일은 상속 필드를
**비우지 말고 생략** 하라고 문서화할 것.

→ 실행 가능: [`snippets/locale-content/resolve-locale-content.ts`](./snippets/locale-content/resolve-locale-content.ts).

---

## 4. 용어집 매칭 (Glossary Matching) — substring 의 함정

### 4.1 현재 동작

```python
haystack = text if case_sensitive else text.lower()
for src, lang_map in src_dict.items():
    needle = src if case_sensitive else src.lower()
    if needle in haystack:        # 단순 substring
        matched.append({"source": src, "target": lang_map[target]})
```

장점: 빠르고 단순. CJK 처럼 단어 경계가 없는 언어에도 동작.
단점: 영어에서 `"AI"` 가 `"said"`, `"again"` 에도 매칭. `"AI"` 와 `"AI Director"` 가 동시에 매칭.

### 4.2 가능한 개선 (적용은 미정)

1. **언어별 분기**: 라틴 스크립트면 `\b{term}\b` 단어 경계 강제, CJK 면 substring 허용.
2. **min_len 가드**: 1글자 용어는 노이즈가 많으니 무시 (`len(term) < 2` skip).
3. **최장 매칭 우선**: `"AI Director"` 가 매칭됐다면 그 안의 `"AI"` 는 스킵.
4. **정규화**: `<br>`, 개행, 다중 공백 → 단일 공백.
5. **Aho-Corasick**: 용어 수천 개 × 세그먼트 수천 개 환경에서 O(N+M) 으로 줄임.

레거시 `module/glossary.py` 의 `extract_glossary_terms_fuzzy` 는 (2)(4) 를 이미 했지만
신규 `app/augmenters/glossary.py` 는 단순화하면서 빠짐. 필요하면 옮겨오면 됨.

### 4.3 case sensitivity 전파 누락 (실제 버그)

`GlossaryAugmenter.config_schema` 는 `case_sensitive` 옵션을 받지만,
`resource_resolver._spec_for_glossary` 는 `file_ref` 만 spec 으로 전달한다. UI/Resource
에서 옵션을 켜도 worker 에서는 항상 기본값(False). 옵션을 살리려면 ResourceVersion.meta
에서 읽어 spec 에 추가 필요.

---

## 5. 파일 다운로드 — RFC 5987 Content-Disposition

### 5.1 문제

한글 파일명을 `Content-Disposition: attachment; filename="용어집.csv"` 로 넣으면
브라우저별로:
- Chrome: 잘 받아짐
- Firefox: 깨진 파일명
- Safari: 다운로드 거부 또는 mojibake

### 5.2 해결

RFC 5987 의 `filename*` 파라미터를 같이 보낸다.

```ts
function contentDisposition(name: string): string {
  const fallback = name.replace(/[^\x20-\x7e]/g, "_");
  const encoded = encodeURIComponent(name).replace(/['()]/g, escape);
  return `attachment; filename="${fallback}"; filename*=UTF-8''${encoded}`;
}
```

- `filename=` 에는 ASCII 안전한 fallback (브라우저 호환)
- `filename*=UTF-8''<percent-encoded>` 가 우선됨

### 5.3 export 시 BOM 도 같이

CSV/XLSX export 의 경우, "다운받아서 Excel 에서 열 것" 이 거의 확정이라 BOM 을 붙여서
보낸다.

```ts
const csv = "﻿" + buildCsv(rows);
return new Response(csv, {
  headers: {
    "Content-Type": "text/csv; charset=utf-8",
    "Content-Disposition": contentDisposition(`${name}.csv`),
  },
});
```

---

## 6. 두 ORM, 한 Postgres — 경계 다루기

### 6.1 구조

같은 Postgres 인스턴스에 두 영역의 테이블이 공존:

```
Postgres
├── FastAPI 소유 (SQLAlchemy, alembic 없이 raw migration.sql)
│   ├── translation_jobs
│   ├── job_rows
│   ├── job_events
│   └── resources / resource_versions
└── Next 소유 (Prisma, prisma migrate)
    ├── text_segments
    ├── translations
    ├── evaluations
    ├── glossary_matches  ← 평가용 정규화
    └── ...
```

### 6.2 원칙

- **마이그레이션 소유권**: 같은 테이블에 두 ORM 이 마이그레이션 걸지 않는다. 한쪽이
  소유 → 다른 쪽은 read-only (raw SQL) 로만 접근.
- **publish 경계**: Job 결과(FastAPI) → 평가 풀(Prisma) 이전은 **Next API route 가
  Prisma transaction 으로 처리**. FastAPI 는 데이터를 노출만 하고 INSERT 안 함.
- **컬럼명 직접 노출 시**: SQLAlchemy 쪽에서 Prisma 의 camelCase 컬럼 (`"sourceText"`,
  `"externalId"`) 을 raw SQL 로 조회할 때 반드시 큰따옴표.

### 6.3 정규화 vs JSON 컬럼 — 판단 기준

이번 작업에서 augmenter contribution payload 를 어디 저장할지 결정한 기준:

> 매칭 결과가 **작고 + 분해 단위가 자연스럽고 + 분석 수요가 있는 것** 만 정규화. 나머지는 ref 만 남기거나 저장 안 함.

| 리소스 | 단위 | 저장 형태 |
|--------|------|----------|
| Glossary | `{source, target}` 작은 쌍 × N | 정규화 (`glossary_matches`) |
| Retrieval (TM) | 긴 문장 쌍 × top_k | 안 저장 또는 S3 ref |
| Graph | 큰 텍스트 블롭 | 안 저장 또는 S3 ref |
| Style Guide | row 무관 동일 | Resource version 참조만 |

정규화 테이블은 FK 인덱스로 분석/통계가 가능하고, JSON 블롭은 "보관" 목적에 적합.
일률 정규화는 retrieval/graph 처럼 큰 payload 에서 RDS 부피만 키운다.

---

# Part II — ML 을 위한 데이터셋 엔지니어링

Part I 은 *파싱 버그* 에 관한 것이다 — 결함 하나, 수정 하나. 한 레이어 위의 작업은
지저분한 다국어 입력을 ML 시스템이 소비하는 **데이터셋·지표·피드백 루프** 로 바꾸는
일이다. Part II 는 그 레이어이며, Part I 과 정확히 같은 primitive 위에 쌓여 있다
(인코딩 규율 #1, lang 코드 alias #2, 스크립트 인지 매칭 #4).

여기 나오는 모든 도구는 **stdlib 만 사용** 하고, 기본으로 Markdown 리포트를 출력하며
(`--format json` 으로 기계용 출력), findings 가 있으면 non-zero 로 종료한다(즉 CI 게이트로
동작). 또 결함을 심어둔 샘플과, 그 결함이 잡히는 것을 증명하는 pytest 를 함께 제공한다.
스위트는 `snippets/` 에서 실행한다:

```bash
python -m pytest tests/ -q      # 16 tests
```

## 7. 코퍼스를 데이터로 감사하기

Part I 은 결함을 하나씩 찾는다. 운영에서는 결코 그런 식으로 들어오지 않는다.
벤더 CSV 하나가 cp949 바이트로, `ko`/`ko-KR` 헤더가 섞인 채, 일본어 컬럼은 절반이
비어 있고, `{player_name}` 이 "번역되어" 버린 row 가 세 개 있는 상태로 도착한다.
[`audit_corpus.py`](./snippets/dataset-quality/audit_corpus.py) 는 **한 번의 패스** 로
이 모두를 row 단위 포인터와 함께 리포트한다. 그래서 데이터 소유자가 소스를 고치게 되고,
당신이 하류에서 증상을 땜질하지 않게 된다.

wide-format 코퍼스(언어당 컬럼 하나) 위에서 다음을 계산한다:

| 체크 | 연결되는 곳 |
|------|--------------|
| 사용된 인코딩 (utf-8 vs cp949), mojibake 셀, U+FFFD | #1 |
| not-NFC 셀 (자모 분리 한글) | #1.4 |
| 혼재된 lang 코드 표기 (`ko` vs `ko-KR`) | #2 |
| 언어별 커버리지 / 누락 번역 | #4 |
| 중복 키 & 소스 값 | — |
| 언어 간 placeholder parity (`{0}`, `%s`, `<br>`) | — |
| base 컬럼 대비 길이 비율 이상치 | — |

```bash
python audit_corpus.py sample_corpus.csv          # markdown report, exit 1 if dirty
python audit_corpus.py corpus.csv --format json --out report
```

감사 도구는 이 책이 설파하는 바를 그대로 실천한다: `utf-8-sig` 로 읽고 cp949 로 폴백하며
(#1.5), `lang_aliases` 규칙을 재사용한다(#2).

같은 코퍼스 지표를 대비를 위해 다른 두 방식으로도 표현한다 — pandas (#8) 와 SQL (#14).
그래서 도구 선택이 기본값이 아니라 의도적인 결정이 된다.

## 8. 같은 지표를 pandas 방식으로

7장은 코퍼스 품질을 표준 라이브러리로 계산했고, #14 는 SQL 로 계산한다. 이것은 세 번째
관점이다: 데이터 분석의 lingua franca 인 pandas. 여기서 보여주는 능력은 pandas 자체가
아니라 — *어떤* 도구가 질문에 맞는지 아는 것이다.

| 관점 | 이럴 때 꺼낸다 | 비용 |
|------|-------------------|------|
| stdlib (#7) | 의존성 0, 스트리밍 가능한 drop-in | 장황함 |
| pandas (여기) | 탐색적 분석, 노트북, 즉석 pivot | 전부 RAM 에 적재 |
| SQL (#14) | 데이터가 이미 Postgres 에 있음 | round-trip |

### 루프가 아니라 벡터화

stdlib 감사 도구는 `Counter` 로 row 를 순회한다. pandas 는 같은 질문을 컬럼 전체 연산으로
표현한다:

```python
nonempty = cells.replace("", pd.NA).notna()
coverage = nonempty.mean()                       # fill rate per language
dup_keys = df[key].str.strip().duplicated().sum()
```

### parity 가 곧 계약

known-good 베이스라인과 대조할 수 없는 도구는 쓸모없다. 그래서 테스트는 pandas 관점이
같은 샘플 위에서 #7 과 *같은 숫자* 를 돌려주는지 단언한다
(`test_pandas_parity_with_stdlib_audit`):

```
coverage ja_JP: stdlib=0.875  pandas=0.875
dup_keys:       stdlib=1      pandas=1
len_outliers:   stdlib=2      pandas=2      PARITY: ALL MATCH
```

### 벡터화 함정

parity 를 맞추는 데 수정 하나가 필요했다. 빈 타깃 셀은 길이가 0 이고, `0 / base` 는 비율
0 이 된다 — 이상치 규칙은 이를 "터무니없이 짧음" 으로 읽고 플래그한다. stdlib 루프는 없는
셀을 건너뛰었지만, 벡터화 버전은 조용히 그것을 셌다. 수정은 빈 값을 `NA` 로 매핑해 누락
셀이 중앙값과 mask 양쪽에서 빠지게 한다:

```python
tgt_len = cells.str.len().replace(0, pd.NA)      # empty -> NA, not 0
```

교훈: 벡터화 코드는 빠르지만 "missing vs zero vs empty" 를 대신 처리해주지 않는다 — 그
구분이 진짜 pandas 작업의 핵심이지, API 암기가 아니다.

### pandas 가 틀린 선택일 때

- 데이터가 RAM 에 안 들어감 -> 뒤에 나올 스케일링 장은 polars(lazy) / duckdb(out-of-core)
  를 쓴다. pandas 는 OOM 난다
- 의존성 0 의 drop-in 이 필요함 -> #7 stdlib
- 데이터가 이미 웨어하우스에 있음 -> #14 SQL

실행 가능: [`snippets/pandas/corpus_metrics_pandas.py`](./snippets/pandas/corpus_metrics_pandas.py).

## 9. 용어집 준수율 — 피드백 루프 닫기

용어집을 배포하는 건 *입력* 이다. 제품이 실제로 신경 쓰는 질문은 *결과* 다: 모델이 그것을
썼는가? [`glossary_adherence.py`](./snippets/glossary-eval/glossary_adherence.py) 는
소스 텍스트, MT 출력, 용어집을 받아서, 세그먼트 × 언어마다 소스에 있는 용어집 용어가
출력에서 그 강제된 번역을 만들어냈는지 검사한다.

```
| language | applicable | applied | adherence |
| ja       | 7          | 4       | 57.1% ⚠️  |   ← actionable: top misses listed
| zh-CN    | 7          | 6       | 85.7%     |
| ko       | 7          | 7       | 100.0%    |
```

이것은 #4 의 평가 쌍둥이다: #4 는 *주입할* 용어를 찾고, 이것은 주입된 용어가 *살아남았는지*
측정한다. 매칭은 스크립트 인지 방식 — 라틴은 `\b`, CJK 는 substring(#4.2). 유창성은
**판단하지 않는다**; 그건 별도의 LLM-as-judge 도구이며 의도적으로 범위 밖이다.

## 10. 매처 변경을 A/B 테스트하기

README #4 는 매칭 전략을 산문으로 나열하는데, 거기서 나쁜 결정이 숨는다. 운영 매처를
교체하기 전에 제품에 숫자 하나를 보여줘야 한다.
[`strategy_ab.py`](./snippets/experiments/strategy_ab.py) 는 각 전략을 **라벨링된 gold
set** 위에서 돌리고 precision / recall / F1 을 리포트한다:

```
| strategy            | precision | recall | F1     | FP | FN |
| word_boundary 🏆    | 100.0%    | 100.0% | 100.0% | 0  | 0  |
| word_boundary+min3  | 100.0%    | 71.4%  | 83.3%  | 0  | 2  |
| substring           | 63.6%     | 100.0% | 77.8%  | 4  | 0  |
```

이제 트레이드오프가 보인다: substring 은 과다 발화하고(`Said` 안의 `AI`), min-length 가드는
2글자 용어를 죽인다(`AI`, `OK`). 이것이 "제품 실험을 설계하고 수행하는" 근육이다: 고정된
control set, 후보 variant, 하나의 지표, 하나의 승자.

## 11. 용어집에서 렉시콘 / 지식 그래프로

플랫 용어집은 "X 를 일본어로 어떻게 말하나" 에만 답하고 그 이상은 못 한다. "어떤 용어가
COMBAT 도메인에 있나" 또는 "戦利品 이 주어졌을 때 그게 무슨 개념이고 그 모든 라벨은
무엇인가" 가 필요해지는 순간, 그래프가 필요하다.
[`build_lexicon.py`](./snippets/knowledge-graph/build_lexicon.py) 는 플랫 CSV 를
SKOS/lexinfo 스타일 트리플을 가진 개념 그래프로 올린다:

```
concept:loot skos:prefLabel "loot"@en .
concept:loot skos:prefLabel "戦利品"@ja .
concept:loot dct:subject domain:Combat .
concept:loot skos:broader concept:item .
```

…더해서 역인덱스도 만든다: 어떤 언어의 어떤 표면형이든 → 그 개념 → 다른 모든 라벨. 그
cross-lingual lookup 이야말로 렉시콘 기반 NLU 레이어나 용어집 augmenter 가 소비하는 것이다.

```bash
python build_lexicon.py glossary_lex.csv --lookup 戦利品   # -> concept 'loot' + all labels
python build_lexicon.py glossary_lex.csv --format triples --out lexicon
```

## 12. 언어 형평성 — 책임 있는 AI 스크린

집계된 품질 숫자는 격차를 숨긴다: "커버리지 92%" 는 모든 언어가 92% 라는 뜻일 수도,
영어가 100% 이고 다섯 언어가 70% 라는 뜻일 수도 있다.
[`coverage_bias.py`](./snippets/responsible-ai/coverage_bias.py) 는 언어별로 분해하고
라벨 없는 프록시로 불평등을 플래그한다 — 커버리지, copy-through(타깃 == 소스),
너무 짧은 stub — 그런 다음 스프레드(최고 대비 격차, 변동계수)를 리포트한다:

```
- coverage gap (best − worst): 50.0% ⚠️
## ⚠️ Underserved languages (>10pp below best)
- `th` — coverage 50.0%; prioritize for data collection
```

샘플은 RTL(아랍어, 히브리어)과 인도계(힌디어) 컬럼을 포함한다. 도구는 또 **자기 지표를
경계한다**: char-length 비교는 밀집 스크립트(CJK)를 과다 플래그하므로, `too-short` 은
within-script 스크린이지 판정이 아니다 — 지표에 대한 자기 인식이 책임 있는 AI 리뷰의
일부다.

## 13. NLU intent + slot 데이터셋 구축

음성 비서 NLU 모델은 라벨링된 발화가 필요하다: 각각 intent 와 slot 스팬으로 태깅된다.
언어당 수천 개를 손으로 쓰면 일관성 없는 offset 과 한쪽으로 치우친 클래스 밸런스를
보장한다. [`build_intent_dataset.py`](./snippets/nlu/build_intent_dataset.py) 는
템플릿 × slot 값을 조합적으로 확장하고 **문자 스팬을 자동 계산** 한다. 그래서 라벨이 항상
정확하다 — CJK 포함:

```jsonl
{"lang":"en","intent":"buy_item","text":"buy two sword",
 "slots":[{"name":"count","value":"two","start":4,"end":7},
          {"name":"item","value":"sword","start":8,"end":13}]}
```

또한 클래스 밸런스를 리포트하고 얇은 `(lang, intent)` 셀에 경고한다. 책의 나머지에 대한
ASR/NLU 쪽 보완물이다.

## 14. 같은 지표를 SQL 로

데이터가 이미 Postgres 에 안착했다면, export 하지 말고 — 쿼리한다.
[`quality_metrics.sql`](./snippets/sql/quality_metrics.sql) 는 Part II 지표를 #6 의
평가 스키마에 대해 표현한다: 언어별 용어집 준수율, 최고 대비 격차가 붙은 언어별 커버리지
(윈도우 함수), lang 코드 표기 drift(#2), mojibake 스크린(#1), copy-through 탐지, 그리고
JSONB `augmenter_log` 로부터의 augmenter 건강도. Prisma 의 camelCase 컬럼은 전부
큰따옴표로 감쌌다(#6.2).

## 15. RAM 에 안 들어가는 코퍼스로 확장하기

Part II 도구는 `rows = list(reader)` 로 코퍼스 전체를 적재하고 `for term in terms` 루프로
용어를 매칭한다. 둘 다 벤더 CSV 에는 괜찮지만, 코퍼스가 수백만 row 에 도달하면 둘 다
무너진다. 이 장은 각각을 확장되는 기법으로 교체한다 — 그리고 이 책의 정신대로, 형용사가
아니라 숫자로 증명한다.

### 시간: Aho-Corasick vs 나이브 루프

나이브 루프는 모든 세그먼트마다 모든 용어를 재스캔한다 — O(terms × text). Aho-Corasick
automaton 은 모든 용어를 한 번 컴파일하고 단일 패스를 한다 — O(text + matches), 용어 수와
무관.

```
| terms | naive (ms) | AC search (ms) | search speed-up | counts agree |
|    50 |        8.5 |           12.1 |            0.7x | yes |
|   200 |       46.0 |           18.0 |            2.6x | yes |
|  1000 |      150.7 |           14.6 |           10.3x | yes |
|  5000 |      912.7 |           26.6 |           34.4x | yes |
```

정직한 crossover 에 주목하라: 50개 용어에서는 나이브 루프가 *이긴다* — automaton 의
build/오버헤드가 아직 상각되지 않았다. 5,000개 용어에 이르면 Aho-Corasick 은 약 34배
빠르고 검색 시간은 거의 평평하게 유지된다. crossover 를 지난 뒤에만 꺼내고, 반사적으로
쓰지 말 것. 실행 가능: [`snippets/benchmark/bench_matching.py`](./snippets/benchmark/bench_matching.py).

### 메모리: Welford vs load-everything

대부분의 체크는 trivial 하게 스트리밍된다: row 를 읽고, 카운터를 올리고, row 를 버린다.
예외는 길이 비율 이상치(#7) 인데, 이건 *중앙값* 이 필요하고 — 중앙값은 모든 값을 한꺼번에
요구한다. Welford 의 online 알고리즘이 그것을 우회한다: running mean 과 variance 를 O(1)
메모리로 유지해, 이상치를 z-score 로 단일 패스에서 플래그한다.

```
| rows      | load peak (KB) | stream peak (KB) |
|    10,000 |            725 |                0 |
|   100,000 |          3,129 |                0 |
| 1,000,000 |         31,691 |                0 |
```

load-everything 의 peak 메모리는 row 수와 함께 오르지만, 스트리밍 peak 은 평평하다 —
얼마나 많은 row 가 지나가든 float 세 개. 실행 가능:
[`snippets/benchmark/stream_vs_load.py`](./snippets/benchmark/stream_vs_load.py).

진짜로 O(1) 이 될 수 없는 단 하나의 체크는 **정확한 중복 탐지** 다 — 본 키를 전부 기억해야
한다. 그 경계에 대해 정직할 것: O(distinct keys) 메모리를 받아들이거나, 먼저 외부 정렬을
하거나, 근사 자료구조(Bloom filter)를 쓴다. stateful 체크를 stateless 인 척하지 말 것.

### 한 머신을 넘어서: 컬럼형 엔진

RAM 에 안 들어가는 데이터 위에서 진짜 분석 쿼리(group-by, join, percentile)를 원할 때는,
스트리밍 aggregator 를 손으로 짜는 걸 멈추고 디스크에서 스트리밍하는 컬럼형 엔진을
꺼내라 — DuckDB(파일 위 SQL) 또는 polars(lazy DataFrame). 이것은 #8(pandas) 과 #14(SQL)
의 운영 단계 확장이다:

```python
duckdb.execute('''
  SELECT lang, AVG(CASE WHEN trim(text) <> '' THEN 1.0 ELSE 0 END) AS coverage
  FROM read_csv(?, header=true) GROUP BY lang''', [path])   # streams from disk
```

[`snippets/scale/out_of_core.py`](./snippets/scale/out_of_core.py) 는 같은 커버리지를 세
방식으로 계산하고 — stdlib 스트림, DuckDB, polars lazy — 그것들이 일치하는지 단언한다.
DuckDB/polars 는 선택적 무거운 의존성이다(#4 의 `pyahocorasick` 처럼); stdlib 경로는
항상 실행된다.

### 결정 규칙

| 상황 | 꺼낼 것 |
|-----------|-----------|
| 용어 수 crossover 를 지남 | Aho-Corasick (#4) |
| 스트리밍 통계, 단일 패스 | Welford z-score (중앙값 말고) |
| RAM 보다 큼 + 분석 쿼리 | DuckDB / polars |
| 아직 RAM 에 들어감 | 더 단순한 #7 / #8 도구 |

## 16. 그 차이는 진짜였나? A/B 의 유의성

10장은 깔끔한 표를 만들었다 — `word_boundary` 100%, `substring` 78% — 그리고 거기서 멈췄다.
하지만 표는 점추정이고, gold 13건에서 그 차이는 운일 수 있다. 이 장은 "A/B 돌려봤다"를
방어 가능한 주장으로 바꾸는 두 가지를 더한다: 유의성 검정과 신뢰구간.
`strategy_ab.py --significance` 로 실행한다.

### McNemar: 두 전략이 실제로 다른가

두 전략을 *같은* 항목들에서 비교하는 건 paired 문제라, 평범한 2-표본 검정은 틀린 도구다.
McNemar 는 *불일치(discordant)* 항목 — 한 전략은 맞고 다른 전략은 틀린 — 만 보고, 그 분할이
동전 던지기일 수 있는지 묻는다. 카이제곱 근사는 hand-label gold 가 흔히 갖는 작은 n 에서
신뢰할 수 없으므로 정확(exact) 이항 형태를 쓴다.

### 같은 차이, 두 표본 크기

n = 13 (10장의 셋):

```
word_boundary vs 차순위
불일치: 2 vs 0  ->  exact p = 0.50  ->  유의하지 않음
```

n = 120 (같은 종류의 케이스, 더 많이):

```
word_boundary vs substring
불일치: 54 vs 0  ->  exact p < 0.0001  ->  유의함
```

똑같아 보이는 승자, 반대의 결론. 바뀐 건 증거의 양뿐이다. "A/B 돌렸다"가 n 과 p 를 달기
전까지는 결과가 아닌 이유다.

### 부트스트랩 CI: 각 숫자는 얼마나 넓은가

p-value 는 "다른가 아닌가"를, 신뢰구간은 "각 추정이 얼마나 확실한가"를 답한다. gold 항목을
복원추출로 리샘플하고, 매번 F1 을 다시 계산해, 가운데 95% 를 취한다:

```
| substring F1 | 95% CI         | n   |
|        71.0% | [62.9%, 77.6%] | 120 |   좁음 — 신뢰 가능
|        77.8% | [50.0%, 95.2%] |  13 |   45 포인트 폭 — 사실상 무의미
```

넓은 구간은 결정을 내리기 전에 데이터를 더 모으라는 정직한 신호다.

### 핵심

승자는 하나가 아니라 셋으로 보고한다: 점추정, 신뢰구간, paired 유의성 검정. 첫째만 보고하는
게 주니어, 셋 다 보고하는 게 방어 가능한 판단이다. 실행:
[`snippets/experiments/strategy_ab.py`](./snippets/experiments/strategy_ab.py) `--significance`.

---

# 부록 (Appendix)

## A. 작업 시 주의사항 체크리스트

### A.1 다국어 텍스트 받기 전에

- [ ] 어디서 디코딩되는가? (브라우저? 서버? 둘 다?)
- [ ] 기본 인코딩 가정은 무엇인가? UTF-8 외 입력은 어떻게 처리?
- [ ] BOM 처리는?
- [ ] NFC/NFD 정규화 필요한가? (macOS 파일명, 한글 자모 분리)

### A.2 lang 코드 다룰 때

- [ ] base 코드 (`ko`)와 locale 코드 (`ko-KR`)가 같은 코드 경로에서 섞이는가?
- [ ] alias 등록 또는 정규화 전략이 있는가?
- [ ] `zh-CN` / `zh-TW` 같은 region 분리가 필요한 케이스를 다루는가?
- [ ] 대소문자? underscore vs hyphen?

### A.3 용어 매칭 로직 만들 때

- [ ] CJK 와 라틴 스크립트의 단어 경계 차이를 인지하는가?
- [ ] 1글자 용어 노이즈 가드는?
- [ ] 부분 매칭 우선순위(최장 매칭)는?
- [ ] 정규화 단계(공백, 개행, HTML 태그) 있는가?
- [ ] 성능: 용어 수 × 세그먼트 수 곱이 1M 넘어가면 자료구조 재고.

### A.4 파일 업로드/다운로드 만들 때

- [ ] 업로드: 원시 바이트로 받고 인코딩 감지 후 디코딩 (브라우저는 `File.text()` 금지)
- [ ] 다운로드: `Content-Disposition` 에 `filename*=UTF-8''...` 같이
- [ ] Excel 호환 export 면 BOM 붙이기
- [ ] presigned URL 사용 시 Content-Type 매칭

### A.5 두 DB 경계 작업할 때

- [ ] 마이그레이션 소유권은 명확한가?
- [ ] 한쪽이 캐싱한 데이터의 stale 검증 시점은?
- [ ] publish/sync 작업이 idempotent 인가? (재실행 안전)
- [ ] 트랜잭션 경계가 두 영역에 걸치는가? (FastAPI 쓰고 Next 가 후처리면 부분 실패 처리)

### A.6 평가/디버깅 데이터 저장할 때

- [ ] 정규화 가치가 있는가? (분석, 인덱싱, 통계)
- [ ] payload 크기는? (row 당 KB 단위면 정규화 다시 고려)
- [ ] retention 정책은? (Job 결과 90일 후 삭제 등)
- [ ] PII 포함 가능성은?

### A.7 locale별 콘텐츠 파일 resolve 할 때

- [ ] locale 변종(`*.en.mdx`)이 base 목록에서 제외되는가?
- [ ] 변종이 없으면 base 파일로 폴백하는가?
- [ ] frontmatter 는 필드 단위 merge, 본문은 통째로 교체인가?
- [ ] base 파생 필드(썸네일, 읽기 시간)를 merge 결과가 아니라 base 에서 다시 계산하는가?
- [ ] 변종의 빈 값이 멀쩡한 base 값을 덮어쓰지 않는가?

---

## B. 스니펫 인덱스

위의 모든 패턴은 [`snippets/`](./snippets/) 에 실행 가능한 대응물이 있다. 파일 하나를 골라
다른 프로젝트에 그대로 떨어뜨려 쓰면 된다 — self-contained.

| 증상 | 스니펫 |
|---------|---------|
| 브라우저로 업로드한 한글 CSV → S3 에서 mojibake | [`encoding/read-text-smart.browser.ts`](./snippets/encoding/read-text-smart.browser.ts) |
| 같은 문제, Python 백엔드가 로컬 파일 읽기 | [`encoding/read_text_smart.py`](./snippets/encoding/read_text_smart.py) |
| export 한 CSV 가 Excel 에서 깨짐 | [`encoding/csv-export-with-bom.ts`](./snippets/encoding/csv-export-with-bom.ts) |
| 화면에 mojibake, 그 출처를 알고 싶음 | [`debug/mojibake_trace.py`](./snippets/debug/mojibake_trace.py) |
| 용어집 CSV 는 `ko-KR`, Job 은 `ko` (0 매칭) | [`lang-codes/lang_aliases.py`](./snippets/lang-codes/lang_aliases.py) |
| 여러 lang-tag 표기가 DB 를 오염시킴 | [`lang-codes/normalize_lang.py`](./snippets/lang-codes/normalize_lang.py) |
| `*.en.mdx` 가 중복 글로 뜸 / EN 글이 썸네일을 잃음 | [`locale-content/resolve-locale-content.ts`](./snippets/locale-content/resolve-locale-content.ts) |
| 매처가 "Said" 안의 "AI" 를 잡음 | [`glossary-matching/word_boundary_match.py`](./snippets/glossary-matching/word_boundary_match.py) |
| 용어 수천 개에서 용어 루프가 너무 느림 | [`glossary-matching/aho_corasick_match.py`](./snippets/glossary-matching/aho_corasick_match.py) |
| Firefox/Safari 다운로드에서 한글 파일명 깨짐 | [`download/content-disposition-rfc5987.ts`](./snippets/download/content-disposition-rfc5987.ts) |
| 큰 CSV export 가 timeout | [`download/streaming-csv-export.ts`](./snippets/download/streaming-csv-export.ts) |
| 빠른 분류: "이 파일 인코딩이 뭐지?" | [`debug/inspect-file-encoding.ps1`](./snippets/debug/inspect-file-encoding.ps1) / [`.sh`](./snippets/debug/inspect-file-encoding.sh) |
| 스케일에서 용어 루프가 너무 느림 — crossover 증명 | [`benchmark/bench_matching.py`](./snippets/benchmark/bench_matching.py) |
| 코퍼스가 RAM 에 안 들어감 — 스트리밍(O(1)) vs load | [`benchmark/stream_vs_load.py`](./snippets/benchmark/stream_vs_load.py) |
| RAM 보다 큼 + 분석 쿼리 (DuckDB/polars) | [`scale/out_of_core.py`](./snippets/scale/out_of_core.py) |
| 이 책을 만들며 부딪힌 버그 재현 | [`debug/error_cases.py`](./snippets/debug/error_cases.py) |

### Field notes — 이 책을 만들며 부딪힌 버그

이 책의 모든 함정은 실제로 배포되고, 부딪히고, 고쳐졌다. Part II 를 만드는 것도 예외가
아니었고, 우리가 부딪힌 여러 버그는 이 책 *자신의* 교훈이 되받아친 것이었다. 각각은
실행 가능하게 재현되어 있다(깨진 동작 옆에 수정) —
[`snippets/debug/error_cases.py`](./snippets/debug/error_cases.py).

1. **#1 을 증명한 콘솔.** 도구가 리포트를 출력할 때
   `UnicodeEncodeError: 'cp949' codec can't encode '—'` — Windows 콘솔이 cp949 다. 수정:
   `sys.stdout.reconfigure(encoding="utf-8")`, 이제 모든 Part II CLI 에 들어가 있다.
2. **mojibake 를 놓친 mojibake 정규식.** 첫 패턴은 `Ã`/`Â` 선두만 매칭했지만,
   Korean-UTF-8-as-latin1 은 `ë`(U+00EB)에서 시작한다. 수정: 진짜 시그니처를 매칭 —
   선두 U+00C2–00F4 + 연속 U+0080–00BF.
3. **dataclasses + importlib.** 경로로 도구를 로드하니 `AttributeError: 'NoneType'
   object has no attribute '__dict__'` 발생 — `@dataclass` 는 annotation 을
   `sys.modules[cls.__module__]` 로 resolve 한다. 수정: `exec_module` 전에
   `sys.modules` 에 등록.
4. **pandas 가 빈 셀을 이상치로 셈.** 빈 셀의 길이 0 은 비율 0 을 주고 → 거짓 "too-short"
   (#8). 수정: 비율 전에 빈 값 → `NA` 매핑.
5. **polars 가 "" 를 null 로 읽음.** null 로 만든 boolean 이 group mean 을 왜곡한다.
   수정: 먼저 `fill_null("")`.
6. **충분히 랜덤하지 않은 LCG.** 그 *낮은* 비트가 6-언어 사이클과 상관되어 → 퇴화된 0/1
   커버리지. 수정: *높은* 비트를 쓴다.
7. **두 Python 프로세스 사이의 파이핑.** surrogate-escape 된 바이트가 파이프를 가로질러
   샜다(`'\udceb' ... surrogates not allowed`). 수정: 한 프로세스로, 또는 둘 다
   `PYTHONIOENCODING` — 인코딩 경계(#1)는 당신 자신의 프로세스 사이에도 존재한다.

---

## C. 디버깅 명령 모음

### 파일 인코딩 의심될 때

```powershell
# PowerShell — 첫 바이트들 보기
$bytes = [System.IO.File]::ReadAllBytes("path.csv")
$bytes[0..20] | ForEach-Object { "{0:X2}" -f $_ }
# 0xEF 0xBB 0xBF → UTF-8 BOM
# 0xE0~ 3바이트 한글 → UTF-8
# 0x80~0xFE 2바이트 한글 → cp949
```

```bash
# Unix
file path.csv                       # 인코딩 추정
hexdump -C path.csv | head -2       # 첫 바이트 확인
iconv -f cp949 -t utf-8 in.csv > out.csv  # 변환
```

→ 미리 만든 스크립트: [`snippets/debug/inspect-file-encoding.ps1`](./snippets/debug/inspect-file-encoding.ps1) 와 [`.sh`](./snippets/debug/inspect-file-encoding.sh).

### 글로서리 매칭이 비었을 때

```sql
-- Job 의 source/target lang 확인
SELECT id, config_snapshot->>'source_lang' FROM translation_jobs ORDER BY created_at DESC LIMIT 5;
SELECT DISTINCT target_lang FROM job_rows WHERE job_id = '<id>';

-- augmenter 가 정말 호출됐는지
SELECT augmenter_log FROM job_rows WHERE job_id = '<id>' LIMIT 5;
-- "requested": ["glossary"] 가 있는데 "glossary_terms": [] 면 매칭 0건
```

CSV 헤더의 lang 코드와 위 결과가 같은 표기인지 비교. 다른 표기(`ko` vs `ko-KR`)면 #2 참고.

### mojibake 인코딩 역추적

```python
# 깨진 텍스트의 원본 인코딩을 추정
mojibake = "ë³´íµ"
for enc in ["latin-1", "cp1252", "cp949", "utf-8"]:
    try:
        recovered = mojibake.encode(enc).decode("utf-8")
        print(enc, "→", recovered)
    except Exception:
        pass
```

`latin-1 → 보통` 같이 의미 있는 한글이 나오면 그게 원래 인코딩. CLI 버전:
[`snippets/debug/mojibake_trace.py`](./snippets/debug/mojibake_trace.py).

---

## D. 참고 자료

- [WHATWG Encoding Standard](https://encoding.spec.whatwg.org/) — `TextDecoder` 지원 인코딩 목록
- [RFC 5987](https://datatracker.ietf.org/doc/html/rfc5987) — Content-Disposition 다국어 파일명
- [BCP 47](https://datatracker.ietf.org/doc/html/rfc5646) — 언어 태그 표준
- [Unicode UAX #15](https://unicode.org/reports/tr15/) — Normalization Forms (NFC/NFD)
- [SKOS](https://www.w3.org/TR/skos-reference/) — Simple Knowledge Organization System (prefLabel / altLabel / broader), #11 에서 사용
- [lemon / lexinfo](https://lemon-model.net/) — 온톨로지용 렉시콘 모델(품사·어휘 메타데이터), #11 에서 사용

---

## E. 자주 쓰는 코드 조각

### E.1 인코딩 자동 감지 (Browser)

```ts
async function readTextSmart(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  const bytes = new Uint8Array(buf);
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    return new TextDecoder("utf-8").decode(bytes.slice(3));
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return new TextDecoder("euc-kr").decode(bytes);
  }
}
```

### E.2 인코딩 자동 감지 (Python, charset-normalizer)

```python
from charset_normalizer import from_bytes

def read_text_smart(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    result = from_bytes(raw).best()
    if result is None:
        return raw.decode("utf-8", errors="replace")
    return str(result)
```

내장만 쓰고 싶으면:

```python
try:
    text = raw.decode("utf-8")
except UnicodeDecodeError:
    text = raw.decode("cp949")
```

### E.3 lang 코드 alias

```python
def lang_aliases(lang: str) -> list[str]:
    norm = lang.replace("_", "-")
    base = norm.split("-", 1)[0]
    return [norm, base] if base and base != norm else [norm]
```

### E.4 RFC 5987 Content-Disposition

```ts
function contentDisposition(filename: string): string {
  const fallback = filename.replace(/[^\x20-\x7e]/g, "_");
  const encoded = encodeURIComponent(filename).replace(/['()*]/g, (c) =>
    "%" + c.charCodeAt(0).toString(16).toUpperCase(),
  );
  return `attachment; filename="${fallback}"; filename*=UTF-8''${encoded}`;
}
```

### E.5 UTF-8 BOM 붙여 CSV export

```ts
return new Response("﻿" + csvBody, {
  headers: {
    "Content-Type": "text/csv; charset=utf-8",
    "Content-Disposition": contentDisposition(`${name}.csv`),
  },
});
```

### E.6 locale별 콘텐츠 resolve (base 폴백 + 부분 override)

```ts
function getPost(slug: string, locale: string): Post {
  const base = parseBase(`${slug}.mdx`);                 // 항상 존재
  if (locale === DEFAULT_LOCALE) return base;
  const variantPath = `${slug}.${locale}.mdx`;
  if (!existsSync(variantPath)) return base;             // 변종 없으면 base
  const variant = matter(readFileSync(variantPath, "utf-8"));
  return {
    ...base,                                             // 썸네일 등 base 파생값 유지
    frontmatter: { ...base.frontmatter, ...stripEmpty(variant.data) },
    content: variant.content,                            // 본문은 통째로 교체
  };
}
```

---

## F. 실제로 부딪힌 이슈 타임라인 (translation-eval)

| 커밋 | 무엇이 깨졌나 | 어떻게 고쳤나 |
|------|--------------|---------------|
| `0549400` | 큰 Job 다운로드 시 timeout | parallel page fetch + maxDuration 증가 |
| `65afd64` | 한글 파일명 다운로드 깨짐 | RFC 5987 `filename*` |
| `59d1850` | cp949 CSV → S3 에 mojibake 저장 | `TextDecoder` BOM/UTF-8/EUC-KR fallback |
| `6e81668` | `ko-KR` CSV ↔ `ko` ctx 매칭 0건 | alias 등록 + 조회 폴백 |
| `433e1dd` | 평가에서 어떤 용어가 적용됐는지 안 보임 | 정규화 테이블 `glossary_matches` + 평가 UI 칩 |
| `7fc06c9` | 다국어 Job 에서 TMX 인덱스 캐시 충돌 | `(src,tgt)` per 캐시 키 |
| `ac0260e` | xlsx 입력 처리 안 됨 | wizard 가 첫 시트 자동 파싱 |
| `21ae7bd` | CSV 컬럼이 `한국어` 같은 자유 헤더 | per-column 매핑 UI + AI 자동 매핑 |

각 항목은 위 1·2·4·5·6장 어느 한 챕터의 변종 (3장은 다른 프로젝트 출처).

---

## G. 안 한 것 / 미해결

- 글로서리 substring 매칭의 단어경계 가드 (#4.2)
- `case_sensitive` 옵션 spec 전달 (#4.3)
- 최장 매칭 우선순위
- retrieval / graph payload 의 S3 ref 저장
- NFD/NFC 정규화 (현재까지 문제 없음)
- 평가 시점에서 "용어집 위반 여부" 자동 체크 (UI 표시만 함)
