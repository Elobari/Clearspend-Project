"""
marts.py
========
Layer 4 of the ClearSpend data pipeline: Data Marts.

RESPONSIBILITY:
    Create the three business-facing data mart views in the mart schema
    by executing the DDL in sql/ddl/03_mart_schema.sql.

    Additionally runs a post-creation smoke test on each view to confirm
    they return data and logs summary statistics for each mart.

MART VIEWS CREATED:
    mart.finance_summary          → Finance team
    mart.finance_by_state         → Finance team (geographic breakdown)
    mart.finance_by_category      → Finance team (category breakdown)
    mart.customer_analytics       → Customer Analytics team
    mart.suspicious_transactions  → Customer Analytics team (fraud flags)
    mart.merchant_summary         → Merchant Partnerships team
    mart.merchant_category_growth → Merchant Partnerships team (MoM growth)

USAGE:
    python src/marts.py                    # run standalone
    called automatically by pipeline.py

AUTHOR:     Jonah Knief (i6263747) | Artem Vysotskyi (...) | Lyan Eleraky (...) | Loredana Lazari
COURSE:     Data Engineering and Data Compliance
UNIVERSITY: Maastricht University
"""

import os
import logging
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

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
load_dotenv()

DB_URL = (
    f"postgresql+psycopg2://"
    f"{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}"
    f"/{os.getenv('DB_NAME')}"
)

engine = create_engine(DB_URL, echo=False)

DDL_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'sql', 'ddl', '03_mart_schema.sql'
)

# ---------------------------------------------------------------------------
# SMOKE TESTS
# A smoke test is a lightweight check that a view returns at least one row
# and that key columns are populated. It is not a full data quality test
# (those live in tests/) but provides immediate feedback after mart creation.
# ---------------------------------------------------------------------------
SMOKE_TESTS = [
    {
        "view":        "mart.finance_summary",
        "description": "Monthly revenue by year/month",
        "query":       "SELECT COUNT(*) AS months, SUM(total_revenue) AS total_rev FROM mart.finance_summary",
    },
    {
        "view":        "mart.finance_by_state",
        "description": "Revenue by US state",
        "query":       "SELECT COUNT(*) AS states FROM mart.finance_by_state",
    },
    {
        "view":        "mart.finance_by_category",
        "description": "Revenue by merchant category",
        "query":       "SELECT COUNT(*) AS categories FROM mart.finance_by_category",
    },
    {
        "view":        "mart.customer_analytics",
        "description": "Customer lifetime value and behaviour",
        "query":       (
            "SELECT COUNT(*) AS customers, "
            "ROUND(AVG(lifetime_value),2) AS avg_ltv "
            "FROM mart.customer_analytics"
        ),
    },
    {
        "view":        "mart.suspicious_transactions",
        "description": "Flagged suspicious transactions",
        "query":       "SELECT COUNT(*) AS flagged FROM mart.suspicious_transactions",
    },
    {
        "view":        "mart.merchant_summary",
        "description": "Merchant transaction volume and revenue",
        "query":       (
            "SELECT COUNT(*) AS merchants, "
            "SUM(total_revenue) AS total_rev "
            "FROM mart.merchant_summary"
        ),
    },
    {
        "view":        "mart.merchant_category_growth",
        "description": "Month-over-month growth by category",
        "query":       "SELECT COUNT(*) AS rows FROM mart.merchant_category_growth",
    },
]


def execute_mart_ddl() -> None:
    """
    Execute the mart schema DDL file to create all views.
    The DDL drops and recreates the mart schema for a clean run.
    """
    log.info(f"\nCreating mart schema from: {DDL_PATH}")

    with open(DDL_PATH, "r") as f:
        sql = f.read()

    with engine.connect() as conn:
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()

    log.info("Mart schema created successfully.\n")


def run_smoke_tests() -> None:
    """
    Run a lightweight smoke test on each mart view.
    Logs the result so the operator can immediately see if any view is empty
    or returning unexpected nulls.
    """
    log.info("Running mart smoke tests...")

    all_passed = True

    with engine.connect() as conn:
        for test in SMOKE_TESTS:
            try:
                result = conn.execute(text(test["query"]))
                row = result.fetchone()

                # A view returning zero rows is flagged as a warning
                first_val = row[0] if row else 0
                status = "PASS" if (first_val or 0) > 0 else "WARN (0 rows)"

                if "WARN" in status:
                    all_passed = False

                log.info(
                    f"  [{status:<16}] {test['view']:<40} "
                    f"— {test['description']}"
                )
                
                # Log all returned columns for observability
                if row:
                    col_names = result.keys()
                    for col, val in zip(col_names, row):
                        log.info(f"              {col}: {val}")
                
                print()  # Blank line between tests

            except Exception as e:
                log.error(f"  [FAIL]  {test['view']} — {e}")
                all_passed = False

    if all_passed:
        log.info("All mart smoke tests passed.")
    else:
        log.warning("Some mart smoke tests returned warnings — review logs above.")
    
    print()  # Blank line after tests


def run_marts() -> None:
    """
    Main mart creation entry point.
    Creates all mart views and runs smoke tests to verify them.
    """
    log.info("=" * 60)
    log.info("CLEARSPEND PIPELINE — LAYER 4: DATA MARTS")
    log.info("=" * 60)

    # Create all mart views from the DDL file
    execute_mart_ddl()

    # Verify each mart returns data
    run_smoke_tests()

    log.info("=" * 60)
    log.info("DATA MARTS COMPLETE")
    log.info("=" * 60)


if __name__ == "__main__":
    run_marts()
