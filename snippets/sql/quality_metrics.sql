-- quality_metrics.sql
-- ---------------------------------------------------------------------------
-- The same dataset-quality questions the Python tools answer, expressed in
-- SQL against the evaluation database described in README #6. Use these when
-- the data already landed in Postgres and you want metrics without exporting:
-- dashboards, scheduled checks, ad-hoc triage.
--
-- Schema recap (README #6.1):
--   FastAPI side : translation_jobs, job_rows, resources, resource_versions
--   Prisma side  : text_segments, translations, evaluations, glossary_matches
--
-- Prisma uses camelCase identifiers, so they MUST be double-quoted when read
-- from SQL -- Postgres folds unquoted identifiers to lowercase (README #6.2).
-- Tested on PostgreSQL 14+. Window functions and FILTER are used throughout.
-- ===========================================================================


-- 1. Glossary adherence by language (the SQL twin of glossary_adherence.py)
--    "Of the glossary terms that were matched into a segment, how many made
--     it into the published translation?"  Outcome metric for the augmenter.
-- ---------------------------------------------------------------------------
SELECT
    t."targetLang"                                            AS lang,
    COUNT(*)                                                  AS applicable,
    COUNT(*) FILTER (WHERE t."text" ILIKE '%' || gm."targetTerm" || '%')
                                                             AS applied,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE t."text" ILIKE '%' || gm."targetTerm" || '%')
        / NULLIF(COUNT(*), 0)
    , 1)                                                      AS adherence_pct
FROM glossary_matches gm
JOIN translations t          ON t."segmentId" = gm."segmentId"
                            AND t."targetLang" = gm."targetLang"
GROUP BY t."targetLang"
ORDER BY adherence_pct ASC;   -- worst-served language first


-- 2. Per-language coverage / equity (the SQL twin of coverage_bias.py)
--    Fill rate of translations per language, plus the gap from the best one.
-- ---------------------------------------------------------------------------
WITH per_lang AS (
    SELECT
        tr."targetLang"                                      AS lang,
        COUNT(*)                                             AS n_segments,
        COUNT(*) FILTER (WHERE COALESCE(TRIM(tr."text"), '') <> '')
                                                            AS n_filled
    FROM translations tr
    GROUP BY tr."targetLang"
)
SELECT
    lang,
    n_filled,
    n_segments,
    ROUND(100.0 * n_filled / NULLIF(n_segments, 0), 1)       AS coverage_pct,
    ROUND(100.0 * (
        MAX(1.0 * n_filled / NULLIF(n_segments, 0)) OVER ()
        - 1.0 * n_filled / NULLIF(n_segments, 0)
    ), 1)                                                    AS gap_from_best_pp
FROM per_lang
ORDER BY coverage_pct ASC;


-- 3. Lang-code form drift (README #2) -- detect base/locale spellings of the
--    SAME language coexisting, which silently breaks key lookups.
-- ---------------------------------------------------------------------------
SELECT
    split_part(replace("targetLang", '_', '-'), '-', 1)      AS base_lang,
    ARRAY_AGG(DISTINCT "targetLang" ORDER BY "targetLang")   AS forms_seen,
    COUNT(DISTINCT "targetLang")                             AS n_forms
FROM translations
GROUP BY 1
HAVING COUNT(DISTINCT "targetLang") > 1                       -- only conflicts
ORDER BY n_forms DESC;


-- 4. Suspected mojibake rows (README #1) -- a cheap SQL screen for the
--    UTF-8-decoded-as-latin1 signature before a deeper Python pass.
-- ---------------------------------------------------------------------------
SELECT id, "targetLang", LEFT("text", 60) AS sample
FROM translations
WHERE "text" ~ '[Â-ô][-¿]+'   -- mojibake run
   OR "text" LIKE '%' || U&'\fffd' || '%'          -- U+FFFD replacement char
LIMIT 100;


-- 5. Untranslated copy-through -- target equals source verbatim, a common
--    "looks done, isn't" defect that inflates coverage numbers.
-- ---------------------------------------------------------------------------
SELECT
    tr."targetLang",
    COUNT(*) FILTER (WHERE TRIM(tr."text") = TRIM(ts."sourceText"))  AS copy_through,
    COUNT(*)                                                          AS total,
    ROUND(100.0 * COUNT(*) FILTER (WHERE TRIM(tr."text") = TRIM(ts."sourceText"))
          / NULLIF(COUNT(*), 0), 1)                                  AS copy_pct
FROM translations tr
JOIN text_segments ts ON ts.id = tr."segmentId"
GROUP BY tr."targetLang"
HAVING COUNT(*) FILTER (WHERE TRIM(tr."text") = TRIM(ts."sourceText")) > 0
ORDER BY copy_pct DESC;


-- 6. Augmenter health from the FastAPI side -- did the glossary augmenter
--    actually run and return matches? (job_rows.augmenter_log is JSONB.)
--    Cross-stack read: FastAPI-owned table, queried read-only (README #6.2).
-- ---------------------------------------------------------------------------
SELECT
    jr.job_id,
    COUNT(*)                                                          AS rows_total,
    COUNT(*) FILTER (WHERE jr.augmenter_log -> 'requested' ? 'glossary')
                                                                     AS glossary_requested,
    COUNT(*) FILTER (WHERE jsonb_array_length(
        COALESCE(jr.augmenter_log -> 'glossary_terms', '[]'::jsonb)) = 0
        AND jr.augmenter_log -> 'requested' ? 'glossary')            AS zero_match_rows
FROM job_rows jr
GROUP BY jr.job_id
ORDER BY zero_match_rows DESC
LIMIT 20;
-- zero_match_rows high while glossary_requested high => the #2 lang-code miss.
