"""
test_ingestion.py
=================
Data quality tests for the ingestion layer (raw schema).

Tests verify that:
    - All raw tables exist and are populated
    - Row counts are within expected ranges
    - Primary key columns have no nulls
    - Critical foreign key columns have no nulls

AUTHOR:     Jonah Knief (i6263747) | Artem Vysotskyi (...) | Lyan Eleraky (...) | Loredana Lazari
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
# ROW COUNT TESTS - From Excel Row Counts
# ---------------------------------------------------------------------------

def test_raw_users_row_count():
    """users_data.csv has 2,020 rows — expect all to load."""
    count = query_scalar("SELECT COUNT(*) FROM raw.users")
    assert count == 2020, f"Expected 2020 users, got {count}"


def test_raw_cards_row_count():
    """cards_data.csv has 6,207 rows — expect all to load."""
    count = query_scalar("SELECT COUNT(*) FROM raw.cards")
    assert count == 6207, f"Expected 6207 cards, got {count}"


def test_raw_mcc_row_count():
    """mcc_data.csv has 127 rows — expect all to load."""
    count = query_scalar("SELECT COUNT(*) FROM raw.mcc")
    assert count == 127, f"Expected 127 MCC rows, got {count}"


def test_raw_transactions_not_empty(): # 13.305.915 rows in transactions_data.csv, but we just want to check it's not empty
    """Transactions table should have rows after ingestion."""
    count = query_scalar("SELECT COUNT(*) FROM raw.transactions")
    assert count > 0, "raw.transactions is empty — ingestion may have failed"


# ---------------------------------------------------------------------------
# NULL CHECKS ON KEY COLUMNS
# ---------------------------------------------------------------------------

def test_raw_users_no_null_id():
    nulls = query_scalar("SELECT COUNT(*) FROM raw.users WHERE id IS NULL")
    assert nulls == 0, f"raw.users has {nulls} rows with null id"


def test_raw_cards_no_null_id():
    nulls = query_scalar("SELECT COUNT(*) FROM raw.cards WHERE id IS NULL")
    assert nulls == 0, f"raw.cards has {nulls} rows with null id"


def test_raw_transactions_no_null_id():
    nulls = query_scalar("SELECT COUNT(*) FROM raw.transactions WHERE id IS NULL")
    assert nulls == 0, f"raw.transactions has {nulls} rows with null id"


def test_raw_transactions_no_null_client_id():
    nulls = query_scalar("SELECT COUNT(*) FROM raw.transactions WHERE client_id IS NULL")
    assert nulls == 0, f"raw.transactions has {nulls} rows with null client_id"


def test_raw_mcc_no_null_code():
    nulls = query_scalar("SELECT COUNT(*) FROM raw.mcc WHERE code IS NULL")
    assert nulls == 0, f"raw.mcc has {nulls} rows with null code"
