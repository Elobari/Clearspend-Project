"""
test_warehouse.py
=================
Data quality tests for the warehouse layer (dw schema).

Tests verify:
    - All dimension tables are populated with expected row counts
    - dim_date covers the full calendar range and has valid attribute values
    - dim_customers SCD Type 2 integrity (exactly one current record per client)
    - dim_cards has valid canonical values and non-negative credit limits
    - dim_merchants has no duplicate merchant_ids
    - Referential integrity: every FK in fact_transactions resolves to a dimension row

AUTHOR:     Jonah Knief (i6263747)
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
# ROW COUNT CHECKS
# ---------------------------------------------------------------------------

def test_dim_customers_row_count():
    """dim_customers must have exactly 2,000 rows (2,020 source users minus 20 duplicates)."""
    count = query_scalar("SELECT COUNT(*) FROM dw.dim_customers")
    assert count == 2000, f"Expected 2000 customers, got {count}"


def test_dim_cards_row_count():
    """dim_cards must have exactly 6,207 rows (one per source card)."""
    count = query_scalar("SELECT COUNT(*) FROM dw.dim_cards")
    assert count == 6207, f"Expected 6207 cards, got {count}"


def test_dim_merchants_row_count():
    """dim_merchants must have exactly 74,831 distinct merchants."""
    count = query_scalar("SELECT COUNT(*) FROM dw.dim_merchants")
    assert count == 74831, f"Expected 74831 merchants, got {count}"


def test_dim_date_row_count():
    """dim_date must cover 2000-01-01 to 2035-12-31 (~13,149 days)."""
    count = query_scalar("SELECT COUNT(*) FROM dw.dim_date")
    assert count >= 13149, f"dim_date has only {count} rows — expected >= 13149"


def test_fact_transactions_row_count():
    """fact_transactions must have rows after the warehouse build."""
    count = query_scalar("SELECT COUNT(*) FROM dw.fact_transactions")
    assert count > 0, "fact_transactions is empty"


# ---------------------------------------------------------------------------
# dim_date INTEGRITY
# ---------------------------------------------------------------------------

def test_dim_date_no_null_dates():
    """Every row in dim_date must have a non-null full_date."""
    nulls = query_scalar("SELECT COUNT(*) FROM dw.dim_date WHERE full_date IS NULL")
    assert nulls == 0, f"{nulls} rows in dim_date have NULL full_date"


def test_dim_date_date_sk_format():
    """date_sk must be an 8-digit YYYYMMDD integer (between 20000101 and 20351231)."""
    invalid = query_scalar("""
        SELECT COUNT(*) FROM dw.dim_date
        WHERE date_sk < 20000101 OR date_sk > 20351231
    """)
    assert invalid == 0, f"{invalid} rows have date_sk outside the valid YYYYMMDD range"


def test_dim_date_month_valid_range():
    """Month column must always be 1–12."""
    invalid = query_scalar(
        "SELECT COUNT(*) FROM dw.dim_date WHERE month < 1 OR month > 12"
    )
    assert invalid == 0, f"{invalid} rows have month outside 1–12"


def test_dim_date_quarter_valid_range():
    """Quarter column must always be 1–4."""
    invalid = query_scalar(
        "SELECT COUNT(*) FROM dw.dim_date WHERE quarter < 1 OR quarter > 4"
    )
    assert invalid == 0, f"{invalid} rows have quarter outside 1–4"


def test_dim_date_day_of_week_valid_range():
    """day_of_week must always be 1 (Monday) to 7 (Sunday) per ISO standard."""
    invalid = query_scalar(
        "SELECT COUNT(*) FROM dw.dim_date WHERE day_of_week < 1 OR day_of_week > 7"
    )
    assert invalid == 0, f"{invalid} rows have day_of_week outside 1–7"


def test_dim_date_is_weekend_correct():
    """is_weekend must be TRUE if and only if day_of_week is 6 (Sat) or 7 (Sun)."""
    mismatches = query_scalar("""
        SELECT COUNT(*) FROM dw.dim_date
        WHERE is_weekend != (day_of_week IN (6, 7))
    """)
    assert mismatches == 0, f"{mismatches} rows have incorrect is_weekend flag"


# ---------------------------------------------------------------------------
# dim_customers INTEGRITY (SCD Type 2)
# ---------------------------------------------------------------------------

def test_dim_customers_no_null_client_id():
    """client_id must never be NULL in dim_customers."""
    nulls = query_scalar(
        "SELECT COUNT(*) FROM dw.dim_customers WHERE client_id IS NULL"
    )
    assert nulls == 0, f"{nulls} rows in dim_customers have NULL client_id"


def test_dim_customers_exactly_one_current_per_client():
    """Each client_id must have exactly one is_current = TRUE row (SCD Type 2)."""
    violations = query_scalar("""
        SELECT COUNT(*) FROM (
            SELECT client_id
            FROM dw.dim_customers
            WHERE is_current = TRUE
            GROUP BY client_id
            HAVING COUNT(*) != 1
        ) sub
    """)
    assert violations == 0, (
        f"{violations} client_ids have more or fewer than one current record"
    )


def test_dim_customers_current_records_have_null_valid_to():
    """Current records (is_current = TRUE) must have valid_to = NULL."""
    invalid = query_scalar("""
        SELECT COUNT(*) FROM dw.dim_customers
        WHERE is_current = TRUE AND valid_to IS NOT NULL
    """)
    assert invalid == 0, f"{invalid} current customer records have a non-NULL valid_to"


def test_dim_customers_employment_status_canonical():
    """employment_status must only contain the five canonical values."""
    invalid = query_scalar("""
        SELECT COUNT(*) FROM dw.dim_customers
        WHERE employment_status IS NOT NULL
          AND employment_status NOT IN (
              'Employed', 'Self-Employed', 'Student', 'Retired', 'Unemployed', 'Unknown'
          )
    """)
    assert invalid == 0, f"{invalid} rows have a non-canonical employment_status"


# ---------------------------------------------------------------------------
# dim_cards INTEGRITY
# ---------------------------------------------------------------------------

def test_dim_cards_no_null_card_id():
    """card_id must never be NULL in dim_cards."""
    nulls = query_scalar("SELECT COUNT(*) FROM dw.dim_cards WHERE card_id IS NULL")
    assert nulls == 0, f"{nulls} rows in dim_cards have NULL card_id"


def test_dim_cards_credit_limit_non_negative():
    """credit_limit must be >= 0 after the ABS() fix in the transform."""
    negatives = query_scalar(
        "SELECT COUNT(*) FROM dw.dim_cards WHERE credit_limit < 0"
    )
    assert negatives == 0, f"{negatives} cards have a negative credit_limit"


def test_dim_cards_brand_canonical():
    """card_brand must only contain the five canonical values."""
    invalid = query_scalar("""
        SELECT COUNT(*) FROM dw.dim_cards
        WHERE card_brand NOT IN ('Visa', 'Mastercard', 'Amex', 'Discover', 'Unknown')
    """)
    assert invalid == 0, f"{invalid} cards have a non-canonical card_brand"


def test_dim_cards_type_canonical():
    """card_type must only contain the four canonical values."""
    invalid = query_scalar("""
        SELECT COUNT(*) FROM dw.dim_cards
        WHERE card_type NOT IN ('Debit', 'Credit', 'Prepaid', 'Unknown')
    """)
    assert invalid == 0, f"{invalid} cards have a non-canonical card_type"


# ---------------------------------------------------------------------------
# dim_merchants INTEGRITY
# ---------------------------------------------------------------------------

def test_dim_merchants_no_null_merchant_id():
    """merchant_id must never be NULL in dim_merchants."""
    nulls = query_scalar(
        "SELECT COUNT(*) FROM dw.dim_merchants WHERE merchant_id IS NULL"
    )
    assert nulls == 0, f"{nulls} rows in dim_merchants have NULL merchant_id"


def test_dim_merchants_no_duplicate_merchant_id():
    """merchant_id must be unique across dim_merchants."""
    duplicates = query_scalar("""
        SELECT COUNT(*) FROM (
            SELECT merchant_id
            FROM dw.dim_merchants
            GROUP BY merchant_id
            HAVING COUNT(*) > 1
        ) sub
    """)
    assert duplicates == 0, f"{duplicates} merchant_ids appear more than once"


# ---------------------------------------------------------------------------
# REFERENTIAL INTEGRITY (fact → dimensions)
# ---------------------------------------------------------------------------

def test_fact_all_date_sks_resolve():
    """Every date_sk in fact_transactions must exist in dim_date."""
    orphans = query_scalar("""
        SELECT COUNT(*) FROM dw.fact_transactions ft
        LEFT JOIN dw.dim_date dd ON ft.date_sk = dd.date_sk
        WHERE dd.date_sk IS NULL
    """)
    assert orphans == 0, f"{orphans} fact rows have a date_sk with no matching dim_date row"


def test_fact_all_customer_sks_resolve():
    """Every customer_sk in fact_transactions must exist in dim_customers."""
    orphans = query_scalar("""
        SELECT COUNT(*) FROM dw.fact_transactions ft
        LEFT JOIN dw.dim_customers dc ON ft.customer_sk = dc.customer_sk
        WHERE dc.customer_sk IS NULL
    """)
    assert orphans == 0, f"{orphans} fact rows have a customer_sk with no matching dim_customers row"


def test_fact_all_card_sks_resolve():
    """Every card_sk in fact_transactions must exist in dim_cards."""
    orphans = query_scalar("""
        SELECT COUNT(*) FROM dw.fact_transactions ft
        LEFT JOIN dw.dim_cards dc ON ft.card_sk = dc.card_sk
        WHERE dc.card_sk IS NULL
    """)
    assert orphans == 0, f"{orphans} fact rows have a card_sk with no matching dim_cards row"


def test_fact_all_merchant_sks_resolve():
    """Every merchant_sk in fact_transactions must exist in dim_merchants."""
    orphans = query_scalar("""
        SELECT COUNT(*) FROM dw.fact_transactions ft
        LEFT JOIN dw.dim_merchants dm ON ft.merchant_sk = dm.merchant_sk
        WHERE dm.merchant_sk IS NULL
    """)
    assert orphans == 0, f"{orphans} fact rows have a merchant_sk with no matching dim_merchants row"
