-- =============================================================================
-- FILE:    stg_users.sql
-- PURPOSE: Clean and standardise raw.users → dw.stg_users
--
-- TRANSFORMATIONS APPLIED:
--   1. per_capita_income, yearly_income, total_debt:
--         Strip '$' and commas, handle 'k' suffix → NUMERIC(12,2)
--   2. employment_status:
--         Map 32 messy variants → 5 canonical values
--   3. education_level:
--         Map 35 messy variants → 5 canonical values
--   4. gender: normalise casing
--
-- AUTHOR:  Jonah Knief (i6263747) | Artem Vysotskyi (...) | Lyan Eleraky (...) | Loredana Lazari
-- COURSE:  Data Engineering and Data Compliance
-- UNI:     Maastricht University
-- =============================================================================

DROP TABLE IF EXISTS dw.stg_users;

CREATE TABLE dw.stg_users AS

SELECT
    id              AS client_id,   -- Natural key — matches client_id in transactions
    current_age,
    retirement_age,
    birth_year,
    birth_month,

    -- -------------------------------------------------------------------------
    -- GENDER NORMALISATION
    -- Ensure consistent capitalisation
    -- -------------------------------------------------------------------------
    INITCAP(TRIM(gender))                                           AS gender,

    address,
    latitude,
    longitude,
    credit_score,
    num_credit_cards,

    -- -------------------------------------------------------------------------
    -- MONETARY FIELDS
    -- Source formats observed:
    --   Standard:   "$59696", "$1,234.56"
    --   k-suffix:   "56k", "1.5k"  (shorthand for thousands)
    -- Strategy:
    --   1. Detect 'k' suffix (case-insensitive)
    --   2. If present: strip non-numeric chars (except '.'), cast, multiply by 1000
    --   3. Otherwise:  strip '$' and commas, cast directly
    -- -------------------------------------------------------------------------
    CASE
        WHEN UPPER(REGEXP_REPLACE(per_capita_income, '[$,\s]', '', 'g')) LIKE '%K'
        THEN (REGEXP_REPLACE(per_capita_income, '[$,Kk\s]', '', 'g')::NUMERIC(12,2)) * 1000
        ELSE REGEXP_REPLACE(per_capita_income, '[$,]', '', 'g')::NUMERIC(12,2)
    END                                                             AS per_capita_income,

    CASE
        WHEN UPPER(REGEXP_REPLACE(yearly_income, '[$,\s]', '', 'g')) LIKE '%K'
        THEN (REGEXP_REPLACE(yearly_income, '[$,Kk\s]', '', 'g')::NUMERIC(12,2)) * 1000
        ELSE REGEXP_REPLACE(yearly_income, '[$,]', '', 'g')::NUMERIC(12,2)
    END                                                             AS yearly_income,

    CASE
        WHEN UPPER(REGEXP_REPLACE(total_debt, '[$,\s]', '', 'g')) LIKE '%K'
        THEN (REGEXP_REPLACE(total_debt, '[$,Kk\s]', '', 'g')::NUMERIC(12,2)) * 1000
        ELSE REGEXP_REPLACE(total_debt, '[$,]', '', 'g')::NUMERIC(12,2)
    END                                                             AS total_debt,

    -- -------------------------------------------------------------------------
    -- EMPLOYMENT STATUS NORMALISATION
    -- Source has 32 unique variants due to casing, whitespace, and typos.
    -- Strategy: UPPER(TRIM()) first, then pattern-match to canonical values.
    -- Unmapped values fall through to 'Unknown' to avoid data loss.
    --
    -- Canonical values: Employed | Self-Employed | Student | Retired | Unemployed
    -- -------------------------------------------------------------------------


    CASE 
        WHEN LEFT(TRIM(UPPER(employment_status)), 2) = 'EM' THEN 'Employed'
        WHEN LEFT(TRIM(UPPER(employment_status)), 2) = 'SE' THEN 'Self-Employed'
        WHEN LEFT(TRIM(UPPER(employment_status)), 2) = 'UN' THEN 'Unemployed'
        WHEN LEFT(TRIM(UPPER(employment_status)), 2) = 'ST' THEN 'Student'
        WHEN LEFT(TRIM(UPPER(employment_status)), 3) = 'RET' THEN 'Retired'
        ELSE 'Unknown'
    END AS employment_status,

    -- -------------------------------------------------------------------------
    -- EDUCATION LEVEL NORMALISATION
    -- Source has 35 unique variants due to casing, extra spaces, abbreviations.
    -- Strategy: normalise to 5 canonical degree levels.
    -- Ordering of WHEN clauses matters — more specific patterns first.
    --
    -- Canonical values: Doctorate | Master | Bachelor | Associate | High School
    -- -------------------------------------------------------------------------
    CASE 
        WHEN LEFT(TRIM(UPPER(education_level)), 1) = 'D' THEN 'Doctorate'
        WHEN LEFT(TRIM(UPPER(education_level)), 1) = 'P' THEN 'Doctorate' -- Catches PhD, Ph.D
        WHEN LEFT(TRIM(UPPER(education_level)), 1) = 'M' THEN 'Master'    -- Catches Master, MS, MA
        WHEN LEFT(TRIM(UPPER(education_level)), 1) = 'B' THEN 'Bachelor'
        WHEN LEFT(TRIM(UPPER(education_level)), 1) = 'A' THEN 'Associate'
        WHEN LEFT(TRIM(UPPER(education_level)), 1) = 'H' THEN 'High School'
        ELSE 'Unknown'
    END AS education_level

FROM raw.users

WHERE id IS NOT NULL;

CREATE INDEX idx_stg_users_client_id ON dw.stg_users (client_id);
