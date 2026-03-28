"""
marts.py
========
Layer 4 of the ClearSpend data pipeline: Data Marts.

RESPONSIBILITY:
    Create the business-facing data mart views in the mart schema
    by executing the DDL in sql/ddl/03_mart_schema.sql.

    Additionally runs a post-creation smoke test on each view to confirm
    they return data and logs summary statistics for each mart.

MART VIEWS CREATED:
    mart.finance_summary          -> Finance team (monthly KPIs)
    mart.finance_by_location      -> Finance team (US / International / Online split)
    mart.finance_by_state         -> Finance team (geographic breakdown by state/country)
    mart.finance_by_category      -> Finance team (category breakdown)
    mart.finance_by_zip           -> Finance team (zip-level geographic drill-down)
    mart.customer_analytics       -> Customer Analytics team
    mart.suspicious_transactions  -> Customer Analytics team (fraud flags)
    mart.merchant_summary         -> Merchant Partnerships team
    mart.merchant_category_growth -> Merchant Partnerships team (MoM growth)

USAGE:
    python src/marts.py                    # run standalone
    called automatically by pipeline.py

AUTHOR:     Jonah Knief (i6263747) | Arthem Vysotskyi (i6327809) | Lyan Eleraky (I6320604) | Loredana Lazari (I6346545)
COURSE:     Data Engineering and Data Compliance
UNIVERSITY: Maastricht University
"""

import os
import sys
import logging
from sqlalchemy import text
from db import engine

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

DDL_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'sql', 'ddl', '03_mart_schema.sql'
)


# ---------------------------------------------------------------------------
# SMOKE TESTS
# ---------------------------------------------------------------------------
SMOKE_TESTS = [
    {
        "view":        "mart.finance_summary",
        "description": "Monthly revenue by year/month",
        "query":       (
            "SELECT COUNT(*) AS months, "
            "SUM(total_revenue) AS total_rev "
            "FROM mart.finance_summary"
        ),
    },
    {
        "view":        "mart.finance_by_location",
        "description": "Revenue split — US / International / Online",
        "query":       (
            "SELECT COUNT(*) AS location_types, "
            "MAX(total_revenue) AS max_location_revenue "
            "FROM mart.finance_by_location"
        ),
    },
    {
        "view":        "mart.finance_by_state",
        "description": "Revenue by state/country with location type",
        "query":       (
            "SELECT COUNT(*) AS states, "
            "COUNT(DISTINCT merchant_location) AS location_types "
            "FROM mart.finance_by_state"
        ),
    },
    {
        "view":        "mart.finance_by_category",
        "description": "Revenue by merchant category",
        "query":       (
            "SELECT COUNT(*) AS categories "
            "FROM mart.finance_by_category"
        ),
    },
    {
        "view":        "mart.finance_by_zip",
        "description": "Revenue by ZIP code (physical transactions only)",
        "query":       (
            "SELECT COUNT(*) AS zip_codes, "
            "SUM(total_revenue) AS total_rev "
            "FROM mart.finance_by_zip"
        ),
    },
    {
        "view":        "mart.customer_analytics",
        "description": "Customer lifetime value and behaviour",
        "query":       (
            "SELECT COUNT(*) AS customers, "
            "ROUND(AVG(lifetime_value), 2) AS avg_ltv "
            "FROM mart.customer_analytics"
        ),
    },
    {
        "view":        "mart.suspicious_transactions",
        "description": "Flagged duplicate-charge transactions (same customer, amount, day, merchant)",
        "query":       (
            "SELECT COUNT(*) AS flagged "
            "FROM mart.suspicious_transactions"
        ),
    },
    {
        "view":        "mart.merchant_summary",
        "description": "Merchant transaction volume and revenue",
        "query":       (
            "SELECT COUNT(*) AS merchants, "
            "SUM(total_revenue) AS total_rev, "
            "COUNT(DISTINCT merchant_location) AS location_types "
            "FROM mart.merchant_summary"
        ),
    },
    {
        "view":        "mart.merchant_category_growth",
        "description": "Month-over-month growth by category",
        "query":       (
            "SELECT COUNT(*) AS rows, "
            "COUNT(*) FILTER (WHERE mom_growth_pct IS NULL) AS null_growth "
            "FROM mart.merchant_category_growth"
        ),
    },
    {
        "view":        "mart.customers_without_transactions",
        "description": "Registered customers with no transaction history",
        "query":       "SELECT COUNT(*) AS customers_no_txn FROM mart.customers_without_transactions",
    },
    {
        "view":        "mart.card_testing_alerts",
        "description": "Card testing pattern: 3+ small transactions across 2+ merchants in one day",
        "query":       (
            "SELECT COUNT(*) AS alerts, "
            "COUNT(DISTINCT client_id) AS flagged_customers "
            "FROM mart.card_testing_alerts"
        ),
    },
    {
        "view":        "mart.error_analysis",
        "description": "Error type breakdown from fact_transactions.error_type",
        "query":       (
            "SELECT COUNT(*) AS error_types, "
            "SUM(error_count) AS total_errors "
            "FROM mart.error_analysis"
        ),
    },
]


# ---------------------------------------------------------------------------
# DDL EXECUTION
# ---------------------------------------------------------------------------

def execute_mart_ddl() -> None:
    """
    Execute the mart schema DDL file to create all views.
    Strips comment lines before splitting on semicolons to avoid false splits
    on periods or SQL-like tokens appearing inside comment blocks.
    """
    log.info(f"\nCreating mart schema from: {DDL_PATH}")

    with open(DDL_PATH, "r", encoding="utf-8") as f:
        raw_sql = f.read()

    lines = [
        line for line in raw_sql.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    stripped_sql = "\n".join(lines)
    statements = [s.strip() for s in stripped_sql.split(";") if s.strip()]

    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()

    log.info("Mart schema created successfully.\n")


# ---------------------------------------------------------------------------
# SMOKE TESTS
# ---------------------------------------------------------------------------

def run_smoke_tests() -> None:
    """
    Run a lightweight smoke test on each mart view.
    Logs the result so the operator can immediately see if any view is empty
    or returning unexpected values.
    """
    log.info("Running mart smoke tests...")

    all_passed = True

    with engine.connect() as conn:
        for test in SMOKE_TESTS:
            try:
                result = conn.execute(text(test["query"]))
                rows   = result.fetchall()
                keys   = result.keys()

                # For multi-row results (e.g. finance_by_location) check any row > 0
                first_val = rows[0][0] if rows else 0
                status = "PASS" if (first_val or 0) > 0 else "WARN (0 rows)"

                if "WARN" in status:
                    all_passed = False

                log.info(
                    f"  [{status:<16}] {test['view']:<42} "
                    f"— {test['description']}"
                )

                for row in rows:
                    log.info(
                        "              " +
                        "  |  ".join(f"{k}: {v}" for k, v in zip(keys, row))
                    )

                print()

            except Exception as e:
                log.error(f"  [FAIL]  {test['view']} — {e}")
                all_passed = False

    if all_passed:
        log.info("All mart smoke tests passed.")
    else:
        log.warning("Some mart smoke tests returned warnings — review logs above.")

    print()


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

def run_marts() -> None:
    log.info("=" * 60)
    log.info("CLEARSPEND PIPELINE — LAYER 4: DATA MARTS")
    log.info("=" * 60)

    execute_mart_ddl()
    run_smoke_tests()

    log.info("=" * 60)
    log.info("DATA MARTS COMPLETE")
    log.info("=" * 60)


if __name__ == "__main__":
    run_marts()