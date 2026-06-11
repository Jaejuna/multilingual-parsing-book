# 다국어 파싱 플레이북

> 영어 버전: [README.md](./README.md) (primary)
> 실행 가능한 코드: [`snippets/`](./snippets/) — 패턴별 파일 분리, 영어 주석

게임 로컬라이제이션 평가 플랫폼(translation-eval) 작업 중 다국어 텍스트·CSV·용어집(TB)·번역
파이프라인을 다루며 정리한 함정과 해결 패턴을 한곳에 모은 문서. 다른 프로젝트로 가지고 가도
독립적으로 읽힐 수 있도록 self-contained 형태로 정리.

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
  │     - substring 매칭                 ← #3 부분문자열·단어경계
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

## 3. 용어집 매칭 (Glossary Matching) — substring 의 함정

### 3.1 현재 동작

```python
haystack = text if case_sensitive else text.lower()
for src, lang_map in src_dict.items():
    needle = src if case_sensitive else src.lower()
    if needle in haystack:        # 단순 substring
        matched.append({"source": src, "target": lang_map[target]})
```

장점: 빠르고 단순. CJK 처럼 단어 경계가 없는 언어에도 동작.
단점: 영어에서 `"AI"` 가 `"said"`, `"again"` 에도 매칭. `"AI"` 와 `"AI Director"` 가 동시에 매칭.

### 3.2 가능한 개선 (적용은 미정)

1. **언어별 분기**: 라틴 스크립트면 `\b{term}\b` 단어 경계 강제, CJK 면 substring 허용.
2. **min_len 가드**: 1글자 용어는 노이즈가 많으니 무시 (`len(term) < 2` skip).
3. **최장 매칭 우선**: `"AI Director"` 가 매칭됐다면 그 안의 `"AI"` 는 스킵.
4. **정규화**: `<br>`, 개행, 다중 공백 → 단일 공백.
5. **Aho-Corasick**: 용어 수천 개 × 세그먼트 수천 개 환경에서 O(N+M) 으로 줄임.

레거시 `module/glossary.py` 의 `extract_glossary_terms_fuzzy` 는 (2)(4) 를 이미 했지만
신규 `app/augmenters/glossary.py` 는 단순화하면서 빠짐. 필요하면 옮겨오면 됨.

### 3.3 case sensitivity 전파 누락 (실제 버그)

`GlossaryAugmenter.config_schema` 는 `case_sensitive` 옵션을 받지만,
`resource_resolver._spec_for_glossary` 는 `file_ref` 만 spec 으로 전달한다. UI/Resource
에서 옵션을 켜도 worker 에서는 항상 기본값(False). 옵션을 살리려면 ResourceVersion.meta
에서 읽어 spec 에 추가 필요.

---

## 4. 파일 다운로드 — RFC 5987 Content-Disposition

### 4.1 문제

한글 파일명을 `Content-Disposition: attachment; filename="용어집.csv"` 로 넣으면
브라우저별로:
- Chrome: 잘 받아짐
- Firefox: 깨진 파일명
- Safari: 다운로드 거부 또는 mojibake

### 4.2 해결

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

### 4.3 export 시 BOM 도 같이

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

## 5. 두 ORM, 한 Postgres — 경계 다루기

### 5.1 구조

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

### 5.2 원칙

- **마이그레이션 소유권**: 같은 테이블에 두 ORM 이 마이그레이션 걸지 않는다. 한쪽이
  소유 → 다른 쪽은 read-only (raw SQL) 로만 접근.
- **publish 경계**: Job 결과(FastAPI) → 평가 풀(Prisma) 이전은 **Next API route 가
  Prisma transaction 으로 처리**. FastAPI 는 데이터를 노출만 하고 INSERT 안 함.
- **컬럼명 직접 노출 시**: SQLAlchemy 쪽에서 Prisma 의 camelCase 컬럼 (`"sourceText"`,
  `"externalId"`) 을 raw SQL 로 조회할 때 반드시 큰따옴표.

### 5.3 정규화 vs JSON 컬럼 — 판단 기준

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

## 6. 작업 시 주의사항 체크리스트

### 6.1 다국어 텍스트 받기 전에

- [ ] 어디서 디코딩되는가? (브라우저? 서버? 둘 다?)
- [ ] 기본 인코딩 가정은 무엇인가? UTF-8 외 입력은 어떻게 처리?
- [ ] BOM 처리는?
- [ ] NFC/NFD 정규화 필요한가? (macOS 파일명, 한글 자모 분리)

### 6.2 lang 코드 다룰 때

- [ ] base 코드 (`ko`)와 locale 코드 (`ko-KR`)가 같은 코드 경로에서 섞이는가?
- [ ] alias 등록 또는 정규화 전략이 있는가?
- [ ] `zh-CN` / `zh-TW` 같은 region 분리가 필요한 케이스를 다루는가?
- [ ] 대소문자? underscore vs hyphen?

### 6.3 용어 매칭 로직 만들 때

- [ ] CJK 와 라틴 스크립트의 단어 경계 차이를 인지하는가?
- [ ] 1글자 용어 노이즈 가드는?
- [ ] 부분 매칭 우선순위(최장 매칭)는?
- [ ] 정규화 단계(공백, 개행, HTML 태그) 있는가?
- [ ] 성능: 용어 수 × 세그먼트 수 곱이 1M 넘어가면 자료구조 재고.

### 6.4 파일 업로드/다운로드 만들 때

- [ ] 업로드: 원시 바이트로 받고 인코딩 감지 후 디코딩 (브라우저는 `File.text()` 금지)
- [ ] 다운로드: `Content-Disposition` 에 `filename*=UTF-8''...` 같이
- [ ] Excel 호환 export 면 BOM 붙이기
- [ ] presigned URL 사용 시 Content-Type 매칭

### 6.5 두 DB 경계 작업할 때

- [ ] 마이그레이션 소유권은 명확한가?
- [ ] 한쪽이 캐싱한 데이터의 stale 검증 시점은?
- [ ] publish/sync 작업이 idempotent 인가? (재실행 안전)
- [ ] 트랜잭션 경계가 두 영역에 걸치는가? (FastAPI 쓰고 Next 가 후처리면 부분 실패 처리)

### 6.6 평가/디버깅 데이터 저장할 때

- [ ] 정규화 가치가 있는가? (분석, 인덱싱, 통계)
- [ ] payload 크기는? (row 당 KB 단위면 정규화 다시 고려)
- [ ] retention 정책은? (Job 결과 90일 후 삭제 등)
- [ ] PII 포함 가능성은?

---

## 7. 자주 쓰는 코드 조각

### 7.1 인코딩 자동 감지 (Browser)

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

### 7.2 인코딩 자동 감지 (Python, charset-normalizer)

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

### 7.3 lang 코드 alias

```python
def lang_aliases(lang: str) -> list[str]:
    norm = lang.replace("_", "-")
    base = norm.split("-", 1)[0]
    return [norm, base] if base and base != norm else [norm]
```

### 7.4 RFC 5987 Content-Disposition

```ts
function contentDisposition(filename: string): string {
  const fallback = filename.replace(/[^\x20-\x7e]/g, "_");
  const encoded = encodeURIComponent(filename).replace(/['()*]/g, (c) =>
    "%" + c.charCodeAt(0).toString(16).toUpperCase(),
  );
  return `attachment; filename="${fallback}"; filename*=UTF-8''${encoded}`;
}
```

### 7.5 UTF-8 BOM 붙여 CSV export

```ts
return new Response("﻿" + csvBody, {
  headers: {
    "Content-Type": "text/csv; charset=utf-8",
    "Content-Disposition": contentDisposition(`${name}.csv`),
  },
});
```

---

## 8. 실제로 부딪힌 이슈 타임라인 (translation-eval)

| 커밋 | 무엇이 깨졌나 | 어떻게 고쳤나 |
|------|--------------|---------------|
| `0549400` | 큰 Job 다운로드 시 timeout | parallel page fetch + maxDuration 증가 |
| `65afd64` | 한글 파일명 다운로드 깨짐 | RFC 5987 `filename*` |
| `59d1850` | cp949 CSV → S3 에 mojibake 저장 | `TextDecoder` BOM/UTF-8/EUC-KR fallback |
| `6e81668` | `ko-KR` CSV ↔ `ko` ctx 매칭 0건 | alias 등록 + 조회 폴백 |
| `433e1dd` | 평가에서 어떤 용어가 적용됐는지 안 보임 | 정규화 테이블 `glossary_matches` + 평가 UI 칩 |
| `7fc06c9` | 다국어 Job 에서 TMX 인덱스 캐시 충돌 | `(src,tgt)` per 캐시 키 |
| `ac0260e` | xlsx 입력 미지원 | wizard 가 첫 시트 자동 파싱 |
| `21ae7bd` | CSV 컬럼이 `한국어` 같은 자유 헤더 | per-column 매핑 UI + AI 자동 매핑 |

각 항목은 위 1~5장 어느 한 챕터의 변종.

---

## 9. 안 한 것 / 미해결

- 글로서리 substring 매칭의 단어경계 가드 (#3.2)
- `case_sensitive` 옵션 spec 전달 (#3.3)
- 최장 매칭 우선순위
- retrieval / graph payload 의 S3 ref 저장
- NFD/NFC 정규화 (현재까지 문제 없음)
- 평가 시점에서 "용어집 위반 여부" 자동 체크 (UI 표시만 함)

---

## 10. 디버깅 명령 모음

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

### 글로서리 매칭이 비었을 때

```sql
-- Job 의 source/target lang 확인
SELECT id, config_snapshot->>'source_lang' FROM translation_jobs ORDER BY created_at DESC LIMIT 5;
SELECT DISTINCT target_lang FROM job_rows WHERE job_id = '<id>';

-- augmenter 가 정말 호출됐는지
SELECT augmenter_log FROM job_rows WHERE job_id = '<id>' LIMIT 5;
-- "requested": ["glossary"] 가 있는데 "glossary_terms": [] 면 매칭 0건
```

CSV 헤더의 lang 코드와 위 결과가 같은 표기인지 비교.

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

`latin-1 → 보통` 같이 의미 있는 한글이 나오면 그게 원래 인코딩.

---

## 11. 참고 자료

- [WHATWG Encoding Standard](https://encoding.spec.whatwg.org/) — `TextDecoder` 지원 인코딩 목록
- [RFC 5987](https://datatracker.ietf.org/doc/html/rfc5987) — Content-Disposition 다국어 파일명
- [BCP 47](https://datatracker.ietf.org/doc/html/rfc5646) — 언어 태그 표준
- [Unicode UAX #15](https://unicode.org/reports/tr15/) — Normalization Forms (NFC/NFD)
