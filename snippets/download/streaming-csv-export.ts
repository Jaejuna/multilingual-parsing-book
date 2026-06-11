/**
 * streaming-csv-export.ts
 *
 * Pattern: stream a large CSV download instead of building the whole
 * body in memory.
 *
 * Why streaming matters here
 * --------------------------
 * Translation jobs can produce ~100k rows. Building the entire CSV in
 * memory, then sending it as a single Response body:
 *
 *   * doubles the peak memory footprint (string + outbound buffer),
 *   * delays the first byte by however long the CSV build takes,
 *   * times out on platforms with hard request-duration limits
 *     (Vercel Edge: 25s; Lambda: 15min hard cap; varies elsewhere),
 *   * forces a single SELECT-everything query that may exhaust the
 *     database connection's memory.
 *
 * A streaming response writes header → BOM → rows as they're fetched.
 * The client sees data immediately, peak memory is bounded by one
 * page's worth of rows, and the request stays under any wall-clock
 * limit as long as the source DB can keep up.
 *
 * Caveats
 * -------
 *   * You give up `Content-Length`. Browsers handle this fine — they
 *     show "downloading..." without a progress bar. Don't pre-compute
 *     a length unless you really need it.
 *   * The BOM has to be the FIRST bytes the stream emits, or Excel
 *     won't pick it up. Don't flush a header row before the BOM.
 *   * If `fetchPage` throws partway through, you've already sent some
 *     bytes — the user gets a truncated file. Log the partial write
 *     and consider adding a sentinel row the importer can detect.
 */

import { contentDispositionRfc5987 } from "./content-disposition-rfc5987";

const UTF8_BOM_BYTES = new Uint8Array([0xef, 0xbb, 0xbf]);

export interface StreamingCsvOptions<TRow> {
  /** Display filename for `Content-Disposition`. */
  filename: string;
  /** Header row (column names). Emitted once, after the BOM. */
  header: readonly string[];
  /**
   * Async page fetcher. Called repeatedly with monotonically increasing
   * `page` indices starting at 0. Return an empty array to signal EOF.
   *
   * Keep pages reasonably small (a few hundred to a few thousand
   * rows). The right size depends on row width and DB roundtrip cost.
   */
  fetchPage: (page: number) => Promise<readonly TRow[]>;
  /** Convert one row to CSV-ready string fields, in header order. */
  toFields: (row: TRow) => readonly string[];
}

/**
 * Build a streaming `Response` that emits a UTF-8 CSV with a BOM.
 */
export function streamingCsvResponse<TRow>(
  opts: StreamingCsvOptions<TRow>,
): Response {
  const encoder = new TextEncoder();

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        // 1) BOM — must be the first bytes for Excel to detect UTF-8.
        controller.enqueue(UTF8_BOM_BYTES);

        // 2) Header row.
        controller.enqueue(encoder.encode(csvRow(opts.header) + "\n"));

        // 3) Pages — yield each row immediately so the client can start
        //    processing while the next page is being fetched.
        for (let page = 0; ; page++) {
          const rows = await opts.fetchPage(page);
          if (rows.length === 0) break;
          for (const row of rows) {
            controller.enqueue(
              encoder.encode(csvRow(opts.toFields(row)) + "\n"),
            );
          }
        }

        controller.close();
      } catch (err) {
        // Propagate errors so the platform can log them. The client
        // sees a truncated download — that's the best we can do once
        // bytes have started flowing.
        controller.error(err);
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": contentDispositionRfc5987(opts.filename),
      // Hint to proxies not to buffer the whole stream before forwarding.
      "X-Accel-Buffering": "no",
      "Cache-Control": "no-store",
    },
  });
}

/**
 * RFC 4180-compliant single-row CSV encoder. Inlined so this file has
 * no external CSV dependency.
 */
function csvRow(fields: readonly string[]): string {
  return fields
    .map((f) => {
      if (/[",\r\n]/.test(f)) {
        return '"' + f.replace(/"/g, '""') + '"';
      }
      return f;
    })
    .join(",");
}
