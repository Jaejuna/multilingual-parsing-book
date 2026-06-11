/**
 * content-disposition-rfc5987.ts
 *
 * Pattern: build a `Content-Disposition` header that survives non-ASCII
 * filenames across all major browsers.
 *
 * Why the naive form doesn't work
 * -------------------------------
 *   Content-Disposition: attachment; filename="용어집.csv"
 *
 * Different browsers do different things with the raw UTF-8 bytes:
 *   * Chrome   : usually displays them correctly, but only because of
 *                its own UTF-8 sniffing heuristic.
 *   * Firefox  : displays mojibake — it treats the value as Latin-1.
 *   * Safari   : may refuse the download or save with a garbled name.
 *
 * RFC 5987 defines a way to spell out the encoding explicitly:
 *
 *   filename*=UTF-8''<percent-encoded-utf8>
 *
 * Browsers that understand this header (all modern ones) use it.
 * Older clients that only know `filename=` keep working because we
 * also send an ASCII-safe fallback. There is no downside to sending
 * both.
 */

/**
 * Percent-encode according to RFC 5987's `ext-value` production.
 *
 * `encodeURIComponent` is close but leaves some characters unencoded
 * (apostrophe, parens, asterisk) that RFC 5987 wants encoded. We do
 * those extras manually so the resulting value is strictly compliant.
 */
function rfc5987Encode(value: string): string {
  return encodeURIComponent(value).replace(
    /['()*]/g,
    (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase(),
  );
}

/**
 * Build a `Content-Disposition: attachment` header value for a filename
 * that may contain non-ASCII characters.
 *
 * Output shape:
 *
 *   attachment; filename="<ascii-fallback>"; filename*=UTF-8''<encoded>
 *
 * The fallback strips characters outside printable ASCII to underscores,
 * which keeps very old clients happy without leaking encoding bugs
 * (e.g. a Latin-1 client wouldn't render those bytes correctly anyway).
 *
 * @example
 *   contentDispositionRfc5987("용어집.csv")
 *   // → 'attachment; filename="___.csv"; filename*=UTF-8''%EC%9A%A9%EC%96%B4%EC%A7%91.csv'
 */
export function contentDispositionRfc5987(filename: string): string {
  // ASCII fallback: replace anything outside printable ASCII so old
  // clients see SOMETHING valid. Underscore is conventional. Quotes
  // inside the filename would break the `filename="..."` form so we
  // also strip them defensively.
  const fallback = filename
    .replace(/[^\x20-\x7e]/g, "_")
    .replace(/["\\]/g, "_");

  const encoded = rfc5987Encode(filename);

  return `attachment; filename="${fallback}"; filename*=UTF-8''${encoded}`;
}

/**
 * Variant that uses `inline` instead of `attachment`. Same encoding
 * rules — the browser just decides whether to render inline (PDFs,
 * images) or prompt to download.
 */
export function contentDispositionInline(filename: string): string {
  return contentDispositionRfc5987(filename).replace(
    /^attachment/,
    "inline",
  );
}
