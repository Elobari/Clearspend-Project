"""
test_marts.py
=============
Data quality tests for the mart layer and warehouse integrity.

Tests verify:
    - Referential integrity between fact and dimension tables
    - Mart views return data and key metrics are non-zero
    - Business logic correctness (e.g. refund_rate_pct is 0-100)
    - Star schema surrogate key completeness

AUTHOR:     Jonah Knief (i6263747) | Arthem Vysotskyi (i6327809) | Lyan Eleraky
COURSE:     Data Engineering and Data Compliance
UNIVERSITY: Maastricht University
"""

import pytest
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()
DB_URL = (
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}"
    f"/{os.getenv('DB_NAME')}"
)
engine = create_engine(DB_URL)


def query_scalar(sql: str):
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar()


# ---------------------------------------------------------------------------
# WAREHOUSE INTEGRITY
# ---------------------------------------------------------------------------

def test_fact_transactions_not_empty():
    """fact_transactions must have rows after the warehouse build."""
    count = query_scalar("SELECT COUNT(*) FROM dw.fact_transactions")
    assert count > 0, "fact_transactions is empty"


def test_fact_no_null_skis():
    """No fact row should have a NULL surrogate key for any dimension."""
    nulls = query_scalar("""
        SELECT COUNT(*) FROM dw.fact_transactions
        WHERE customer_sk IS NULL
           OR card_sk     IS NULL
           OR merchant_sk IS NULL
           OR date_sk     IS NULL
    """)
    assert nulls == 0, f"{nulls} fact rows have NULL surrogate keys"


def test_dim_customers_has_current_records():
    """dim_customers must have at least one is_current = TRUE record."""
    count = query_scalar(
        "SELECT COUNT(*) FROM dw.dim_customers WHERE is_current = TRUE"
    )
    assert count > 0, "No current customer records found in dim_customers"


def test_dim_date_coverage():
    """dim_date must cover the full 2000–2035 range."""
    count = query_scalar("SELECT COUNT(*) FROM dw.dim_date")
    # 36 years * ~365.25 days = ~13,149 rows expected
    assert count >= 13000, f"dim_date has only {count} rows — expected ~13149"


def test_fact_amounts_all_positive():
    """All amounts in fact_transactions must be non-negative.
    Zero-amount rows are valid — they represent declined/error transactions
    where no money moved. is_error = TRUE already flags these separately.
    """
    negatives = query_scalar(
        "SELECT COUNT(*) FROM dw.fact_transactions WHERE amount < 0"
    )
    assert negatives == 0, f"{negatives} fact rows have negative amounts"


# ---------------------------------------------------------------------------
# MART TESTS
# ---------------------------------------------------------------------------

def test_finance_summary_not_empty():
    """Finance summary mart must return rows."""
    count = query_scalar("SELECT COUNT(*) FROM mart.finance_summary")
    assert count > 0, "mart.finance_summary is empty"


def test_finance_refund_rate_valid_range():
    """Refund rate percentage must be between 0 and 100."""
    invalid = query_scalar("""
        SELECT COUNT(*) FROM mart.finance_summary
        WHERE refund_rate_pct < 0 OR refund_rate_pct > 100
    """)
    assert invalid == 0, f"{invalid} months have invalid refund_rate_pct"


def test_finance_net_revenue_reasonable():
    """Total net revenue across all months must be positive."""
    net = query_scalar("SELECT SUM(net_revenue) FROM mart.finance_summary")
    assert net > 0, f"Total net revenue is {net} — expected positive"


def test_customer_analytics_not_empty():
    """Customer analytics mart must return rows."""
    count = query_scalar("SELECT COUNT(*) FROM mart.customer_analytics")
    assert count > 0, "mart.customer_analytics is empty"


def test_customer_ltv_non_negative():
    """All customer lifetime values must be non-negative."""
    negatives = query_scalar(
        "SELECT COUNT(*) FROM mart.customer_analytics WHERE lifetime_value < 0"
    )
    assert negatives == 0, f"{negatives} customers have negative LTV"


def test_online_spend_pct_valid_range():
    """Online spend percentage must be 0–100."""
    invalid = query_scalar("""
        SELECT COUNT(*) FROM mart.customer_analytics
        WHERE online_spend_pct < 0 OR online_spend_pct > 100
    """)
    assert invalid == 0, f"{invalid} customers have invalid online_spend_pct"


def test_merchant_summary_not_empty():
    """Merchant summary mart must return rows."""
    count = query_scalar("SELECT COUNT(*) FROM mart.merchant_summary")
    assert count > 0, "mart.merchant_summary is empty"


def test_merchant_error_rate_valid_range():
    """Merchant error rates must be between 0 and 100."""
    invalid = query_scalar("""
        SELECT COUNT(*) FROM mart.merchant_summary
        WHERE error_rate_pct < 0 OR error_rate_pct > 100
    """)
    assert invalid == 0, f"{invalid} merchants have invalid error_rate_pct"


def test_card_testing_alerts_thresholds():
    """card_testing_alerts must only return rows meeting the 3-txn / 2-merchant thresholds."""
    invalid = query_scalar("""
        SELECT COUNT(*) FROM mart.card_testing_alerts
        WHERE txn_count < 3 OR distinct_merchants < 2
    """)
    assert invalid == 0, f"{invalid} alert rows violate the threshold conditions"


def test_error_analysis_shares_sum_to_100():
    """Error share percentages must sum to approximately 100."""
    total = query_scalar("SELECT SUM(error_share_pct) FROM mart.error_analysis")
    if total is not None:
        assert 99.0 <= float(total) <= 101.0, \
            f"error_share_pct sums to {total}, expected ~100"


def test_error_analysis_no_null_error_type():
    """error_analysis must not expose NULL error_type rows."""
    nulls = query_scalar(
        "SELECT COUNT(*) FROM mart.error_analysis WHERE error_type IS NULL"
    )
    assert nulls == 0, f"{nulls} rows in error_analysis have NULL error_type"
