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
-- AUTHOR:  Jonah Knief (i6263747)
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
        -- Employed (catches: 'employed', 'EMPLOYED', ' Employed', 'Empl0yed')
        WHEN UPPER(TRIM(REGEXP_REPLACE(employment_status, '[^a-zA-Z\s\-]', 'e', 'g')))
             LIKE '%EMPLOY%'
         AND UPPER(TRIM(employment_status)) NOT LIKE '%SELF%'
         AND UPPER(TRIM(employment_status)) NOT LIKE '%UN%'
            THEN 'Employed'
        -- Self-Employed (catches: 'self-employed', 'SELF-EMPLOYED', 'Self Employed')
        WHEN UPPER(TRIM(employment_status)) LIKE '%SELF%'
            THEN 'Self-Employed'
        -- Unemployed (catches: 'Unemployed', 'UNEMPLOYED', ' Unemployed')
        WHEN UPPER(TRIM(employment_status)) LIKE '%UNEMPLOY%'
            THEN 'Unemployed'
        -- Student (catches: 'Student', 'student', ' Student', 'STUDENT')
        WHEN UPPER(TRIM(employment_status)) LIKE '%STUDENT%'
            THEN 'Student'
        -- Retired (catches: 'Retired', 'RETIRED', 'retired')
        WHEN UPPER(TRIM(employment_status)) LIKE '%RETIR%'
            THEN 'Retired'
        ELSE 'Unknown'
    END                                                             AS employment_status,

    -- -------------------------------------------------------------------------
    -- EDUCATION LEVEL NORMALISATION
    -- Source has 35 unique variants due to casing, extra spaces, abbreviations.
    -- Strategy: normalise to 5 canonical degree levels.
    -- Ordering of WHEN clauses matters — more specific patterns first.
    --
    -- Canonical values: Doctorate | Master | Bachelor | Associate | High School
    -- -------------------------------------------------------------------------
    CASE
        -- Doctorate (catches: 'Doctorate', 'DOCTORATE', 'PhD', 'Ph.D')
        WHEN UPPER(TRIM(education_level)) LIKE '%DOCT%'
          OR UPPER(TRIM(education_level)) LIKE '%PHD%'
            THEN 'Doctorate'
        -- Master (catches: 'Master Degree', 'Masters', 'MS/MA', 'Master  Degree')
        WHEN UPPER(TRIM(education_level)) LIKE '%MASTER%'
          OR UPPER(TRIM(education_level)) LIKE '%MS/MA%'
          OR UPPER(TRIM(education_level)) LIKE 'MS'
          OR UPPER(TRIM(education_level)) LIKE 'MA'
            THEN 'Master'
        -- Bachelor (catches: 'Bachelor Degree', "Bachelor's Degree", 'BACHELOR DEGREE',
        --                    'Bachelor  Degree' (double space))
        WHEN UPPER(TRIM(education_level)) LIKE '%BACH%'
            THEN 'Bachelor'
        -- Associate (catches: 'Associate Degree', 'ASSOCIATE DEGREE')
        WHEN UPPER(TRIM(education_level)) LIKE '%ASSOC%'
            THEN 'Associate'
        -- High School (catches: 'High School', 'HIGH SCHOOL', 'high school')
        WHEN UPPER(TRIM(education_level)) LIKE '%HIGH%'
          OR UPPER(TRIM(education_level)) LIKE '%H.S%'
            THEN 'High School'
        ELSE 'Unknown'
    END                                                             AS education_level

FROM raw.users

WHERE id IS NOT NULL;

CREATE INDEX idx_stg_users_client_id ON dw.stg_users (client_id);