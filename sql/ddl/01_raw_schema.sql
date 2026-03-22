-- =============================================================================
-- FILE:    01_raw_schema.sql
-- PURPOSE: Define the RAW schema — the first landing zone for all source data.
--
-- DESIGN PHILOSOPHY:
--   The raw schema stores data exactly as it arrives from the CSV exports.
--   No cleaning, no business logic, no transformations happen here.
--   Every column is typed as broadly as possible (TEXT, NUMERIC) to ensure
--   we never lose a row due to a type mismatch during ingestion.
--
--   This separation is critical: if a transformation bug is discovered later,
--   we can fix the transform layer and re-run without re-importing source files.
--
-- SCHEMAS:
--   raw   → typed-only staging (this file)
--   dw    → clean star schema (02_dw_schema.sql)
--   mart  → business views    (03_mart_schema.sql)
--
-- AUTHOR:  Jonah Knief (i6263747) | Artem Vysotskyi (...) | Lyan Eleraky (...) | Loredana Lazari
-- COURSE:  Data Engineering and Data Compliance
-- UNI:     Maastricht University
-- =============================================================================


-- -----------------------------------------------------------------------------
-- SCHEMA SETUP
-- Drop and recreate raw schema for a clean, reproducible run.
-- In production this would be replaced with incremental logic.
-- -----------------------------------------------------------------------------

DROP SCHEMA IF EXISTS raw CASCADE;
CREATE SCHEMA raw;


-- -----------------------------------------------------------------------------
-- TABLE: raw.transactions
--
-- Source: transaction_data.csv (~1M+ rows)
-- Grain: one row per card swipe or online purchase
--
-- Known source issues handled at ingest (not cleaned here):
--   - amount:       stored as string with '$' prefix, can be negative (refunds)
--   - use_chip:     free-text string ("Swipe Transaction" / "Online Transaction")
--   - errors:       sparse column — mostly null for clean transactions
--   - merchant_state: "ONLINE" for e-commerce, real state codes for in-store
--   - zip:          numeric but nullable for online transactions
-- -----------------------------------------------------------------------------

CREATE TABLE raw.transactions (
    id               BIGINT,           -- Source transaction identifier
    date             TEXT,             -- Raw timestamp string: "2010-01-01 00:00:00"
    client_id        INTEGER,          -- FK to users (raw.users.id)
    card_id          INTEGER,          -- FK to cards (raw.cards.id)
    amount           TEXT,             -- Raw amount string: "$20.74" or "$-77.00"
    use_chip         TEXT,             -- "Swipe Transaction" or "Online Transaction"
    merchant_id      INTEGER,          -- Merchant identifier
    merchant_city    TEXT,             -- City name or "ONLINE"
    merchant_state   TEXT,             -- US state code, "ONLINE", or null
    zip              TEXT,             -- ZIP code — kept as TEXT to preserve leading zeros
    mcc              INTEGER,          -- Merchant Category Code — joins to raw.mcc.code
    errors           TEXT              -- Error description if transaction failed, else null
);


-- -----------------------------------------------------------------------------
-- TABLE: raw.users
--
-- Source: users_data.csv (2,020 rows)
-- Grain: one row per customer
--
-- Known source issues:
--   - per_capita_income, yearly_income, total_debt: string with '$' prefix
--   - employment_status: 32 unique variants for 5 logical values
--   - education_level:   35 unique variants for 5 logical values
-- -----------------------------------------------------------------------------

CREATE TABLE raw.users (
    id                  INTEGER,      -- Primary key — matches client_id in transactions
    current_age         INTEGER,
    retirement_age      INTEGER,
    birth_year          INTEGER,
    birth_month         INTEGER,
    gender              TEXT,
    address             TEXT,
    latitude            NUMERIC(9,6),
    longitude           NUMERIC(9,6),
    per_capita_income   TEXT,         -- Raw: "$29278"
    yearly_income       TEXT,         -- Raw: "$59696"
    total_debt          TEXT,         -- Raw: "$127613"
    credit_score        INTEGER,
    num_credit_cards    INTEGER,
    employment_status   TEXT,         -- Inconsistent: "Employed", "EMPLOYED", "Empl0yed" etc.
    education_level     TEXT          -- Inconsistent: "Bachelor Degree", "BACHELOR DEGREE" etc.
);


-- -----------------------------------------------------------------------------
-- TABLE: raw.cards
--
-- Source: cards_data.csv (6,207 rows)
-- Grain: one row per payment card
--
-- Known source issues:
--   - card_brand:        91 unique variants for 5 brands (Visa, Mastercard, etc.)
--   - card_type:         60 unique variants for 4 types (Debit, Credit, Prepaid, Unknown)
--   - card_number:       stored as FLOAT in CSV, losing leading digits — kept as TEXT
--   - credit_limit:      mixed formats: "$24295", "21968.00", "0.1k", "ten thousand"
--   - expires:           month-year string: "Dec-22"
--   - issuer_risk_rating: mixed case: "Low", "LOW", "Low Risk", "Med"
-- -----------------------------------------------------------------------------

CREATE TABLE raw.cards (
    id                      INTEGER,  -- Primary key — matches card_id in transactions
    client_id               INTEGER,  -- FK to raw.users.id
    card_brand              TEXT,     -- Raw: "V", "Visa", "VISA", "Vissa", "VVisa" etc.
    card_type               TEXT,     -- Raw: "Debit", "DEBIT", "DeBiT", "DB", "D" etc.
    card_number             TEXT,     -- Stored as TEXT to preserve all 16 digits
    expires                 TEXT,     -- Raw: "Dec-22"
    cvv                     INTEGER,
    has_chip                TEXT,     -- "YES" or "NO"
    num_cards_issued        INTEGER,
    credit_limit            TEXT,     -- Raw: "$24295", "0.1k", "ten thousand"
    acct_open_date          TEXT,     -- Raw: "Sep-02"
    year_pin_last_changed   INTEGER,
    card_on_dark_web        TEXT,     -- "Yes" or "No"
    issuer_bank_name        TEXT,
    issuer_bank_state       TEXT,
    issuer_bank_type        TEXT,
    issuer_risk_rating      TEXT      -- Raw: "Low", "LOW", "Low Risk", "Med" etc.
);


-- -----------------------------------------------------------------------------
-- TABLE: raw.mcc
--
-- Source: mcc_data.csv (127 rows)
-- Grain: one row per Merchant Category Code
--
-- Known source issues:
--   - code:        quoted strings ('"3000"'), MCC-prefixed ("MCC3066"), plain integers
--   - description: mixed case, leading/trailing whitespace
--
-- NOTE: The 'notes' and 'updated_by' columns from the source file are intentionally
--       excluded here — they are internal ops metadata with no analytical value.
-- -----------------------------------------------------------------------------

CREATE TABLE raw.mcc (
    code         TEXT,   -- Raw code: "1711", '"3000"', "MCC3066"
    description  TEXT    -- Raw description: " Steelworks", "STEEL PRODUCTS" etc.
);


-- -----------------------------------------------------------------------------
-- TABLE: raw.rejected
--
-- Rows that fail validation during Python ingestion are written here rather
-- than being silently dropped. This preserves full traceability — every source
-- row can be accounted for as either successfully loaded or rejected with a reason.
--
-- This table is appended to (not replaced) on each run so we maintain a
-- cumulative rejection log across pipeline executions.
-- -----------------------------------------------------------------------------

CREATE TABLE raw.rejected (
    source_table    TEXT,         -- Which source table the row came from
    source_row      TEXT,         -- The raw row content as a JSON string
    rejection_reason TEXT,        -- Human-readable reason (e.g. "null primary key")
    ingested_at     TIMESTAMP DEFAULT NOW()  -- When the rejection was logged
);


-- -----------------------------------------------------------------------------
-- INDEXES
-- Added on the join columns used in the transformation layer to improve
-- performance when reading from raw tables during transformation.
-- -----------------------------------------------------------------------------

CREATE INDEX idx_raw_transactions_client_id  ON raw.transactions (client_id);
CREATE INDEX idx_raw_transactions_card_id    ON raw.transactions (card_id);
CREATE INDEX idx_raw_transactions_mcc        ON raw.transactions (mcc);
CREATE INDEX idx_raw_cards_client_id         ON raw.cards (client_id);
