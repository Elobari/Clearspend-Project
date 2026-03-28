-- =============================================================================
-- FILE:    03_mart_schema.sql
-- PURPOSE: Define the MART schema — business-facing analytical views.
--
-- DESIGN PHILOSOPHY:
--   Data marts are pre-joined, pre-aggregated views that answer specific
--   business questions for each team. Analysts query mart views directly —
--   they never need to understand the underlying star schema joins.
--
--   All three marts are implemented as PostgreSQL VIEWS (not materialised views).
--   This means they always reflect the latest warehouse data with zero sync
--   overhead. If query performance becomes a concern at scale, any view can be
--   converted to a MATERIALISED VIEW with a single ALTER statement.
--
-- MARTS:
--   1. mart.finance_summary       -> Finance team
--   2. mart.customer_analytics    -> Customer Analytics team
--   3. mart.merchant_summary      -> Merchant Partnerships team
--
-- AUTHOR:  Jonah Knief (i6263747) | Arthem Vysotskyi (i6327809) | Lyan Eleraky
-- COURSE:  Data Engineering and Data Compliance
-- UNI:     Maastricht University
-- =============================================================================


-- -----------------------------------------------------------------------------
-- SCHEMA SETUP
-- -----------------------------------------------------------------------------

DROP SCHEMA IF EXISTS mart CASCADE;
CREATE SCHEMA mart;


-- =============================================================================
-- MART 1: mart.finance_summary
-- Target team: Finance
--
-- Answers:
--   1. What is our total revenue by month?
--   2. What percentage of transactions are refunds?
--   3. Which states/countries generate the most revenue?
--   4. Which merchant categories drive the highest spending?
--   5. How is revenue split across US / International / Online?
-- =============================================================================

CREATE VIEW mart.finance_summary AS
SELECT
    dd.year,
    dd.month,
    dd.month_name,
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
        AS total_revenue,
    SUM(CASE WHEN ft.is_refund = TRUE THEN ft.amount ELSE 0 END)
        AS total_refunds,
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
    - SUM(CASE WHEN ft.is_refund = TRUE  THEN ft.amount ELSE 0 END)
        AS net_revenue,
    COUNT(*)                                               AS total_transactions,
    SUM(CASE WHEN ft.is_refund = FALSE THEN 1 ELSE 0 END)  AS sale_count,
    SUM(CASE WHEN ft.is_refund = TRUE  THEN 1 ELSE 0 END)  AS refund_count,
    ROUND(
        100.0 * SUM(CASE WHEN ft.is_refund = TRUE THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0), 2
    ) AS refund_rate_pct,
    ROUND(
        100.0 * SUM(CASE WHEN ft.is_error = TRUE THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0), 2
    ) AS error_rate_pct,
    ROUND(
        AVG(CASE WHEN ft.is_refund = FALSE THEN ft.amount END), 2
    ) AS avg_transaction_value
FROM dw.fact_transactions ft
JOIN dw.dim_date dd ON ft.date_sk = dd.date_sk
GROUP BY dd.year, dd.month, dd.month_name
ORDER BY dd.year, dd.month;


-- =============================================================================
-- Supporting view: Revenue by location type (US / International / Online)
-- New view — answers the question of how revenue splits across merchant locations.
-- =============================================================================

CREATE VIEW mart.finance_by_location AS
SELECT
    dm.merchant_location,
    COUNT(*)                                                        AS total_transactions,
    COUNT(DISTINCT ft.customer_sk)                                  AS unique_customers,
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)  AS total_revenue,
    SUM(CASE WHEN ft.is_refund = TRUE  THEN ft.amount ELSE 0 END)  AS total_refunds,
    ROUND(
        100.0 * SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
              / NULLIF(SUM(SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)) OVER (), 0),
        2
    ) AS revenue_share_pct
FROM dw.fact_transactions ft
JOIN dw.dim_merchants dm ON ft.merchant_sk = dm.merchant_sk
WHERE ft.is_refund = FALSE
GROUP BY dm.merchant_location
ORDER BY total_revenue DESC;


-- =============================================================================
-- Supporting view: Revenue by state/country
-- merchant_location added so analysts can filter US-only or international-only.
-- =============================================================================

CREATE VIEW mart.finance_by_state AS
SELECT
    dm.merchant_location,
    dm.merchant_state,
    COUNT(*)                                                        AS total_transactions,
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)  AS total_revenue,
    SUM(CASE WHEN ft.is_refund = TRUE  THEN ft.amount ELSE 0 END)  AS total_refunds,
    ROUND(
        100.0 * SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
              / NULLIF(SUM(SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)) OVER (), 0),
        2
    ) AS revenue_share_pct
FROM dw.fact_transactions ft
JOIN dw.dim_merchants dm ON ft.merchant_sk = dm.merchant_sk
WHERE dm.merchant_state NOT IN ('ONLINE', 'UNKNOWN')
  AND dm.merchant_state IS NOT NULL
GROUP BY dm.merchant_location, dm.merchant_state
ORDER BY total_revenue DESC;


-- =============================================================================
-- Supporting view: Revenue by merchant category
-- =============================================================================

CREATE VIEW mart.finance_by_category AS
SELECT
    dm.mcc_code,
    dm.mcc_description,
    COUNT(*)                                                       AS total_transactions,
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END) AS total_revenue,
    ROUND(
        AVG(CASE WHEN ft.is_refund = FALSE THEN ft.amount END), 2
    ) AS avg_transaction_value,
    MODE() WITHIN GROUP (ORDER BY dd.month) AS peak_month
FROM dw.fact_transactions ft
JOIN dw.dim_merchants dm ON ft.merchant_sk = dm.merchant_sk
JOIN dw.dim_date     dd ON ft.date_sk      = dd.date_sk
WHERE ft.is_refund = FALSE
  AND dm.mcc_description IS NOT NULL
GROUP BY dm.mcc_code, dm.mcc_description
ORDER BY total_revenue DESC;


-- =============================================================================
-- Supporting view: Revenue by ZIP code (physical US transactions only)
-- merchant_location added for completeness — will always be 'US' here since
-- zip IS NOT NULL filters out Online and International merchants.
-- =============================================================================

CREATE VIEW mart.finance_by_zip AS
SELECT
    dm.zip,
    dm.merchant_state,
    dm.merchant_location,
    COUNT(*)                                                        AS total_transactions,
    COUNT(DISTINCT ft.customer_sk)                                  AS unique_customers,
    COUNT(DISTINCT dm.merchant_id)                                  AS unique_merchants,
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)  AS total_revenue,
    SUM(CASE WHEN ft.is_refund = TRUE  THEN ft.amount ELSE 0 END)  AS total_refunds,
    ROUND(
        AVG(CASE WHEN ft.is_refund = FALSE THEN ft.amount END), 2
    ) AS avg_transaction_value
FROM dw.fact_transactions ft
JOIN dw.dim_merchants dm ON ft.merchant_sk = dm.merchant_sk
WHERE dm.zip IS NOT NULL
GROUP BY dm.zip, dm.merchant_state, dm.merchant_location
ORDER BY total_revenue DESC;


-- =============================================================================
-- MART 2: mart.customer_analytics
-- Target team: Customer Analytics
--
-- Answers:
--   1. What is the lifetime value of each customer?
--   2. How do customers behave online vs in-store?
--   3. How many active cards does a typical customer have?
--   4. Can we identify suspicious transaction patterns?
-- =============================================================================

CREATE VIEW mart.customer_analytics AS
SELECT
    dc.client_id,
    dc.gender,
    dc.employment_status,
    dc.education_level,
    dc.credit_score,
    dc.yearly_income,
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
        AS lifetime_value,
    SUM(CASE WHEN ft.is_refund = TRUE THEN ft.amount ELSE 0 END)
        AS total_refunds,
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
    - SUM(CASE WHEN ft.is_refund = TRUE  THEN ft.amount ELSE 0 END)
        AS net_lifetime_value,
    SUM(CASE WHEN ft.is_online = TRUE  AND ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
        AS online_spend,
    SUM(CASE WHEN ft.is_online = FALSE AND ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
        AS instore_spend,
    ROUND(
        100.0 * SUM(CASE WHEN ft.is_online = TRUE AND ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
              / NULLIF(SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END), 0),
        2
    ) AS online_spend_pct,
    COUNT(*)                                                AS total_transactions,
    SUM(CASE WHEN ft.is_online = TRUE  THEN 1 ELSE 0 END)  AS online_transactions,
    SUM(CASE WHEN ft.is_online = FALSE THEN 1 ELSE 0 END)  AS instore_transactions,
    COUNT(DISTINCT ft.card_sk)                              AS distinct_cards_used,
    COUNT(DISTINCT ft.merchant_sk)                          AS distinct_merchants,
    MIN(dd.full_date)                                       AS first_transaction_date,
    MAX(dd.full_date)                                       AS last_transaction_date,
    MAX(dd.full_date) - MIN(dd.full_date)                   AS tenure_days,
    SUM(CASE WHEN ft.is_error = TRUE THEN 1 ELSE 0 END)    AS error_count
FROM dw.fact_transactions ft
JOIN dw.dim_customers dc ON ft.customer_sk = dc.customer_sk
JOIN dw.dim_date      dd ON ft.date_sk     = dd.date_sk
WHERE dc.is_current = TRUE
GROUP BY
    dc.client_id, dc.gender, dc.employment_status,
    dc.education_level, dc.credit_score, dc.yearly_income
ORDER BY lifetime_value DESC;


-- =============================================================================
-- Supporting view: Suspicious transaction flags
--
-- Flags transactions where the same customer charged the same amount at the
-- same merchant more than once on the same calendar day.
-- merchant_sk is included in the PARTITION BY to avoid false positives where
-- two different merchants coincidentally charge the same amount on the same day.
-- =============================================================================

CREATE VIEW mart.suspicious_transactions AS
WITH txn_counts AS (
    SELECT
        transaction_sk,
        transaction_id,
        customer_sk,
        date_sk,
        amount,
        is_online,
        merchant_sk,
        COUNT(*) OVER (
            PARTITION BY customer_sk, date_sk, amount, merchant_sk
        ) AS duplicate_count
    FROM dw.fact_transactions
    WHERE is_refund = FALSE
)
SELECT
    tc.transaction_id,
    tc.transaction_sk,
    dc.client_id,
    dd.full_date                AS transaction_date,
    tc.amount,
    tc.is_online,
    tc.merchant_sk,
    dm.merchant_city,
    dm.merchant_state,
    dm.merchant_location,
    tc.duplicate_count,
    'Same amount charged ' || tc.duplicate_count
        || ' times on the same day at the same merchant'   AS flag_reason
FROM txn_counts tc
JOIN dw.dim_customers dc ON tc.customer_sk = dc.customer_sk
JOIN dw.dim_date      dd ON tc.date_sk     = dd.date_sk
JOIN dw.dim_merchants dm ON tc.merchant_sk = dm.merchant_sk
WHERE tc.duplicate_count > 1
  AND dc.is_current = TRUE
ORDER BY dc.client_id, dd.full_date, tc.amount;


-- =============================================================================
-- MART 3: mart.merchant_summary
-- Target team: Merchant Partnerships
--
-- Answers:
--   1. Which merchants generate the highest transaction volume?
--   2. What industries are growing the fastest?
--   3. Which merchants have the highest error rates?
--   4. How is revenue distributed geographically?
-- merchant_location added so Merchant Partnerships can filter by US /
-- International / Online without needing to know the state coding convention.
-- =============================================================================

CREATE VIEW mart.merchant_summary AS
SELECT
    dm.merchant_id,
    dm.merchant_location,
    dm.merchant_city,
    dm.merchant_state,
    dm.mcc_code,
    dm.mcc_description,
    COUNT(*)                                                       AS total_transactions,
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END) AS total_revenue,
    SUM(CASE WHEN ft.is_refund = TRUE  THEN ft.amount ELSE 0 END) AS total_refunds,
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
    - SUM(CASE WHEN ft.is_refund = TRUE  THEN ft.amount ELSE 0 END)
        AS net_revenue,
    ROUND(
        AVG(CASE WHEN ft.is_refund = FALSE THEN ft.amount END), 2
    ) AS avg_transaction_value,
    ROUND(
        100.0 * SUM(CASE WHEN ft.is_error = TRUE THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0), 2
    ) AS error_rate_pct,
    ROUND(
        100.0 * SUM(CASE WHEN ft.is_refund = TRUE THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0), 2
    ) AS refund_rate_pct,
    COUNT(DISTINCT ft.customer_sk) AS unique_customers,
    MIN(dd.full_date) AS first_seen,
    MAX(dd.full_date) AS last_seen
FROM dw.fact_transactions ft
JOIN dw.dim_merchants dm ON ft.merchant_sk = dm.merchant_sk
JOIN dw.dim_date      dd ON ft.date_sk     = dd.date_sk
GROUP BY
    dm.merchant_id, dm.merchant_location, dm.merchant_city,
    dm.merchant_state, dm.mcc_code, dm.mcc_description
ORDER BY total_revenue DESC;


-- =============================================================================
-- Supporting view: Month-over-month growth by industry
-- =============================================================================

CREATE VIEW mart.merchant_category_growth AS
WITH monthly_category_revenue AS (
    SELECT
        dm.mcc_code,
        dm.mcc_description,
        dd.year,
        dd.month,
        SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END) AS monthly_revenue
    FROM dw.fact_transactions ft
    JOIN dw.dim_merchants dm ON ft.merchant_sk = dm.merchant_sk
    JOIN dw.dim_date      dd ON ft.date_sk     = dd.date_sk
    GROUP BY dm.mcc_code, dm.mcc_description, dd.year, dd.month
),
with_lag AS (
    SELECT
        mcc_code,
        mcc_description,
        year,
        month,
        monthly_revenue,
        LAG(monthly_revenue) OVER (
            PARTITION BY mcc_code
            ORDER BY year, month
        ) AS prev_month_revenue
    FROM monthly_category_revenue
)
SELECT
    mcc_code,
    mcc_description,
    year,
    month,
    monthly_revenue,
    prev_month_revenue,
    CASE
        WHEN prev_month_revenue IS NULL THEN NULL
        WHEN prev_month_revenue < 100   THEN NULL
        ELSE ROUND(
            100.0 * (monthly_revenue - prev_month_revenue)
                  / prev_month_revenue,
            2
        )
    END AS mom_growth_pct
FROM with_lag
ORDER BY year DESC, month DESC, monthly_revenue DESC;

CREATE VIEW mart.customers_without_transactions AS
SELECT
    dc.client_id,
    dc.gender,
    dc.employment_status,
    dc.education_level,
    dc.yearly_income,
    dc.credit_score,
    dc.birth_year,
    dc.birth_month
FROM dw.dim_customers dc
LEFT JOIN dw.fact_transactions ft ON dc.customer_sk = ft.customer_sk
WHERE dc.is_current = TRUE
  AND ft.customer_sk IS NULL;


-- =============================================================================
-- Supporting view: Card testing alerts (fraud detection)
--
-- Detects potential card testing patterns: a customer making 3+ small-amount
-- transactions (< $10) across 2+ distinct merchants on the same calendar day.
--
-- Card testing is a fraud pattern where stolen card numbers are validated by
-- running small probe charges at different merchants before escalating to
-- high-value fraud. Unlike mart.suspicious_transactions (which detects exact
-- duplicate charges at the same merchant), this view catches the varying-amount
-- multi-merchant pattern that duplicate detection misses.
--
-- Thresholds used:
--   amount < $10   — typical card-testing probe amount
--   txn_count >= 3 — at least 3 transactions in one day
--   distinct_merchants >= 2 — spread across multiple merchants (not just one)
-- =============================================================================

CREATE VIEW mart.card_testing_alerts AS
WITH daily_small_txns AS (
    SELECT
        ft.customer_sk,
        ft.card_sk,
        ft.date_sk,
        COUNT(*)                    AS txn_count,
        COUNT(DISTINCT ft.merchant_sk) AS distinct_merchants,
        SUM(ft.amount)              AS total_amount,
        MIN(ft.amount)              AS min_amount,
        MAX(ft.amount)              AS max_amount
    FROM dw.fact_transactions ft
    WHERE ft.is_refund = FALSE
      AND ft.amount < 10.00
    GROUP BY ft.customer_sk, ft.card_sk, ft.date_sk
    HAVING COUNT(*) >= 3
       AND COUNT(DISTINCT ft.merchant_sk) >= 2
)
SELECT
    dc.client_id,
    dst.card_sk,
    dd.full_date                AS alert_date,
    dst.txn_count,
    dst.distinct_merchants,
    ROUND(dst.total_amount, 2)  AS total_amount,
    ROUND(dst.min_amount, 2)    AS min_amount,
    ROUND(dst.max_amount, 2)    AS max_amount,
    'Card testing pattern: ' || dst.txn_count
        || ' small transactions ($' || ROUND(dst.min_amount, 2)
        || '-$' || ROUND(dst.max_amount, 2)
        || ') across ' || dst.distinct_merchants
        || ' merchants in one day'  AS flag_reason
FROM daily_small_txns dst
JOIN dw.dim_customers dc ON dst.customer_sk = dc.customer_sk
JOIN dw.dim_date      dd ON dst.date_sk     = dd.date_sk
WHERE dc.is_current = TRUE
ORDER BY dst.txn_count DESC, dd.full_date;


-- =============================================================================
-- Supporting view: Error type analysis
--
-- Surfaces the error_type column from fact_transactions (populated from the
-- raw errors field during staging) as a mart-level breakdown. Enables the
-- Customer Analytics and Merchant Partnerships teams to:
--   - Identify the most common failure types (e.g. Insufficient Balance vs Bad PIN)
--   - Track error trends over time per merchant or card type
--   - Prioritise fraud investigation by error category
--
-- NOTE: dim_merchants is derived from observed transactions, not a separate
-- merchant source file. Merchants with zero transactions are therefore not
-- present in dim_merchants or any mart view — this is a known data model
-- constraint documented in the Known Limitations section of the report.
-- =============================================================================

CREATE VIEW mart.error_analysis AS
SELECT
    ft.error_type,
    COUNT(*)                            AS error_count,
    COUNT(DISTINCT ft.customer_sk)      AS affected_customers,
    COUNT(DISTINCT ft.merchant_sk)      AS affected_merchants,
    ROUND(AVG(ft.amount), 2)            AS avg_transaction_amount,
    ROUND(
        100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0), 2
    )                                   AS error_share_pct,
    MIN(dd.full_date)                   AS first_seen,
    MAX(dd.full_date)                   AS last_seen
FROM dw.fact_transactions ft
JOIN dw.dim_date dd ON ft.date_sk = dd.date_sk
WHERE ft.is_error = TRUE
  AND ft.error_type IS NOT NULL
GROUP BY ft.error_type
ORDER BY error_count DESC;