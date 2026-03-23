"""
transform.py
============
Layer 2 of the ClearSpend data pipeline: Transformation.

RESPONSIBILITY:
    Read from the raw schema and produce clean, standardised staging tables
    in the dw schema by executing the SQL transform scripts.

DESIGN PHILOSOPHY (Python orchestrates, SQL defines):
    The actual transformation logic lives in the sql/transforms/*.sql files.
    This Python script is the orchestrator — it reads those SQL files,
    executes them in the correct order, and logs the results.

    This separation means:
    - A marker/reviewer can open the SQL files and see the transformation
      logic clearly, without reading Python
    - SQL can be tested and modified independently
    - The execution order and logging are controlled here in Python

EXECUTION ORDER:
    1. stg_mcc.sql          — must run first; stg_transactions joins to it
    2. stg_users.sql
    3. stg_cards.sql
    4. stg_transactions.sql — runs last; joins to mcc for enrichment

USAGE:
    python src/transform.py                # run standalone
    called automatically by pipeline.py

AUTHOR:     Jonah Knief (i6263747) | Artem Vysotskyi (...) | Lyan Eleraky (...) | Loredana Lazari
COURSE:     Data Engineering and Data Compliance
UNIVERSITY: Maastricht University
"""

import os
import sys
import logging
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Force UTF-8 console output on all platforms (Windows defaults to cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DATABASE CONNECTION
# ---------------------------------------------------------------------------
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

DB_URL = (
    f"postgresql+psycopg2://"
    f"{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}"
    f"/{os.getenv('DB_NAME')}"
)

engine = create_engine(DB_URL, echo=False)


# ---------------------------------------------------------------------------
# SQL FILE PATHS
# Ordered deliberately: mcc must precede transactions (join dependency)
# ---------------------------------------------------------------------------
SQL_DIR = os.path.join(os.path.dirname(__file__), '..', 'sql', 'transforms')

TRANSFORM_SCRIPTS = [
    ("MCC reference",    os.path.join(SQL_DIR, "stg_mcc.sql")),
    ("Users",            os.path.join(SQL_DIR, "stg_users.sql")),
    ("Cards",            os.path.join(SQL_DIR, "stg_cards.sql")),
    ("Transactions",     os.path.join(SQL_DIR, "stg_transactions.sql")),
]

# ---------------------------------------------------------------------------
# QUALITY CHECKS
# Run after each transform to report data quality stats in real time.
# Each entry maps the transform label to a list of (description, sql) pairs.
# ---------------------------------------------------------------------------
QUALITY_CHECKS = {
    "MCC reference": [
        ("Invalid MCC code (<=0)",
         "SELECT COUNT(*) FROM dw.stg_mcc WHERE mcc_code <= 0"),
        ("NULL mcc_description",
         "SELECT COUNT(*) FROM dw.stg_mcc WHERE mcc_description IS NULL"),
    ],
    "Users": [
        ("employment_status mapped to 'Unknown'",
         "SELECT COUNT(*) FROM dw.stg_users WHERE employment_status = 'Unknown'"),
        ("education_level mapped to 'Unknown'",
         "SELECT COUNT(*) FROM dw.stg_users WHERE education_level = 'Unknown'"),
        ("NULL yearly_income (parse failed)",
         "SELECT COUNT(*) FROM dw.stg_users WHERE yearly_income IS NULL"),
        ("NULL per_capita_income (parse failed)",
         "SELECT COUNT(*) FROM dw.stg_users WHERE per_capita_income IS NULL"),
        ("NULL total_debt (parse failed)",
         "SELECT COUNT(*) FROM dw.stg_users WHERE total_debt IS NULL"),
    ],
    "Cards": [
        ("card_brand mapped to 'Unknown'",
         "SELECT COUNT(*) FROM dw.stg_cards WHERE card_brand = 'Unknown'"),
        ("card_type mapped to 'Unknown'",
         "SELECT COUNT(*) FROM dw.stg_cards WHERE card_type = 'Unknown'"),
        ("issuer_risk_rating mapped to 'Unknown'",
         "SELECT COUNT(*) FROM dw.stg_cards WHERE issuer_risk_rating = 'Unknown'"),
        ("NULL credit_limit (parse failed)",
         "SELECT COUNT(*) FROM dw.stg_cards WHERE credit_limit IS NULL"),
        ("NULL expires (parse failed)",
         "SELECT COUNT(*) FROM dw.stg_cards WHERE expires IS NULL"),
        ("NULL acct_open_date (parse failed)",
         "SELECT COUNT(*) FROM dw.stg_cards WHERE acct_open_date IS NULL"),
    ],
    "Transactions": [
        ("Refund transactions (is_refund = TRUE)",
         "SELECT COUNT(*) FROM dw.stg_transactions WHERE is_refund = TRUE"),
        ("Online transactions (is_online = TRUE)",
         "SELECT COUNT(*) FROM dw.stg_transactions WHERE is_online = TRUE"),
        ("Error transactions (is_error = TRUE)",
         "SELECT COUNT(*) FROM dw.stg_transactions WHERE is_error = TRUE"),
        ("NULL mcc_code (online/no-MCC transactions)",
         "SELECT COUNT(*) FROM dw.stg_transactions WHERE mcc_code IS NULL"),
        ("merchant_state = 'UNKNOWN' (null/blank in source)",
         "SELECT COUNT(*) FROM dw.stg_transactions WHERE merchant_state = 'UNKNOWN'"),
        ("merchant_state = 'ONLINE' (online in source)",
         "SELECT COUNT(*) FROM dw.stg_transactions WHERE merchant_state = 'ONLINE'"),
    ],
}


def run_sql_file(label: str, sql_path: str) -> None:
    """
    Read a SQL file and execute all statements within it.

    Each SQL file may contain multiple statements (CREATE TABLE, INSERT,
    CREATE INDEX etc.) separated by semicolons. We split on semicolons
    and execute each statement individually within a single transaction.

    Args:
        label:    Human-readable label for logging
        sql_path: Path to the .sql file to execute
    """
    log.info(f"  Running transform: {label}  ({os.path.basename(sql_path)})")

    with open(sql_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    # Split into individual statements, filtering out empty strings
    # that result from trailing semicolons or blank lines
    statements = [
        s.strip()
        for s in sql_content.split(";")
        if s.strip() and not all(
            line.strip() == '' or line.strip().startswith('--')
            for line in s.strip().splitlines()
)
    ]

    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()

    log.info(f"  Completed: {label}")


def log_quality_summary(label: str) -> None:
    """
    After a transform runs, query the resulting staging table for known
    data quality indicators and log them in real time. This makes it
    immediately visible how many values were unmappable, NULL, or flagged.
    """
    checks = QUALITY_CHECKS.get(label, [])
    if not checks:
        return

    log.info(f"  Quality summary — {label}:")
    with engine.connect() as conn:
        for description, sql in checks:
            count = conn.execute(text(sql)).scalar()
            # Warn on NULLs in critical fields; info for expected/mapped values
            if count > 0 and "NULL" in description and "mcc_code" not in description:
                log.warning(f"    {description}: {count:,}")
            else:
                log.info(f"    {description}: {count:,}")
        print()  # blank line after each summary for readability


def log_row_counts() -> None:
    """
    After all transforms complete, log the row count of each staging table.
    This provides a quick sanity check that the transforms ran correctly
    and gives a baseline for data quality monitoring.
    """
    staging_tables = [
        "dw.stg_mcc",
        "dw.stg_users",
        "dw.stg_cards",
        "dw.stg_transactions",
    ]

    log.info("  Staging table row counts:")
    with engine.connect() as conn:
        for table in staging_tables:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = result.scalar()
            log.info(f"    {table:<30} {count:>10,} rows")


def run_transform() -> None:
    """
    Main transformation entry point.
    Executes all SQL transform scripts in order, then logs row counts.
    """
    log.info("=" * 60)
    log.info("CLEARSPEND PIPELINE — LAYER 2: TRANSFORMATION")
    log.info("=" * 60)

    # Ensure the target schema exists before running any transforms
    log.info("  Ensuring schema 'dw' exists...\n")
    with engine.connect() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS dw"))
        conn.commit()

    # Execute each SQL transform file in order, followed by quality checks
    for label, sql_path in TRANSFORM_SCRIPTS:
        run_sql_file(label, sql_path)
        log_quality_summary(label)

    # Log row counts as a post-transform sanity check
    log_row_counts()

    log.info("=" * 60)
    log.info("TRANSFORMATION COMPLETE")
    log.info("=" * 60)


if __name__ == "__main__":
    run_transform()
