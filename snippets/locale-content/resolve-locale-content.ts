/**
 * resolve-locale-content.ts
 *
 * Pattern: per-locale content resolution with partial frontmatter fallback.
 *
 * Why this exists
 * ---------------
 * File-based content sites (MDX/Markdown blogs, docs) often ship ONE base
 * file per slug plus OPTIONAL per-locale override files:
 *
 *     content/my-post.mdx        ← base (ko): full frontmatter + body + thumbnail
 *     content/my-post.en.mdx     ← en override: frontmatter only, often partial
 *
 * The base file is the source of truth. A locale file overrides SOME
 * frontmatter fields and replaces the body — it is NOT a standalone post.
 * That single rule is the source of two recurring bugs:
 *
 *   (a) The variant counted as a separate post. A naive
 *       `files.filter(f => f.endsWith(".mdx"))` lists `my-post.en.mdx` as
 *       its own entry, so the index renders the post twice.
 *
 *   (b) Blind spread erases inherited fields. The thumbnail lives only in
 *       the base; the `.en.mdx` file has no `thumbnail` key. A blind
 *       `{ ...base, ...variant }` is fine — UNTIL an author writes
 *       `thumbnail:` with a blank value, which then overrides the good base
 *       value with nothing. Fields the locale file doesn't own must come
 *       from the BASE, not from the merged object.
 *
 * Strategy (mirrors the lang-code alias rule in ../lang-codes/)
 * -------------------------------------------------------------
 *   1. LIST base files only — `*.<locale>.mdx` are variants, excluded.
 *   2. RESOLVE one slug for a locale: base is the floor, the locale file
 *      overrides on top. Exact (locale) wins; missing variant → base.
 *   3. Frontmatter merges field-by-field; the BODY is replaced wholesale,
 *      never merged. Drop empty variant keys so a blank doesn't clobber base.
 *
 * Notes
 * -----
 *   - Real projects parse frontmatter with `gray-matter`
 *     (`const { data, content } = matter(raw)`). To stay dependency-free and
 *     runnable, this file inlines a tiny YAML-subset parser — swap it for
 *     gray-matter in production.
 *   - The "filesystem" here is an in-memory Map so the demo runs anywhere
 *     (`npx tsx resolve-locale-content.ts`). In real code, replace
 *     `FILES.get(name)` with `fs.readFileSync` and `FILES.has(name)` with
 *     `fs.existsSync`.
 */

const DEFAULT_LOCALE = "ko";
const LOCALE_RE = /\.(en|ja|zh)\.mdx$/; // recognised variant suffixes

export interface Post {
  slug: string;
  frontmatter: Record<string, string>;
  content: string;
  /** Derived from the BASE file, never from a locale variant. */
  thumbnail?: string;
}

/** Minimal `gray-matter` stand-in: split `--- yaml --- body`. */
function parseMatter(raw: string): { data: Record<string, string>; content: string } {
  const match = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(raw);
  if (!match) return { data: {}, content: raw };
  const data: Record<string, string> = {};
  for (const line of match[1].split("\n")) {
    const i = line.indexOf(":");
    if (i === -1) continue;
    data[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return { data, content: match[2] };
}

/** Drop keys whose value is empty/whitespace so they don't override base. */
function stripEmpty(data: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(data).filter(([, v]) => v.trim() !== ""));
}

/** First image URL in the body — a field the locale file does NOT carry. */
function extractThumbnail(content: string): string | undefined {
  return /!\[[^\]]*]\(([^)]+)\)/.exec(content)?.[1];
}

function parseBase(slug: string, raw: string): Post {
  const { data, content } = parseMatter(raw);
  return { slug, frontmatter: data, content, thumbnail: extractThumbnail(content) };
}

/**
 * Stage 1 — list posts. Base files only; `*.en.mdx` etc. are variants and
 * must NOT appear as their own entries.
 */
export function getAllPosts(files: Map<string, string>): Post[] {
  return [...files.keys()]
    .filter((name) => name.endsWith(".mdx") && !LOCALE_RE.test(name))
    .map((name) => parseBase(name.replace(/\.mdx$/, ""), files.get(name)!));
}

/**
 * Stage 2 — resolve one slug for a locale. Base is the floor; the locale
 * file overrides frontmatter (partially) and replaces the body.
 */
export function getPost(files: Map<string, string>, slug: string, locale: string): Post {
  const base = parseBase(slug, files.get(`${slug}.mdx`)!); // base always exists
  if (locale === DEFAULT_LOCALE) return base;

  const variantRaw = files.get(`${slug}.${locale}.mdx`);
  if (variantRaw === undefined) return base; // no variant → fall back to base

  const variant = parseMatter(variantRaw);
  return {
    ...base, // thumbnail (and any other base-derived field) survives
    frontmatter: { ...base.frontmatter, ...stripEmpty(variant.data) },
    content: variant.content, // body fully replaced, never merged
  };
}

// --- demo -----------------------------------------------------------------
// Run: `npx tsx resolve-locale-content.ts`
if (typeof require !== "undefined" && require.main === module) {
  const FILES = new Map<string, string>([
    [
      "my-post.mdx",
      `---\ntitle: 첫 글\nsummary: 한국어 본문\n---\n![cover](/img/cover.png)\n\n# 안녕`,
    ],
    // EN variant: overrides title/summary, but has NO thumbnail and a BLANK summary line
    ["my-post.en.mdx", `---\ntitle: First Post\nsummary:\n---\n# Hello`],
    ["solo-post.mdx", `---\ntitle: 단일 언어 글\n---\nko only`],
  ]);

  const posts = getAllPosts(FILES);
  console.log("listed slugs:", posts.map((p) => p.slug)); // ['my-post', 'solo-post'] — variant excluded

  const en = getPost(FILES, "my-post", "en");
  console.log("en.title    :", en.frontmatter.title); // 'First Post' (overridden)
  console.log("en.summary  :", en.frontmatter.summary); // '한국어 본문' (blank ignored → base kept)
  console.log("en.thumbnail:", en.thumbnail); // '/img/cover.png' (inherited from base)
  console.log("en.body     :", en.content.trim()); // '# Hello' (body replaced)

  const missing = getPost(FILES, "solo-post", "en");
  console.log("fallback    :", missing.frontmatter.title); // '단일 언어 글' (no variant → base)
}
