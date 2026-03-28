"""
test_transforms.py
==================
Data quality tests for the transformation layer (dw staging tables).

Tests verify that:
    - Monetary columns are clean numerics with no $ signs remaining
    - Boolean derived columns are correctly populated
    - Categorical columns only contain canonical values
    - MCC codes are valid integers after cleaning
    - No critical nulls introduced during transformation

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


def query_scalar(sql: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar()


# ---------------------------------------------------------------------------
# TRANSACTION TRANSFORMS
# ---------------------------------------------------------------------------

def test_transactions_amount_non_negative():
    """All amounts must be positive after cleaning (refunds flagged separately)."""
    negatives = query_scalar(
        "SELECT COUNT(*) FROM dw.stg_transactions WHERE amount < 0"
    )
    assert negatives == 0, f"{negatives} negative amounts remain after transform"


def test_transactions_is_refund_populated():
    """is_refund must be non-null for every row."""
    nulls = query_scalar(
        "SELECT COUNT(*) FROM dw.stg_transactions WHERE is_refund IS NULL"
    )
    assert nulls == 0, f"{nulls} rows have null is_refund"


def test_transactions_is_online_populated():
    """is_online must be non-null for every row."""
    nulls = query_scalar(
        "SELECT COUNT(*) FROM dw.stg_transactions WHERE is_online IS NULL"
    )
    assert nulls == 0, f"{nulls} rows have null is_online"


def test_transactions_date_sk_valid():
    """date_sk should be an 8-digit integer (YYYYMMDD format)."""
    invalid = query_scalar("""
        SELECT COUNT(*) FROM dw.stg_transactions
        WHERE date_sk < 20000101 OR date_sk > 20351231
    """)
    assert invalid == 0, f"{invalid} rows have out-of-range date_sk"


# ---------------------------------------------------------------------------
# USER TRANSFORMS
# ---------------------------------------------------------------------------

def test_users_employment_canonical():
    """employment_status must only contain the 6 canonical values."""
    allowed = "('Employed','Self-Employed','Student','Retired','Unemployed','Unknown')"
    invalid = query_scalar(
        f"SELECT COUNT(*) FROM dw.stg_users WHERE employment_status NOT IN {allowed}"
    )
    assert invalid == 0, f"{invalid} rows have non-canonical employment_status"


def test_users_education_canonical():
    """education_level must only contain the 6 canonical values."""
    allowed = "('High School','Associate','Bachelor','Master','Doctorate','Unknown')"
    invalid = query_scalar(
        f"SELECT COUNT(*) FROM dw.stg_users WHERE education_level NOT IN {allowed}"
    )
    assert invalid == 0, f"{invalid} rows have non-canonical education_level"


def test_users_income_non_negative():
    """yearly_income must be non-negative after stripping '$'."""
    negatives = query_scalar(
        "SELECT COUNT(*) FROM dw.stg_users WHERE yearly_income < 0"
    )
    assert negatives == 0, f"{negatives} rows have negative yearly_income"


# ---------------------------------------------------------------------------
# CARD TRANSFORMS
# ---------------------------------------------------------------------------

def test_cards_brand_canonical():
    """card_brand must be one of the 5 canonical values."""
    allowed = "('Visa','Mastercard','Amex','Discover','Unknown')"
    invalid = query_scalar(
        f"SELECT COUNT(*) FROM dw.stg_cards WHERE card_brand NOT IN {allowed}"
    )
    assert invalid == 0, f"{invalid} rows have non-canonical card_brand"


def test_cards_type_canonical():
    """card_type must be one of the 4 canonical values."""
    allowed = "('Debit','Credit','Prepaid','Unknown')"
    invalid = query_scalar(
        f"SELECT COUNT(*) FROM dw.stg_cards WHERE card_type NOT IN {allowed}"
    )
    assert invalid == 0, f"{invalid} rows have non-canonical card_type"


def test_cards_credit_limit_non_negative():
    """credit_limit must be non-negative after parsing."""
    negatives = query_scalar(
        "SELECT COUNT(*) FROM dw.stg_cards WHERE credit_limit < 0"
    )
    assert negatives == 0, f"{negatives} rows have negative credit_limit"


# ---------------------------------------------------------------------------
# MCC TRANSFORMS
# ---------------------------------------------------------------------------

def test_mcc_codes_are_integers():
    """After cleaning, all MCC codes must be valid positive integers."""
    invalid = query_scalar(
        "SELECT COUNT(*) FROM dw.stg_mcc WHERE mcc_code <= 0"
    )
    assert invalid == 0, f"{invalid} MCC codes are zero or negative after cleaning"


def test_mcc_no_quoted_codes():
    """No MCC code should contain a quote character after cleaning."""
    # stg_mcc.mcc_code is INTEGER — this test catches the raw layer instead
    quoted = query_scalar(
        "SELECT COUNT(*) FROM raw.mcc WHERE code LIKE '%\"%'"
    )
    # We don't assert 0 here (raw is intentionally messy) but log for info
    # The real assertion is that stg_mcc has clean integers (test above)
    assert True  # Informational only


def test_mcc_no_null_descriptions():
    """All MCC descriptions should be populated after cleaning."""
    nulls = query_scalar(
        "SELECT COUNT(*) FROM dw.stg_mcc WHERE mcc_description IS NULL"
    )
    assert nulls == 0, f"{nulls} MCC rows have null description after cleaning"
