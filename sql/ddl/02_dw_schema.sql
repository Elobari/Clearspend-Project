-- =============================================================================
-- FILE:    02_dw_schema.sql
-- PURPOSE: Define the DATA WAREHOUSE (dw) schema — the clean star schema.
--
-- DESIGN PHILOSOPHY (Adamson, Star Schema: The Complete Reference):
--   A star schema consists of one central fact table surrounded by dimension
--   tables. The fact table stores measurable business events (transactions).
--   Dimension tables store the descriptive context (who, what, when, where).
--
--   Key design decisions made here:
--   1. SURROGATE KEYS: Every dimension uses a SERIAL surrogate key as its PK.
--      This decouples the warehouse from source system ID changes and is the
--      star schema standard. Natural/source keys are retained as separate columns.
--   2. SCD TYPE 2: dim_customers implements Slowly Changing Dimension Type 2
--      on income and employment fields. New rows are inserted when these change,
--      with valid_from/valid_to/is_current tracking the history. This enables
--      accurate point-in-time customer lifetime value calculations.
--   3. GRAIN: fact_transactions has a grain of one row per individual transaction
--      (one card swipe or one online purchase). This is the lowest grain
--      available from the source and gives maximum analytical flexibility.
--   4. FOREIGN KEYS: All FK constraints are defined to enforce referential
--      integrity between fact and dimension tables.
--
-- AUTHOR:  Jonah Knief (i6263747) | Artem Vysotskyi (...) | Lyan Eleraky (...) | Loredana Lazari
-- COURSE:  Data Engineering and Data Compliance
-- UNI:     Maastricht University
-- =============================================================================


-- -----------------------------------------------------------------------------
-- SCHEMA SETUP
-- -----------------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS dw;

-- =============================================================================
-- DIMENSION TABLES
-- Dimensions are created before the fact table because the fact table's
-- foreign key constraints reference them.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- DIMENSION: dw.dim_date
--
-- A fully pre-populated calendar dimension covering 2000–2035.
-- Pre-populating (rather than deriving at query time) is a star schema best
-- practice — it allows BI tools to filter and group by any calendar attribute
-- without complex date functions in every query.
--
-- SURROGATE KEY: date_sk is an integer in YYYYMMDD format (e.g. 20100115).
--   This is a deliberate choice over SERIAL: it makes the key human-readable
--   and allows a direct cast from a date column without a lookup join.
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS dw.dim_date CASCADE;
CREATE TABLE dw.dim_date (
    date_sk         INTEGER       PRIMARY KEY,
    full_date       DATE          NOT NULL UNIQUE,
    year            SMALLINT      NOT NULL,
    quarter         SMALLINT      NOT NULL,     -- 1-4
    month           SMALLINT      NOT NULL,     -- 1-12
    month_name      VARCHAR(10)   NOT NULL,     -- 'January' etc. — no trailing spaces (FMMonth)
    iso_year        SMALLINT      NOT NULL,     -- ISO 8601 year — use with week, not year
    week            SMALLINT      NOT NULL,     -- ISO week 1-53 — always pair with iso_year
    day_of_month    SMALLINT      NOT NULL,     -- 1-31
    day_of_week     SMALLINT      NOT NULL,     -- 1 (Mon) - 7 (Sun)
    day_name        VARCHAR(10)   NOT NULL,     -- 'Monday' etc. — no trailing spaces (FMDay)
    is_weekend      BOOLEAN       NOT NULL,
    is_month_start  BOOLEAN       NOT NULL,
    is_month_end    BOOLEAN       NOT NULL
    );

-- FMMonth / FMDay: FM prefix suppresses padding spaces.
-- Without FM: TO_CHAR('2010-01-01', 'Month') = 'January   ' (10 chars, padded).
-- With FM:    TO_CHAR('2010-01-01', 'FMMonth') = 'January'   (7 chars, clean).
-- Any WHERE month_name = 'January' query silently returns 0 rows without this fix.
--
-- iso_year: ISO year differs from calendar year at year boundaries.
-- Example: 2010-01-01 is in ISO week 53 of ISO year 2009.
-- GROUP BY year, week is WRONG for those dates — use iso_year, week instead.
INSERT INTO dw.dim_date (
    date_sk, full_date, year, quarter, month, month_name,
    iso_year, week, day_of_month, day_of_week, day_name,
    is_weekend, is_month_start, is_month_end
    )
SELECT
    TO_CHAR(d, 'YYYYMMDD')::INTEGER           AS date_sk,
    d::DATE                                    AS full_date,
    EXTRACT(YEAR    FROM d)::SMALLINT          AS year,
    EXTRACT(QUARTER FROM d)::SMALLINT          AS quarter,
    EXTRACT(MONTH   FROM d)::SMALLINT          AS month,
    TO_CHAR(d, 'FMMonth')                      AS month_name,
    EXTRACT(ISOYEAR FROM d)::SMALLINT          AS iso_year,
    EXTRACT(WEEK    FROM d)::SMALLINT          AS week,
    EXTRACT(DAY     FROM d)::SMALLINT          AS day_of_month,
    EXTRACT(ISODOW  FROM d)::SMALLINT          AS day_of_week,
    TO_CHAR(d, 'FMDay')                        AS day_name,
    EXTRACT(ISODOW  FROM d) IN (6, 7)          AS is_weekend,
    EXTRACT(DAY     FROM d) = 1                AS is_month_start,
    d = DATE_TRUNC('month', d) + INTERVAL '1 month' - INTERVAL '1 day'
                                               AS is_month_end
FROM generate_series(
    '1900-01-01'::DATE,
    '2999-12-31'::DATE,
    INTERVAL '1 day'
    ) AS gs(d);


-- -----------------------------------------------------------------------------
-- DIMENSION: dw.dim_customers
--
-- Stores cleaned customer demographic and financial data.
-- Implements SCD Type 2 on yearly_income and employment_status.
--
-- SCD TYPE 2 MECHANICS:
--   When a customer's income or employment status changes, we do NOT overwrite
--   the existing row. Instead we:
--     1. Set valid_to = change_date - 1 day on the OLD row
--     2. Set is_current = FALSE on the OLD row
--     3. INSERT a new row with valid_from = change_date, is_current = TRUE
--   This preserves the full history, enabling accurate point-in-time analysis.
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS dw.dim_customers CASCADE;
CREATE TABLE dw.dim_customers (
    customer_sk         SERIAL        PRIMARY KEY,  -- Surrogate key
    client_id           INTEGER       NOT NULL,     -- Natural key from source
    current_age         SMALLINT,
    retirement_age      SMALLINT,
    birth_year          SMALLINT,
    birth_month         SMALLINT,
    gender              VARCHAR(10),
    address             TEXT,
    latitude            NUMERIC(9,6),
    longitude           NUMERIC(9,6),
    per_capita_income   NUMERIC(12,2),              -- Cleaned: '$29278' → 29278.00
    yearly_income       NUMERIC(12,2),              -- Cleaned: '$59696' → 59696.00
    total_debt          NUMERIC(12,2),              -- Cleaned: '$127613' → 127613.00
    credit_score        SMALLINT,
    num_credit_cards    SMALLINT,
    employment_status   VARCHAR(20),                -- Canonical: Employed | Self-Employed |
                                                    --            Student | Retired | Unemployed
    education_level     VARCHAR(20),                -- Canonical: High School | Associate |
                                                    --            Bachelor | Master | Doctorate
    -- SCD Type 2 tracking columns
    valid_from          DATE          NOT NULL DEFAULT CURRENT_DATE,
    valid_to            DATE          DEFAULT NULL, -- NULL means this is the current record
    is_current          BOOLEAN       NOT NULL DEFAULT TRUE
    );

-- Index on client_id for fast lookups when joining from fact table
CREATE INDEX idx_dim_customers_client_id  ON dw.dim_customers (client_id);
-- Index on is_current to quickly filter to current records only
CREATE INDEX idx_dim_customers_is_current ON dw.dim_customers (is_current);


-- -----------------------------------------------------------------------------
-- DIMENSION: dw.dim_cards
--
-- Stores cleaned card information including brand, type, credit limit,
-- and issuer details. No SCD — card attributes are treated as static.
-- If a card's details change, the warehouse is rebuilt (full refresh).
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS dw.dim_cards CASCADE;
CREATE TABLE dw.dim_cards (
    card_sk              SERIAL PRIMARY KEY,
    card_id              BIGINT NOT NULL,
    client_id            BIGINT NOT NULL,
    cvv                  INTEGER,
    num_cards_issued     INTEGER,
    year_pin_last_changed INTEGER,
    issuer_bank_name     VARCHAR(100), -- Increased from previous limit
    issuer_bank_state    VARCHAR(50),  -- Full state names possible (e.g. 'Pennsylvania')
    issuer_bank_type     VARCHAR(50),  -- Increased from VARCHAR(5)
    card_number          VARCHAR(20),
    card_brand           VARCHAR(50),
    card_type            VARCHAR(50),
    credit_limit         NUMERIC(12,2),
    expires              DATE,
    acct_open_date       DATE,
    has_chip             BOOLEAN,
    card_on_dark_web     BOOLEAN,
    issuer_risk_rating   VARCHAR(20)
    );

CREATE UNIQUE INDEX idx_dim_cards_card_id ON dw.dim_cards (card_id);
CREATE INDEX idx_dim_cards_client_id        ON dw.dim_cards (client_id);


-- -----------------------------------------------------------------------------
-- DIMENSION: dw.dim_merchants
--
-- Stores merchant location and category information.
-- The MCC description is joined from the mcc reference table during the
-- transformation layer and stored denormalised here — this is intentional
-- in a star schema to avoid a separate MCC lookup join at query time.
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS dw.dim_merchants CASCADE;
CREATE TABLE dw.dim_merchants (
    merchant_sk         SERIAL        PRIMARY KEY,
    merchant_id         INTEGER       NOT NULL,
    merchant_city       TEXT,                       -- City name, 'ONLINE' for online merchants, or NULL if unknown
    merchant_state      TEXT,                       -- US state code, Country name, or 'UNKNOWN'
    zip                 TEXT,                       -- 5-char ZIP code, NULL for online merchants
    mcc_code            INTEGER,
    mcc_description     TEXT,
    merchant_location   VARCHAR(15) NOT NULL DEFAULT 'US'
        CHECK (merchant_location IN ('US', 'International', 'Online'))
    );
 
CREATE UNIQUE INDEX idx_dim_merchants_id ON dw.dim_merchants (merchant_id);
CREATE INDEX idx_dim_merchants_zip       ON dw.dim_merchants (zip);
CREATE INDEX idx_dim_merchants_state     ON dw.dim_merchants (merchant_state);


-- =============================================================================
-- FACT TABLE
-- Created after dimensions so FK constraints can reference them.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- FACT: dw.fact_transactions
--
-- The central fact table of the star schema.
-- GRAIN: one row per individual transaction (card swipe or online purchase).
--
-- MEASURES (the numeric facts we aggregate):
--   - amount:     always positive - use is_refund to identify credits
--   - is_refund:  derived from negative amount in source data
--   - is_online:  derived from use_chip = 'Online Transaction'
--   - is_error:   derived from errors column being non-null
--
-- FOREIGN KEYS: all four dimension tables are joined via surrogate keys.
--   If a dimension record cannot be found (e.g. unknown merchant), the SK
--   is set to -1 (a conventional "unknown" sentinel row) rather than NULL,
--   preserving the row in the fact table while flagging the gap.
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS dw.fact_transactions CASCADE;
CREATE TABLE dw.fact_transactions (
    transaction_sk      SERIAL        PRIMARY KEY,  -- Surrogate key
    transaction_id      BIGINT        NOT NULL,     -- Natural key from source
    -- Dimension foreign keys
    date_sk             INTEGER       NOT NULL REFERENCES dw.dim_date(date_sk),
    customer_sk         INTEGER       NOT NULL REFERENCES dw.dim_customers(customer_sk),
    card_sk             INTEGER       NOT NULL REFERENCES dw.dim_cards(card_sk),
    merchant_sk         INTEGER       NOT NULL REFERENCES dw.dim_merchants(merchant_sk),
    -- Measures
    amount              NUMERIC(12,2) NOT NULL,     -- Always positive after cleaning
    is_refund           BOOLEAN       NOT NULL DEFAULT FALSE,  -- TRUE if source amount < 0
    is_online           BOOLEAN       NOT NULL DEFAULT FALSE,  -- TRUE if use_chip = 'Online Transaction' or merchant_city = 'ONLINE' ???
    is_error            BOOLEAN       NOT NULL DEFAULT FALSE,  -- TRUE if errors column was populated
    error_type          VARCHAR(100)                -- Normalised error category, NULL if clean 
    );

-- Indexes on all FK columns — critical for join performance on a large fact table
CREATE INDEX idx_fact_date_sk     ON dw.fact_transactions (date_sk);
CREATE INDEX idx_fact_customer_sk ON dw.fact_transactions (customer_sk);
CREATE INDEX idx_fact_card_sk     ON dw.fact_transactions (card_sk);
CREATE INDEX idx_fact_merchant_sk ON dw.fact_transactions (merchant_sk);

-- Composite index for the most common analytical query pattern:
-- filtering by customer over a date range
CREATE INDEX idx_fact_customer_date ON dw.fact_transactions (customer_sk, date_sk);