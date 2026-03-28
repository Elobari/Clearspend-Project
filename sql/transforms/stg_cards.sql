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
-- AUTHOR:  Jonah Knief (i6263747) | Arthem Vysotskyi (i6327809) | Lyan Eleraky (I6320604) | Loredana Lazari (I6346545)
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
    CASE 
        WHEN LEFT(TRIM(UPPER(card_brand)), 1) = 'V' THEN 'Visa'
        WHEN LEFT(TRIM(UPPER(card_brand)), 1) = 'M' THEN 'Mastercard'
        WHEN LEFT(TRIM(UPPER(card_brand)), 1) = 'A' THEN 'Amex'
        WHEN LEFT(TRIM(UPPER(card_brand)), 1) = 'D' THEN 'Discover'
        ELSE 'Unknown'
    END AS card_brand,
    -- -------------------------------------------------------------------------

    -- CARD TYPE NORMALISATION
    -- Source variants include: 'DEBIT', 'debit', 'DEBTI', 'DB', 'DEIBT', 'CREDIT', 'credit', 'CR', 'CEDIT', 'PREPAY', 'PPD', 'DPP', 'PAYED'
    --   Unknown: null, anything else
    -- Note: Prepaid check must come BEFORE Debit to avoid mis-classification
    -- -------------------------------------------------------------------------
    CASE
        -- 1. PREPAID: Check if the string contains a 'P' (catches PREPAY, PPD, DPP, PAYED)
        WHEN UPPER(TRIM(card_type)) LIKE '%P%' 
            THEN 'Prepaid'

        -- 2. DEBIT: Check if it starts with 'D' (catches DEBIT, DB, DEBTI, DEIBT)
        WHEN LEFT(UPPER(TRIM(card_type)), 1) = 'D'
        OR (UPPER(TRIM(card_type)) LIKE '%DEBIT%') -- Catch 'Bank Debit' and similar 
            THEN 'Debit'

        -- 3. CREDIT: Check if it starts with 'C' (catches CREDIT, CC, CR, CEDIT)
        WHEN LEFT(UPPER(TRIM(card_type)), 1) = 'C' 
            THEN 'Credit'

        ELSE 'Unknown'
    END AS card_type,

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
    -- Handles: 'Dec-22', '01-12-22', '2028-01-21', '02/01/1999', 'Feb 01 1996'
    -- -------------------------------------------------------------------------
    CASE 
        WHEN TRIM(expires) ~ '^\d{2}-\d{2}-\d{2}$' THEN TO_DATE(TRIM(expires), 'DD-MM-YY')
        WHEN TRIM(expires) ~* '^[a-z]{3}-\d{2}$'   THEN TO_DATE(TRIM(expires), 'Mon-YY')
        ELSE NULL 
    END AS expires,

    CASE 
        -- 1. Standard: '01-12-22' (DD-MM-YY)
        WHEN TRIM(acct_open_date) ~ '^\d{2}-\d{2}-\d{2}$' 
            THEN TO_DATE(TRIM(acct_open_date), 'DD-MM-YY')
            
        -- 2. Standard: 'Dec-22' (Mon-YY)
        WHEN TRIM(acct_open_date) ~* '^[a-z]{3}-\d{2}$' 
            THEN TO_DATE(TRIM(acct_open_date), 'Mon-YY')
            
        -- 3. ISO: '2028-01-21' (YYYY-MM-DD)
        WHEN TRIM(acct_open_date) ~ '^\d{4}-\d{2}-\d{2}$' 
            THEN TO_DATE(TRIM(acct_open_date), 'YYYY-MM-DD')
            
        -- 4. Slash: '02/01/1999' (MM/DD/YYYY)
        WHEN TRIM(acct_open_date) ~ '^\d{2}/\d{2}/\d{4}$' 
            THEN TO_DATE(TRIM(acct_open_date), 'MM/DD/YYYY')
            
        -- 5. Spaced: 'Feb 01 1996' (Mon DD YYYY)
        WHEN TRIM(acct_open_date) ~* '^[a-z]{3} \d{2} \d{4}$' 
            THEN TO_DATE(TRIM(acct_open_date), 'Mon DD YYYY')
            
        ELSE NULL 
    END AS acct_open_date,

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
