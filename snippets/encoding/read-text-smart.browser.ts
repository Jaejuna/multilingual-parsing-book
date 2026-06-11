/**
 * read-text-smart.browser.ts
 *
 * Pattern: encoding-aware text reader for a browser-side File / Blob.
 *
 * Why this exists
 * ---------------
 * The Web API `Blob.text()` (and therefore `File.text()`) ALWAYS decodes
 * its bytes as UTF-8. There is no parameter to choose a different encoding.
 * This is fine for files produced by modern tooling, but Excel on Korean
 * Windows still saves CSVs as cp949 (a.k.a. EUC-KR / windows-949) by default.
 * Reading those bytes through `file.text()` silently yields mojibake.
 *
 * If you then turn that mojibake string back into a Blob and upload it,
 * the broken bytes are persisted to S3 / the server forever. The fix is
 * to read the raw bytes first and pick the right decoder.
 *
 * Detection strategy
 * ------------------
 *   1. Strip a UTF-8 BOM if present (Excel sometimes writes one).
 *   2. Try strict UTF-8 (`fatal: true`) — if the bytes form valid UTF-8,
 *      they almost certainly ARE UTF-8.
 *   3. Otherwise fall back to euc-kr. `TextDecoder("euc-kr")` is a WHATWG
 *      standard label and decodes the full cp949 / windows-949 superset on
 *      modern browsers, so this covers Korean-Windows Excel exports.
 *
 * Limitations
 * -----------
 *   - Only handles UTF-8 and EUC-KR (cp949). For Shift_JIS / GB18030 / etc.
 *     either add more fallbacks or expose an encoding dropdown in the UI.
 *   - "Valid UTF-8" is a heuristic — a short cp949 file can technically
 *     also be valid UTF-8 by coincidence. In practice this almost never
 *     happens for real CSV content, but if you need certainty, ask the
 *     user.
 */

export async function readTextSmart(file: Blob): Promise<string> {
  const buf = await file.arrayBuffer();
  const bytes = new Uint8Array(buf);

  // 1) Strip UTF-8 BOM if present so it doesn't end up inside the first column.
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    return new TextDecoder("utf-8").decode(bytes.slice(3));
  }

  // 2) Strict UTF-8 attempt. `fatal: true` throws on invalid sequences,
  //    which lets us cleanly differentiate UTF-8 from cp949.
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    // 3) Fall back to euc-kr. Covers cp949 / windows-949 — the encoding
    //    Korean-Windows Excel uses when "Save As CSV" is chosen.
    return new TextDecoder("euc-kr").decode(bytes);
  }
}

/**
 * Helper for when you have already pulled raw bytes (e.g. from `fetch`)
 * and want the same detection behaviour without a `Blob`.
 */
export function decodeTextSmart(bytes: Uint8Array): string {
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    return new TextDecoder("utf-8").decode(bytes.slice(3));
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return new TextDecoder("euc-kr").decode(bytes);
  }
}
