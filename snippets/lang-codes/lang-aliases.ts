/**
 * lang-aliases.ts
 *
 * Pattern: bridge "locale" lang codes (`ko-KR`) with "base" lang codes
 * (`ko`) so a lookup keyed on one form still hits when the data is
 * keyed on the other.
 *
 * See ../lang-codes/lang_aliases.py for the longer rationale; this file
 * is the TypeScript twin used in browser/Node code.
 */

/**
 * Expand a language tag to `[exact, base]` (or just `[exact]`).
 *
 *   langAliases("ko-KR") // → ["ko-KR", "ko"]
 *   langAliases("ko_KR") // → ["ko-KR", "ko"]   (underscore normalised)
 *   langAliases("ko")    // → ["ko"]            (already base)
 *   langAliases("")      // → [""]              (defensive)
 *
 * Returning an array (rather than a single normalised string) is
 * intentional: callers can register under both forms or try them in
 * order at lookup time without losing the region distinction
 * (`zh-CN` vs `zh-TW`).
 */
export function langAliases(lang: string): string[] {
  const norm = lang.replace(/_/g, "-");
  const base = norm.split("-", 1)[0];
  return base && base !== norm ? [norm, base] : [norm];
}

/**
 * Insert `value` under every alias of `lang`. Mutates `store`.
 * Last write wins when two locales share a base (e.g. `zh-CN` then
 * `zh-TW` both stamp `zh`). For glossary-style fallback that's fine;
 * for region-sensitive data, prefer the exact key only.
 */
export function registerWithAliases<T>(
  store: Map<string, T>,
  lang: string,
  value: T,
): void {
  for (const key of langAliases(lang)) {
    store.set(key, value);
  }
}

/**
 * Look up `lang` in `store`, trying exact form first then base.
 * Returns `undefined` (not null) to match the Map.get convention.
 */
export function lookupWithAliases<T>(
  store: Map<string, T>,
  lang: string,
): T | undefined {
  for (const key of langAliases(lang)) {
    const hit = store.get(key);
    if (hit !== undefined) return hit;
  }
  return undefined;
}
