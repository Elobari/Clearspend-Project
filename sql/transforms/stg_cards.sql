-- =============================================================================
-- FILE:    stg_cards.sql
-- PURPOSE: Clean and standardise raw.cards → dw.stg_cards
--
-- TRANSFORMATIONS APPLIED:
--   1. card_brand:        Map 91 variants → 5 canonical values
--   2. card_type:         Map 60 variants → 4 canonical values
--   3. credit_limit:      Strip '$', parse 'k' suffix + word numbers → NUMERIC
--   4. card_number:       Already TEXT in raw, zero-padded to 16 chars
--   5. expires:           Parse 'Dec-22' or 'DD-MM-YY' → DATE
--   6. acct_open_date:    Parse 'Sep-02' or 'DD-MM-YY' → DATE
--   7. has_chip:          'YES'/'NO' → TRUE/FALSE boolean
--   8. card_on_dark_web:  'Yes'/'No' → TRUE/FALSE boolean
--   9. issuer_risk_rating: Map variants → Low | Medium | High | Unknown
--
-- DATE FORMAT HANDLING:
--   Two source date formats are observed:
--     Standard:  'Dec-22'    — abbreviated month + 2-digit year (Mon-YY)
--     Alternate: '01-12-22'  — DD-MM-YY numeric format
--   The CASE expression detects the format by regex and normalises both
--   to Mon-YY before final parsing. Invalid dates fall through to NULL.
--
-- AUTHOR:  Jonah Knief (i6263747) | Artem Vysotskyi (...) | Lyan Eleraky (...) | Loredana Lazari
-- COURSE:  Data Engineering and Data Compliance
-- UNI:     Maastricht University
-- =============================================================================

DROP TABLE IF EXISTS dw.stg_cards;

CREATE TABLE dw.stg_cards AS

SELECT
    id          AS card_id,
    client_id,
    cvv,
    num_cards_issued,
    year_pin_last_changed,
    TRIM(issuer_bank_name) AS issuer_bank_name,
    TRIM(issuer_bank_state) AS issuer_bank_state,
    TRIM(issuer_bank_type) AS issuer_bank_type,

    -- -------------------------------------------------------------------------
    -- CARD NUMBER
    -- Source was stored as FLOAT in the original CSV (losing leading digits).
    -- In raw.cards it was captured as TEXT with the decimal stripped at ingest.
    -- Here we zero-pad to 16 chars for consistent formatting.
    -- -------------------------------------------------------------------------
    LPAD(TRIM(card_number), 16, '0')                            AS card_number,

    -- -------------------------------------------------------------------------
    -- CARD BRAND NORMALISATION
    -- Source has 91 unique variants. Key mapping groups:
    --   Visa:       'V', 'Visa', 'VISA', 'Vissa', 'VVisa', ' Visa' etc.
    --   Mastercard: 'Mastercard', 'MASTERCARD', 'Master Card' etc.
    --   Amex:       'Amex', 'American Express', 'AMEX'
    --   Discover:   'Discover', 'DISCOVER'
    --   Unknown:    null, anything else
    -- -------------------------------------------------------------------------
    CASE
        WHEN UPPER(TRIM(card_brand)) IN ('V', 'VISA', 'VISSA', 'VVISA', 'VI')
          OR UPPER(TRIM(card_brand)) LIKE 'VIS%'
            THEN 'Visa'
        WHEN UPPER(TRIM(card_brand)) LIKE '%MASTER%'
          OR UPPER(TRIM(card_brand)) = 'MC'
            THEN 'Mastercard'
        WHEN UPPER(TRIM(card_brand)) LIKE '%AMEX%'
          OR UPPER(TRIM(card_brand)) LIKE '%AMERICAN%'
            THEN 'Amex'
        WHEN UPPER(TRIM(card_brand)) LIKE '%DISCOVER%'
            THEN 'Discover'
        ELSE 'Unknown'
    END                                                         AS card_brand,

    -- -------------------------------------------------------------------------
    -- CARD TYPE NORMALISATION
    -- Source has 60 unique variants. Key patterns:
    --   Debit:   'Debit', 'DEBIT', 'DEB', 'DB', 'D', 'Bank Debit'
    --   Credit:  'Credit', 'CREDIT', 'CC', 'CR', 'CRED', 'C'
    --   Prepaid: 'Debit (Prepaid)', 'Debit (Pre payed)', 'Prepaid'
    --   Unknown: null, anything else
    -- Note: Prepaid check must come BEFORE Debit to avoid mis-classification
    -- -------------------------------------------------------------------------
    CASE
        WHEN UPPER(TRIM(card_type)) LIKE '%PREPAID%'
          OR UPPER(TRIM(card_type)) LIKE '%PRE PAY%'
          OR UPPER(TRIM(card_type)) LIKE '%PAYED%'
            THEN 'Prepaid'
        WHEN UPPER(TRIM(card_type)) LIKE '%DEBIT%'
          OR UPPER(TRIM(card_type)) IN ('DEB', 'DB', 'D', 'BANK DEBIT', 'DEBIIT')
            THEN 'Debit'
        WHEN UPPER(TRIM(card_type)) LIKE '%CREDIT%'
          OR UPPER(TRIM(card_type)) IN ('CC', 'CR', 'CRED', 'C')
            THEN 'Credit'
        ELSE 'Unknown'
    END                                                         AS card_type,

    -- -------------------------------------------------------------------------
    -- CREDIT LIMIT NORMALISATION
    -- Source formats: '$24295', '21968.00', '0.1k', 'ten thousand', '10000000'
    -- Strategy:
    --   1. Handle word-form numbers explicitly (rare but present in data)
    --   2. Handle 'k' suffix shorthand (0.1k = 100, 10k = 10000)
    --   3. Validate remaining values are numeric before casting
    --   4. Non-parseable values (e.g. 'error_value') fall through to NULL
    -- -------------------------------------------------------------------------
    ABS(CASE
        WHEN LOWER(TRIM(credit_limit)) = 'ten thousand'    THEN 10000.00
        WHEN LOWER(TRIM(credit_limit)) = 'five thousand'   THEN 5000.00
        WHEN LOWER(TRIM(credit_limit)) = 'one thousand'    THEN 1000.00
        WHEN LOWER(TRIM(credit_limit)) LIKE '%k'
            THEN (REGEXP_REPLACE(LOWER(TRIM(credit_limit)), '[^0-9.]', '', 'g')::NUMERIC * 1000)
        WHEN REGEXP_REPLACE(credit_limit, '[$,\s]', '', 'g') ~ '^\-?[0-9]+(\.[0-9]+)?$'
            THEN REGEXP_REPLACE(credit_limit, '[$,]', '', 'g')::NUMERIC(12,2)
        ELSE NULL
    END)                                                        AS credit_limit,

    -- -------------------------------------------------------------------------
    -- DATE FIELDS
    -- Two source formats are observed:
    --   Standard:  'Dec-22'    (Mon-YY)
    --   Alternate: '01-12-22'  (DD-MM-YY)
    -- The CASE expression detects the format by regex and normalises both
    -- to Mon-YY before the outer TO_DATE parses them.
    -- Values matching neither pattern (e.g. 'not available') return NULL.
    -- -------------------------------------------------------------------------
    TO_DATE(
        CASE
            WHEN expires ~ '^\d{2}-\d{2}-\d{2}$'
                THEN TO_CHAR(TO_DATE(expires, 'DD-MM-YY'), 'Mon-YY')
            WHEN expires ~ '^[A-Za-z]{3}-\d{2}$'
                THEN expires
            ELSE NULL
        END,
        'Mon-YY'
    )                                                           AS expires,

    TO_DATE(
        CASE
            WHEN acct_open_date ~ '^\d{2}-\d{2}-\d{2}$'
                THEN TO_CHAR(TO_DATE(acct_open_date, 'DD-MM-YY'), 'Mon-YY')
            WHEN acct_open_date ~ '^[A-Za-z]{3}-\d{2}$'
                THEN acct_open_date
            ELSE NULL
        END,
        'Mon-YY'
    )                                                           AS acct_open_date,

    -- -------------------------------------------------------------------------
    -- BOOLEAN FIELDS
    -- -------------------------------------------------------------------------
    CASE WHEN UPPER(TRIM(has_chip))         = 'YES' THEN TRUE ELSE FALSE END
        AS has_chip,
    CASE WHEN UPPER(TRIM(card_on_dark_web)) = 'YES' THEN TRUE ELSE FALSE END
        AS card_on_dark_web,

    -- -------------------------------------------------------------------------
    -- ISSUER RISK RATING NORMALISATION
    -- Source: 'Low', 'low', 'LOW', 'Low Risk', 'Med', 'Medium', 'MEDIUM'
    -- Canonical values: Low | Medium | High | Unknown
    -- -------------------------------------------------------------------------
    CASE
        WHEN UPPER(TRIM(issuer_risk_rating)) LIKE '%LOW%'   THEN 'Low'
        WHEN UPPER(TRIM(issuer_risk_rating)) LIKE '%MED%'   THEN 'Medium'
        WHEN UPPER(TRIM(issuer_risk_rating)) LIKE '%HIGH%'  THEN 'High'
        ELSE 'Unknown'
    END                                                         AS issuer_risk_rating

FROM raw.cards

WHERE id IS NOT NULL;

CREATE INDEX idx_stg_cards_card_id   ON dw.stg_cards (card_id);
CREATE INDEX idx_stg_cards_client_id ON dw.stg_cards (client_id);
