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

REJECTION LOGGING:
    Rows that are structurally unusable after transformation (e.g. MCC codes
    that cannot be parsed to a valid integer) are written to raw.rejected,
    the same table used by the ingest layer. This gives a single, complete
    audit trail of every row discarded anywhere in the pipeline.

EXECUTION ORDER:
    1. stg_mcc.sql          -- must run first; stg_transactions joins to it
    2. stg_users.sql
    3. stg_cards.sql
    4. stg_transactions.sql -- runs last; joins to mcc for enrichment

USAGE:
    python src/transform.py                # run standalone
    called automatically by pipeline.py

AUTHOR:     Jonah Knief (i6263747) | Arthem Vysotskyi (i6327809) | Lyan Eleraky (I6320604) | Loredana Lazari (I6346545)
COURSE:     Data Engineering and Data Compliance
UNIVERSITY: Maastricht University
"""

import os
import sys
import json
import logging
import pandas as pd
from sqlalchemy import text
from db import engine

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
        ("merchant_city = 'ONLINE' (online in source)",
         "SELECT COUNT(*) FROM dw.stg_transactions WHERE merchant_city = 'ONLINE'"),
    ],
}


# ---------------------------------------------------------------------------
# REJECTION LOGGING
# Writes structurally unusable rows to raw.rejected for full audit traceability.
# Called after specific transforms where known rows are discarded.
# ---------------------------------------------------------------------------

def reject_unparseable_mcc() -> None:
    """
    After stg_mcc.sql runs, identify any raw.mcc rows whose code could not
    be cleaned to a valid integer and write them to raw.rejected.

    These rows are excluded from stg_mcc by the WHERE filter in stg_mcc.sql.
    This function provides the audit trail for those dropped rows — without
    this, they would disappear silently with no record in the pipeline.

    Unparseable examples: 'NOTE', blank strings, codes with non-numeric
    characters that survive the quote/prefix stripping.
    """
    unparseable_sql = """
        SELECT code, description, notes, updated_by
        FROM raw.mcc
        WHERE code IS NULL
           OR TRIM(code) = ''
           OR REGEXP_REPLACE(
                REGEXP_REPLACE(
                    REPLACE(TRIM(code), '"', ''),
                    '^[Mm][Cc][Cc]', ''
                ),
                '\s', '', 'g'
              ) !~ '^\d+$'
    """

    with engine.connect() as conn:
        rows = conn.execute(text(unparseable_sql)).fetchall()

    if not rows:
        log.info("    Unparseable MCC rows written to raw.rejected: 0")
        return

    rejected = []
    for row in rows:
        code, description, notes, updated_by = row
        rejected.append({
            "source_table":     "raw.mcc",
            "source_row":       json.dumps({
                "code":        code,
                "description": description,
                "notes":       notes,
                "updated_by":  updated_by,
            }, default=str),
            "rejection_reason": (
                "MCC code cannot be parsed to a valid integer after "
                "stripping quotes and MCC prefix — cannot join to transactions.mcc"
            ),
        })

    pd.DataFrame(rejected).to_sql(
        "rejected", engine, schema="raw",
        if_exists="append", index=False, method="multi"
    )
    log.warning(
        f"    Unparseable MCC rows written to raw.rejected: {len(rejected)} "
        f"(codes: {[r['source_row'] for r in rejected[:5]]}{'...' if len(rejected) > 5 else ''})"
    )


# ---------------------------------------------------------------------------
# SQL EXECUTION
# ---------------------------------------------------------------------------

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

    # Strip comment lines before splitting to avoid false splits on
    # periods or SQL-like tokens appearing inside comment blocks.
    lines = [
        line for line in sql_content.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    stripped_sql = "\n".join(lines)
    statements = [s.strip() for s in stripped_sql.split(";") if s.strip()]

    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()

    log.info(f"  Completed: {label}")


def log_quality_summary(label: str) -> None:
    """
    After a transform runs, query the resulting staging table for known
    data quality indicators and log them in real time.
    """
    checks = QUALITY_CHECKS.get(label, [])
    if not checks:
        return

    log.info(f"  Quality summary — {label}:")
    with engine.connect() as conn:
        for description, sql in checks:
            count = conn.execute(text(sql)).scalar()
            if count > 0 and "NULL" in description and "mcc_code" not in description:
                log.warning(f"    {description}: {count:,}")
            else:
                log.info(f"    {description}: {count:,}")
        print()


def log_row_counts() -> None:
    """Log the row count of each staging table as a post-transform sanity check."""
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


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

def run_transform() -> None:
    """
    Main transformation entry point.
    Executes all SQL transform scripts in order, runs quality checks,
    logs rejections, then logs final row counts.
    """
    log.info("=" * 60)
    log.info("CLEARSPEND PIPELINE — LAYER 2: TRANSFORMATION")
    log.info("=" * 60)

    log.info("  Ensuring schema 'dw' exists...\n")
    with engine.connect() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS dw"))
        conn.commit()

    for label, sql_path in TRANSFORM_SCRIPTS:
        run_sql_file(label, sql_path)

        # After stg_mcc runs, capture unparseable rows into raw.rejected
        # before logging the quality summary so the count appears in context.
        if label == "MCC reference":
            reject_unparseable_mcc()

        log_quality_summary(label)

    log_row_counts()

    log.info("=" * 60)
    log.info("TRANSFORMATION COMPLETE")
    log.info("=" * 60)


if __name__ == "__main__":
    run_transform()