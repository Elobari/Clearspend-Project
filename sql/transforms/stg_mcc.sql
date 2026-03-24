-- =============================================================================
-- FILE:    stg_mcc.sql
-- PURPOSE: Clean and standardise raw.mcc -> dw.stg_mcc
--
-- TRANSFORMATIONS APPLIED:
--   1. code:        Strip surrounding quotes and 'MCC' prefix -> INTEGER
--   2. description: Strip leading/trailing whitespace -> title case
--
-- WHY THIS MATTERS:
--   The mcc code is the join key between transactions and the reference table.
--   If we don't clean '"3000"' and 'MCC3066' to integer 3000 and 3066,
--   every MCC join in the transformation layer will silently fail, leaving
--   all merchant category descriptions as NULL in the warehouse.
--
-- DEDUPLICATION:
--   DISTINCT ON (mcc_code) keeps exactly one row per code after cleaning.
--   This handles source rows where different format variants of the same
--   code (e.g. '3504' and '"3504"') would otherwise produce duplicates.
--   These are purely formatting artefacts of the same code, not distinct
--   business entities, so collapsing them is appropriate in the transform layer.
--
-- UNPARSEABLE ROWS:
--   Rows whose code cannot be cleaned to a valid integer (e.g. 'NOTE', blank)
--   are excluded here via the WHERE filter. These rows are structurally
--   unusable — they can never join to transactions.mcc — and are written to
--   raw.rejected by transform.py immediately after this script runs.
--
-- AUTHOR:  Jonah Knief (i6263747) | Artem Vysotskyi (...) | Lyan Eleraky (...) | Loredana Lazari
-- COURSE:  Data Engineering and Data Compliance
-- UNI:     Maastricht University
-- =============================================================================

DROP TABLE IF EXISTS dw.stg_mcc;

CREATE TABLE dw.stg_mcc AS
--SELECT DISTINCT ON (mcc_code) mcc_code, mcc_description
--FROM (
    SELECT
        -- -----------------------------------------------------------------------
        -- MCC CODE NORMALISATION
        -- Source formats observed:
        --   Plain integer:   '1711', '3005'
        --   Quoted string:   '"3000"', '"3001"', '"3260"', '"3390"'
        --   MCC-prefixed:    'MCC3066', 'MCC3359', 'MCC3387'
        --
        -- Strategy:
        --   Step 1: Remove all double-quote characters
        --   Step 2: Remove the 'MCC' prefix (case-insensitive)
        --   Step 3: Strip any remaining whitespace
        --   Step 4: Cast to INTEGER for clean joins to transactions.mcc
        -- -----------------------------------------------------------------------
        REGEXP_REPLACE(
            REGEXP_REPLACE(
                REPLACE(TRIM(code), '"', ''),
                '^[Mm][Cc][Cc]', ''
            ),
            '\s', '', 'g'
        )::INTEGER                          AS mcc_code,

        -- -----------------------------------------------------------------------
        -- DESCRIPTION NORMALISATION
        -- Source issues: mixed case (ALL CAPS, lowercase, Title Case),
        --                leading/trailing whitespace (e.g. ' Steelworks')
        -- Strategy: TRIM whitespace, then INITCAP for consistent title case
        -- -----------------------------------------------------------------------
        INITCAP(TRIM(description))          AS mcc_description

    FROM raw.mcc

    -- Exclude rows where the code cannot be cleaned to a valid integer.
    -- These are NOT silently dropped -- transform.py reads these same rows
    -- after this script runs and writes them to raw.rejected for traceability.
    WHERE code IS NOT NULL
      AND TRIM(code) != ''
      AND REGEXP_REPLACE(
            REGEXP_REPLACE(
              REPLACE(TRIM(code), '"', ''),
              '^[Mm][Cc][Cc]', ''
            ),
            '\s', '', 'g'
          ) ~ '^\d+$'
--) cleaned
ORDER BY mcc_code;

CREATE INDEX idx_stg_mcc_code ON dw.stg_mcc (mcc_code);