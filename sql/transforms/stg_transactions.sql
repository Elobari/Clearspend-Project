-- =============================================================================
-- FILE:    stg_transactions.sql
-- PURPOSE: Clean and standardise raw.transactions → dw.stg_transactions
--
-- TRANSFORMATIONS APPLIED:
--   1. amount:         Strip '$', convert to NUMERIC - derive is_refund from sign
--   2. use_chip:       Map free-text → is_online boolean
--   3. errors:         Map null/populated → is_error boolean + error_type string
--   4. date:           Cast timestamp string to DATE - derive date_sk integer
--   5. merchant_state: Uppercase, standardise ONLINE/null → 'UNKNOWN'
--   6. zip:            Normalise to 5-char string, null for ONLINE transactions
--
-- OUTPUT TABLE: dw.stg_transactions
--   This staging table is the clean input for the warehouse build step.
--   It retains source natural keys (client_id, card_id, merchant_id) which
--   are resolved to surrogate keys when fact_transactions is populated.
--
-- AUTHOR:  Jonah Knief (i6263747) | Artem Vysotskyi (...) | Lyan Eleraky (...) | Loredana Lazari
-- COURSE:  Data Engineering and Data Compliance
-- UNI:     Maastricht University
-- =============================================================================

-- Drop and recreate staging table for a clean run
DROP TABLE IF EXISTS dw.stg_transactions;

CREATE TABLE dw.stg_transactions AS

SELECT
    -- -------------------------------------------------------------------------
    -- IDENTITY COLUMNS
    -- Retained as natural keys for surrogate key lookup in warehouse.py
    -- -------------------------------------------------------------------------
    id              AS transaction_id,
    client_id,
    card_id,
    merchant_id,

    -- -------------------------------------------------------------------------
    -- DATE TRANSFORMATION
    -- Source format: "2010-01-01 00:00:00" (timestamp string)
    -- Target:        DATE + integer date_sk in YYYYMMDD format
    -- date_sk is used as the FK to join with dw.dim_date
    -- -------------------------------------------------------------------------
    date::TIMESTAMP::DATE                                       AS transaction_date,
    TO_CHAR(date::TIMESTAMP::DATE, 'YYYYMMDD')::INTEGER         AS date_sk,

    -- -------------------------------------------------------------------------
    -- AMOUNT TRANSFORMATION
    -- Source: "$20.74" (positive sale) or "$-77.00" (refund/credit)
    -- Step 1: Strip the '$' character with REPLACE
    -- Step 2: Cast to NUMERIC
    -- Step 3: ABS() ensures amount is always stored as a positive number
    -- Step 4: is_refund captures whether the original was negative
    -- -------------------------------------------------------------------------
    ABS(REPLACE(amount, '$', '')::NUMERIC(12,2))                AS amount,
    CASE
        WHEN REPLACE(amount, '$', '')::NUMERIC(12,2) < 0 THEN TRUE
        ELSE FALSE
    END                                                          AS is_refund,

    -- -------------------------------------------------------------------------
    -- USE_CHIP → IS_ONLINE TRANSFORMATION
    -- Source: "Swipe Transaction" (physical) or "Online Transaction" (e-commerce)
    -- We derive a clean boolean flag for use in the customer analytics mart.
    -- Any value other than "Online Transaction" is treated as in-store.
    -- -------------------------------------------------------------------------
    CASE
        WHEN TRIM(use_chip) = 'Online Transaction' THEN TRUE
        ELSE FALSE
    END                                                          AS is_online,

    -- -------------------------------------------------------------------------
    -- ERRORS TRANSFORMATION
    -- Source: NULL for clean transactions, free-text string for failures
    -- We derive:
    --   is_error   → boolean: was there an error?
    --   error_type → standardised category (trimmed, uppercased for grouping)
    -- -------------------------------------------------------------------------
    CASE
        WHEN errors IS NULL OR TRIM(errors) = '' THEN FALSE
        ELSE TRUE
    END                                                          AS is_error,
    CASE
        WHEN errors IS NULL OR TRIM(errors) = '' THEN NULL
        ELSE UPPER(TRIM(errors))
    END                                                          AS error_type,

    -- -------------------------------------------------------------------------
    -- MERCHANT STATE NORMALISATION
    -- Source: US state codes ('CA', 'NY'), 'ONLINE', or NULL
    -- All non-state values are mapped to 'UNKNOWN' for consistent grouping.
    -- -------------------------------------------------------------------------
    CASE
        WHEN merchant_state IS NULL          THEN 'UNKNOWN'
        WHEN TRIM(merchant_state) = ''       THEN 'UNKNOWN'
        WHEN UPPER(TRIM(merchant_state)) = 'ONLINE' THEN 'ONLINE'
        ELSE UPPER(TRIM(merchant_state))
    END                                                          AS merchant_state,

    merchant_city,

    -- -------------------------------------------------------------------------
    -- ZIP CODE NORMALISATION
    -- Stored as TEXT to preserve leading zeros (e.g. "01234").
    -- NULL for online transactions where no zip applies.
    -- -------------------------------------------------------------------------
    CASE
        WHEN UPPER(TRIM(merchant_state)) = 'ONLINE' THEN NULL
        WHEN zip IS NULL OR TRIM(zip) = ''          THEN NULL
        ELSE LPAD(TRIM(zip), 5, '0')
    END                                                          AS zip,

    mcc                                                          AS mcc_code

FROM raw.transactions

-- Exclude rows where the primary key is null — these cannot be loaded
-- into the fact table and will already be in raw.rejected
WHERE id IS NOT NULL;

-- Index the natural keys for fast surrogate key lookups during warehouse build
CREATE INDEX idx_stg_txn_client_id   ON dw.stg_transactions (client_id);
CREATE INDEX idx_stg_txn_card_id     ON dw.stg_transactions (card_id);
CREATE INDEX idx_stg_txn_merchant_id ON dw.stg_transactions (merchant_id);
CREATE INDEX idx_stg_txn_date_sk     ON dw.stg_transactions (date_sk);
