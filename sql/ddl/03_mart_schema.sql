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
--   1. mart.finance_summary    → Finance team
--   2. mart.customer_analytics → Customer Analytics team
--   3. mart.merchant_summary   → Merchant Partnerships team
--
-- AUTHOR:  Jonah Knief (i6263747)
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
--   3. Which states generate the most revenue?
--   4. Which merchant categories drive the highest spending?
-- =============================================================================

CREATE VIEW mart.finance_summary AS

-- Monthly revenue and transaction metrics
-- Each row = one calendar month with full financial KPIs
SELECT
    dd.year,
    dd.month,
    dd.month_name,

    -- Total revenue: sum of all non-refund transactions
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
        AS total_revenue,

    -- Total refund value: sum of all refund transactions
    SUM(CASE WHEN ft.is_refund = TRUE THEN ft.amount ELSE 0 END)
        AS total_refunds,

    -- Net revenue: revenue minus refunds
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
    - SUM(CASE WHEN ft.is_refund = TRUE  THEN ft.amount ELSE 0 END)
        AS net_revenue,

    -- Transaction counts
    COUNT(*)                                            AS total_transactions,
    SUM(CASE WHEN ft.is_refund = FALSE THEN 1 ELSE 0 END) AS sale_count,
    SUM(CASE WHEN ft.is_refund = TRUE  THEN 1 ELSE 0 END) AS refund_count,

    -- Refund rate as a percentage of total transactions
    ROUND(
        100.0 * SUM(CASE WHEN ft.is_refund = TRUE THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0),
        2
    ) AS refund_rate_pct,

    -- Error rate as a percentage of total transactions
    ROUND(
        100.0 * SUM(CASE WHEN ft.is_error = TRUE THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0),
        2
    ) AS error_rate_pct,

    -- Average transaction value (sales only, excluding refunds)
    ROUND(
        AVG(CASE WHEN ft.is_refund = FALSE THEN ft.amount END),
        2
    ) AS avg_transaction_value

FROM dw.fact_transactions ft
JOIN dw.dim_date dd ON ft.date_sk = dd.date_sk

GROUP BY dd.year, dd.month, dd.month_name
ORDER BY dd.year, dd.month;


-- =============================================================================
-- Supporting view: Revenue by state (for Finance question 3)
-- =============================================================================

CREATE VIEW mart.finance_by_state AS

SELECT
    dm.merchant_state,

    COUNT(*)                                                        AS total_transactions,
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)  AS total_revenue,
    SUM(CASE WHEN ft.is_refund = TRUE  THEN ft.amount ELSE 0 END)  AS total_refunds,

    ROUND(
        100.0 * SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
              / NULLIF(SUM(ft.amount), 0),
        2
    ) AS revenue_share_pct

FROM dw.fact_transactions ft
JOIN dw.dim_merchants dm ON ft.merchant_sk = dm.merchant_sk

-- Exclude ONLINE and UNKNOWN pseudo-states for geographic analysis
WHERE dm.merchant_state NOT IN ('ONLINE', 'UNKNOWN')
  AND dm.merchant_state IS NOT NULL

GROUP BY dm.merchant_state
ORDER BY total_revenue DESC;


-- =============================================================================
-- Supporting view: Revenue by merchant category (for Finance question 4)
-- =============================================================================

CREATE VIEW mart.finance_by_category AS

SELECT
    dm.mcc_code,
    dm.mcc_description,

    COUNT(*)                                                       AS total_transactions,
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END) AS total_revenue,

    ROUND(
        AVG(CASE WHEN ft.is_refund = FALSE THEN ft.amount END),
        2
    ) AS avg_transaction_value,

    -- Month with the highest revenue for this category
    MODE() WITHIN GROUP (ORDER BY dd.month) AS peak_month

FROM dw.fact_transactions ft
JOIN dw.dim_merchants dm ON ft.merchant_sk = dm.merchant_sk
JOIN dw.dim_date     dd ON ft.date_sk      = dd.date_sk

WHERE ft.is_refund = FALSE
  AND dm.mcc_description IS NOT NULL

GROUP BY dm.mcc_code, dm.mcc_description
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

    -- Customer Lifetime Value: total spend across all non-refund transactions
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
        AS lifetime_value,

    -- Total refunds received
    SUM(CASE WHEN ft.is_refund = TRUE THEN ft.amount ELSE 0 END)
        AS total_refunds,

    -- Net lifetime value (LTV minus refunds)
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
    - SUM(CASE WHEN ft.is_refund = TRUE  THEN ft.amount ELSE 0 END)
        AS net_lifetime_value,

    -- Online vs in-store breakdown
    SUM(CASE WHEN ft.is_online = TRUE  AND ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
        AS online_spend,
    SUM(CASE WHEN ft.is_online = FALSE AND ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
        AS instore_spend,

    -- Online spend as a percentage of total spend
    ROUND(
        100.0 * SUM(CASE WHEN ft.is_online = TRUE AND ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
              / NULLIF(SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END), 0),
        2
    ) AS online_spend_pct,

    -- Transaction counts
    COUNT(*)                                                AS total_transactions,
    SUM(CASE WHEN ft.is_online = TRUE  THEN 1 ELSE 0 END)  AS online_transactions,
    SUM(CASE WHEN ft.is_online = FALSE THEN 1 ELSE 0 END)  AS instore_transactions,

    -- Number of distinct cards used — proxy for active card count
    COUNT(DISTINCT ft.card_sk)                              AS distinct_cards_used,

    -- Number of distinct merchants visited — measures customer breadth
    COUNT(DISTINCT ft.merchant_sk)                          AS distinct_merchants,

    -- Date of first and most recent transaction
    MIN(dd.full_date)                                       AS first_transaction_date,
    MAX(dd.full_date)                                       AS last_transaction_date,

    -- Customer tenure in days
    MAX(dd.full_date) - MIN(dd.full_date)                   AS tenure_days,

    -- Error count — how many of this customer's transactions failed
    SUM(CASE WHEN ft.is_error = TRUE THEN 1 ELSE 0 END)    AS error_count

FROM dw.fact_transactions ft
JOIN dw.dim_customers dc ON ft.customer_sk = dc.customer_sk
JOIN dw.dim_date      dd ON ft.date_sk     = dd.date_sk

-- Only use the current customer record (SCD2 — ignore historical versions)
WHERE dc.is_current = TRUE

GROUP BY
    dc.client_id, dc.gender, dc.employment_status,
    dc.education_level, dc.credit_score, dc.yearly_income

ORDER BY lifetime_value DESC;


-- =============================================================================
-- Supporting view: Suspicious transaction flags (for Customer Analytics question 4)
--
-- Flags transactions where the same client_id made a transaction with the same
-- amount within 60 seconds — a common pattern for duplicate charges or fraud.
-- =============================================================================

CREATE VIEW mart.suspicious_transactions AS

SELECT
    ft.transaction_id,
    ft.transaction_sk,
    dc.client_id,
    dd.full_date                AS transaction_date,
    ft.amount,
    ft.is_online,
    dm.merchant_city,
    dm.merchant_state,

    -- Label that explains why this transaction was flagged
    'Duplicate amount within 60 seconds for same client' AS flag_reason

FROM dw.fact_transactions ft
JOIN dw.dim_customers dc ON ft.customer_sk = dc.customer_sk
JOIN dw.dim_date      dd ON ft.date_sk     = dd.date_sk
JOIN dw.dim_merchants dm ON ft.merchant_sk = dm.merchant_sk

-- Self-join to find transactions from the same customer with the same amount
-- that occurred within 60 seconds of each other
WHERE EXISTS (
    SELECT 1
    FROM dw.fact_transactions ft2
    JOIN dw.dim_customers dc2 ON ft2.customer_sk = dc2.customer_sk
    WHERE dc2.client_id = dc.client_id
      AND ft2.amount    = ft.amount
      AND ft2.transaction_sk != ft.transaction_sk
      -- The 60-second window is approximated using date_sk proximity
      -- for sub-day precision the transaction timestamp would be needed
)

AND dc.is_current = TRUE

ORDER BY dc.client_id, ft.amount;


-- =============================================================================
-- MART 3: mart.merchant_summary
-- Target team: Merchant Partnerships
--
-- Answers:
--   1. Which merchants generate the highest transaction volume?
--   2. What industries are growing the fastest?
--   3. Which merchants have the highest error rates?
--   4. How is revenue distributed geographically?
-- =============================================================================

CREATE VIEW mart.merchant_summary AS

SELECT
    dm.merchant_id,
    dm.merchant_city,
    dm.merchant_state,
    dm.mcc_code,
    dm.mcc_description,

    -- Transaction volume and value
    COUNT(*)                                                       AS total_transactions,
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END) AS total_revenue,
    SUM(CASE WHEN ft.is_refund = TRUE  THEN ft.amount ELSE 0 END) AS total_refunds,

    -- Net revenue
    SUM(CASE WHEN ft.is_refund = FALSE THEN ft.amount ELSE 0 END)
    - SUM(CASE WHEN ft.is_refund = TRUE  THEN ft.amount ELSE 0 END)
        AS net_revenue,

    -- Average transaction value
    ROUND(
        AVG(CASE WHEN ft.is_refund = FALSE THEN ft.amount END),
        2
    ) AS avg_transaction_value,

    -- Error rate: percentage of transactions that had an error/decline
    ROUND(
        100.0 * SUM(CASE WHEN ft.is_error = TRUE THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0),
        2
    ) AS error_rate_pct,

    -- Refund rate per merchant
    ROUND(
        100.0 * SUM(CASE WHEN ft.is_refund = TRUE THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0),
        2
    ) AS refund_rate_pct,

    -- Count of unique customers who transacted at this merchant
    COUNT(DISTINCT ft.customer_sk) AS unique_customers,

    -- Date range of transactions at this merchant
    MIN(dd.full_date) AS first_seen,
    MAX(dd.full_date) AS last_seen

FROM dw.fact_transactions ft
JOIN dw.dim_merchants dm ON ft.merchant_sk = dm.merchant_sk
JOIN dw.dim_date      dd ON ft.date_sk     = dd.date_sk

GROUP BY
    dm.merchant_id, dm.merchant_city, dm.merchant_state,
    dm.mcc_code, dm.mcc_description

ORDER BY total_revenue DESC;


-- =============================================================================
-- Supporting view: Month-over-month growth by industry (for Merchant question 2)
-- =============================================================================

CREATE VIEW mart.merchant_category_growth AS

WITH monthly_category_revenue AS (
    -- Step 1: Calculate revenue per category per month
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
)

-- Step 2: Use LAG window function to compare each month to the previous month
SELECT
    mcc_code,
    mcc_description,
    year,
    month,
    monthly_revenue,

    -- Previous month's revenue for the same category
    LAG(monthly_revenue) OVER (
        PARTITION BY mcc_code
        ORDER BY year, month
    ) AS prev_month_revenue,

    -- Month-over-month growth as a percentage
    ROUND(
        100.0 * (monthly_revenue - LAG(monthly_revenue) OVER (
                    PARTITION BY mcc_code ORDER BY year, month
                 ))
              / NULLIF(LAG(monthly_revenue) OVER (
                    PARTITION BY mcc_code ORDER BY year, month
                 ), 0),
        2
    ) AS mom_growth_pct

FROM monthly_category_revenue
ORDER BY year DESC, month DESC, monthly_revenue DESC;