/**
 * csv-export-with-bom.ts
 *
 * Pattern: emit a UTF-8 CSV that Excel on Korean Windows will open
 * without mojibake.
 *
 * The problem
 * -----------
 * A CSV that is "valid UTF-8" is not necessarily a CSV that Excel opens
 * correctly. When you double-click a `.csv` file, Excel on Korean
 * Windows ignores any HTTP `charset` parameter and reads the bytes using
 * the system code page (cp949). Korean characters then render as
 * mojibake — but only for the user, not for VSCode / Notepad / a Python
 * server, so the bug looks like "it works on my machine."
 *
 * Excel changes its behaviour the moment the file starts with a UTF-8
 * BOM (three bytes: 0xEF 0xBB 0xBF). With a BOM, Excel correctly reads
 * the file as UTF-8, regardless of the system code page.
 *
 * Server-side parsers handle this transparently:
 *   * Python's ``utf-8-sig`` codec strips the BOM.
 *   * JavaScript's ``TextDecoder('utf-8')`` strips the BOM.
 *   * The ``csv`` Python module + ``utf-8-sig`` keeps the first column
 *     header clean.
 *
 * So adding a BOM is a one-way improvement for Excel users and invisible
 * to everyone else.
 */

import { contentDispositionRfc5987 } from "../download/content-disposition-rfc5987";

const UTF8_BOM = "﻿";

/**
 * Build a Response that streams a UTF-8 CSV with a BOM and a
 * cross-browser-safe Content-Disposition header.
 *
 * @param csvBody Raw CSV text WITHOUT a leading BOM. The function adds
 *                one. Don't double-add — that would produce ``﻿﻿``
 *                at the start of the file, which Excel renders as a stray
 *                character in the first cell.
 * @param filename The display filename. Non-ASCII characters are
 *                 handled by ``contentDispositionRfc5987`` (filename* +
 *                 ASCII fallback).
 */
export function csvDownloadResponse(csvBody: string, filename: string): Response {
  // Defensive: if the caller already prepended a BOM, leave it alone.
  // Excel only needs ONE BOM; two would show up as an empty leading
  // cell in some viewers.
  const body = csvBody.startsWith(UTF8_BOM) ? csvBody : UTF8_BOM + csvBody;

  return new Response(body, {
    headers: {
      // ``charset=utf-8`` is informative — browsers and Excel mostly
      // rely on the BOM, but specifying it doesn't hurt and helps the
      // occasional spec-following client.
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": contentDispositionRfc5987(filename),
    },
  });
}

/**
 * Lower-level helper if you're not in a Web API ``Response`` environment
 * (e.g. writing to a Node.js stream).
 *
 * Returns a ``Uint8Array`` because the BOM + body must be sent as bytes,
 * not as a JS string that some downstream serialiser might re-encode.
 */
export function csvBytesWithBom(csvBody: string): Uint8Array {
  const encoder = new TextEncoder();
  const body = csvBody.startsWith(UTF8_BOM) ? csvBody : UTF8_BOM + csvBody;
  return encoder.encode(body);
}

/**
 * Tiny CSV row builder — quotes fields containing commas, quotes or
 * newlines and escapes embedded quotes by doubling them, per RFC 4180.
 *
 * Inlined here so this snippet is fully standalone; replace with your
 * favourite CSV library in real code.
 */
export function csvRow(fields: readonly string[]): string {
  return fields
    .map((f) => {
      if (/[",\r\n]/.test(f)) {
        return '"' + f.replace(/"/g, '""') + '"';
      }
      return f;
    })
    .join(",");
}
