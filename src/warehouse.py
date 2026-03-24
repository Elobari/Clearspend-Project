"""
warehouse.py
============
Layer 3 of the ClearSpend data pipeline: Warehouse Build.

RESPONSIBILITY:
    Build the star schema in the dw schema by:
    1. Executing the dw DDL (creates dim and fact table structures)
    2. Populating dimension tables from staging data
    3. Implementing SCD Type 2 logic for dim_customers
    4. Populating fact_transactions with surrogate key lookups

DESIGN DECISIONS:
    - Dimension tables are populated BEFORE the fact table (FK constraint order)
    - dim_date is pre-populated by the DDL file itself (no Python needed)
    - SCD Type 2 on dim_customers: new rows inserted for changed records,
      old rows updated with valid_to and is_current = FALSE
    - Unknown/missing dimension members get a -1 sentinel surrogate key
      rather than NULL, preserving fact rows while flagging gaps
    - dim_customers and dim_cards use pandas default insert (no method="multi")
      to avoid PostgreSQL parameter limit and float/NUMERIC type mismatch issues

STAR SCHEMA LOAD ORDER:
    1. DDL execution         — creates all table structures
    2. dim_customers         — SCD Type 2
    3. dim_cards
    4. dim_merchants         — enriched with MCC descriptions
    5. fact_transactions     — surrogate key lookups to all 4 dims

USAGE:
    python src/warehouse.py                # run standalone
    called automatically by pipeline.py

AUTHOR:     Jonah Knief (i6263747) | Artem Vysotskyi (...) | Lyan Eleraky (...) | Loredana Lazari
COURSE:     Data Engineering and Data Compliance
UNIVERSITY: Maastricht University
"""

import io
import os
import sys
import logging
import pandas as pd

# Force UTF-8 console output on all platforms (Windows defaults to cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import date
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
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

DB_URL = (
    f"postgresql+psycopg2://"
    f"{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}"
    f"/{os.getenv('DB_NAME')}"
)

engine = create_engine(DB_URL, echo=False, connect_args={"client_encoding": "utf8"})

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------
DDL_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'sql', 'ddl', '02_dw_schema.sql'
)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _parse_sql_statements(sql_content: str) -> list[str]:
    """
    Split a SQL file into individual executable statements.
    Skips chunks that are entirely comments or blank lines.
    """
    statements = []
    for chunk in sql_content.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        non_blank_lines = [l for l in chunk.splitlines() if l.strip()]
        if all(l.strip().startswith("--") for l in non_blank_lines):
            continue
        statements.append(chunk)
    return statements


def execute_ddl(path: str) -> None:
    """Execute the dw schema DDL file (creates tables and populates dim_date)."""
    log.info(f"Executing DW DDL: {path}")
    with open(path, "r", encoding="utf-8") as f:
        sql = f.read()
    with engine.connect() as conn:
        for stmt in _parse_sql_statements(sql):
            conn.execute(text(stmt))
        conn.commit()
    log.info("DW DDL complete — all dimension and fact tables created.")


def log_table_count(table: str) -> None:
    """Log the row count of a warehouse table after population."""
    with engine.connect() as conn:
        count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
    log.info(f"  {table:<35} {count:>10,} rows")


def copy_df_to_table(df: pd.DataFrame, table: str, schema: str, conn) -> None:
    # Stream a DataFrame into PostgreSQL using the COPY protocol.
    # NaN values are written as the literal string \N — PostgreSQL's NULL marker
    # for COPY format. This is 10-50x faster than INSERT statements and handles
    # NULL integers correctly, avoiding the float/NaN type mismatch that breaks
    # method="multi".
    buffer = io.StringIO()
    df.to_csv(buffer, index=False, header=False, na_rep="\\N")
    buffer.seek(0)
    raw_conn = conn.connection
    with raw_conn.cursor() as cur:
        columns = ", ".join(f'"{c}"' for c in df.columns)
        cur.copy_expert(
            f"COPY {schema}.{table} ({columns}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')",
            buffer
        )


# ---------------------------------------------------------------------------
# DIMENSION LOADERS
# ---------------------------------------------------------------------------

def load_dim_customers() -> None:
    """
    Populate dw.dim_customers from dw.stg_users with SCD Type 2 logic.

    SCD Type 2 strategy:
    - On first run: all customers are inserted as new current records
    - On subsequent runs:
        1. Check if yearly_income or employment_status has changed
           for any existing client_id
        2. If changed: set valid_to = today, is_current = FALSE on the old
           record, then insert a new current record
        3. If unchanged: skip (no action needed)

    Uses pandas default insert (not method="multi") to avoid PostgreSQL's
    65,535 parameter limit and float/NUMERIC type coercion issues.
    """
    log.info("  Loading dim_customers (SCD Type 2)...")

    stg = pd.read_sql("SELECT * FROM dw.stg_users", engine)
    log.info(f"    Staging rows: {len(stg):,}")

    with engine.connect() as conn:
        existing_count = conn.execute(
            text("SELECT COUNT(*) FROM dw.dim_customers WHERE is_current = TRUE")
        ).scalar()

    if existing_count == 0:
        # ── FIRST RUN: insert all customers as current records ──
        log.info("    First load — inserting all customers as current records.")

        # Deduplicate by client_id — source data can contain duplicate user rows.
        # Keep the last occurrence so we always get exactly one current record
        # per client, which is the SCD Type 2 invariant.
        before = len(stg)
        stg = stg.drop_duplicates(subset="client_id", keep="last")
        removed = before - len(stg)
        if removed > 0:
            log.info(f"    Deduplication: removed {removed:,} duplicate client_id rows "
                     f"({before:,} → {len(stg):,} unique customers)")
        else:
            log.info(f"    Deduplication: no duplicates found — all {len(stg):,} rows are unique")

        stg["valid_from"] = date.today()
        stg["valid_to"]   = None
        stg["is_current"] = True

        stg.to_sql(
            "dim_customers", engine,
            schema="dw",
            if_exists="append",
            index=False
            # No method="multi" — avoids parameter limit and NUMERIC type issues
        )

    else:
        # ── SUBSEQUENT RUNS: SCD Type 2 change detection ──
        existing = pd.read_sql(
            "SELECT client_id, yearly_income, employment_status, customer_sk "
            "FROM dw.dim_customers WHERE is_current = TRUE",
            engine
        )

        merged = stg.merge(existing, on="client_id", how="left", suffixes=("_new", "_old"))

        changed = merged[
            (merged["yearly_income_new"] != merged["yearly_income_old"]) |
            (merged["employment_status_new"] != merged["employment_status_old"])
        ]

        if len(changed) > 0:
            log.info(f"    Detected {len(changed):,} changed customer records — applying SCD2")

            today = date.today()

            # Batch UPDATE — one round-trip for all changed rows using ANY(array)
            changed_sks = changed["customer_sk"].astype(int).tolist()
            with engine.connect() as conn:
                conn.execute(text("""
                    UPDATE dw.dim_customers
                    SET valid_to = :valid_to, is_current = FALSE
                    WHERE customer_sk = ANY(:sks)
                      AND is_current = TRUE
                """), {"valid_to": today, "sks": changed_sks})
                conn.commit()

            new_records = stg[stg["client_id"].isin(changed["client_id"])].copy()
            new_records["valid_from"] = today
            new_records["valid_to"]   = None
            new_records["is_current"] = True

            new_records.to_sql(
                "dim_customers", engine,
                schema="dw",
                if_exists="append",
                index=False
            )
        else:
            log.info("    No customer changes detected — dim_customers is up to date.")

    log_table_count("dw.dim_customers")


def load_dim_cards() -> None:
    """
    Populate dw.dim_cards from dw.stg_cards.
    No SCD — card attributes are treated as static in this model.

    Uses pandas default insert (not method="multi") to avoid PostgreSQL's
    parameter limit and float/NUMERIC type coercion issues with credit_limit.
    """
    log.info("  Loading dim_cards...")

    stg = pd.read_sql("SELECT * FROM dw.stg_cards", engine)
    log.info(f"    Staging rows: {len(stg):,}")

    # Deduplicate by card_id — source data can contain duplicate card rows.
    # Without this, the fact JOIN on card_id would fan out, producing more
    # fact rows than source transactions.
    before = len(stg)
    stg = stg.drop_duplicates(subset="card_id", keep="last")
    removed = before - len(stg)
    if removed > 0:
        log.info(f"    Deduplication: removed {removed:,} duplicate card_id rows "
                 f"({before:,} → {len(stg):,} unique cards)")
    else:
        log.info(f"    Deduplication: no duplicates found — all {len(stg):,} rows are unique")

    stg.to_sql(
        "dim_cards", engine,
        schema="dw",
        if_exists="append",
        index=False
        # No method="multi" — avoids NUMERIC type mismatch for credit_limit
    )

    log_table_count("dw.dim_cards")


def load_dim_merchants() -> None:
    """
    Populate dw.dim_merchants from the distinct merchant combinations
    in dw.stg_transactions, enriched with MCC descriptions from dw.stg_mcc.

    We build the dimension from the transactions staging table (not a separate
    merchants source file) because the transactions data IS the merchant master.

    MCC descriptions are joined from stg_mcc and stored denormalised here —
    this is intentional star schema design to avoid a runtime lookup join on
    every mart query.
    """
    log.info("  Loading dim_merchants...")

    merchants_sql = """
        SELECT DISTINCT ON (t.merchant_id)
            t.merchant_id,
            t.merchant_city,
            t.merchant_state,
            t.mcc_code,
            m.mcc_description
        FROM dw.stg_transactions t
        LEFT JOIN dw.stg_mcc m ON t.mcc_code = m.mcc_code
        ORDER BY t.merchant_id
    """
    merchants = pd.read_sql(merchants_sql, engine)
    log.info(f"    Distinct merchants: {len(merchants):,}")

    null_mcc = merchants["mcc_description"].isna().sum()
    merchants["mcc_description"] = merchants["mcc_description"].fillna("Unknown Category")
    if null_mcc > 0:
        log.info(f"    {null_mcc:,} merchants had no MCC match — description filled with 'Unknown Category'")

    # Use COPY protocol: faster than to_sql() and handles NULL mcc_code integers
    # correctly — COPY writes NaN as \N (PostgreSQL NULL) rather than a float,
    # which avoids the type mismatch that breaks method="multi".
    with engine.begin() as conn:
        copy_df_to_table(merchants, "dim_merchants", "dw", conn)

    log_table_count("dw.dim_merchants")


def load_fact_transactions() -> None:
    """
    Populate dw.fact_transactions by joining staging transactions to all
    four dimension tables to resolve surrogate keys.

    Done as a server-side SQL INSERT rather than pandas because the
    transaction table is ~13M rows — a server-side join is vastly more
    efficient than loading all rows into memory for merging.

    COALESCE(-1) handles any unmatched dimension members with a sentinel
    value rather than dropping the fact row.
    """
    log.info("  Loading fact_transactions (server-side SQL insert)...")

    insert_sql = """
        INSERT INTO dw.fact_transactions (
            transaction_id,
            date_sk,
            customer_sk,
            card_sk,
            merchant_sk,
            amount,
            is_refund,
            is_online,
            is_error,
            error_type
        )
        SELECT
            t.transaction_id,
            t.date_sk,
            COALESCE(c.customer_sk, -1)  AS customer_sk,
            COALESCE(cd.card_sk, -1)     AS card_sk,
            COALESCE(m.merchant_sk, -1)  AS merchant_sk,
            t.amount,
            t.is_refund,
            t.is_online,
            t.is_error,
            t.error_type
        FROM dw.stg_transactions t
        LEFT JOIN dw.dim_customers c
            ON t.client_id = c.client_id
            AND c.is_current = TRUE
        LEFT JOIN dw.dim_cards cd
            ON t.card_id = cd.card_id
        LEFT JOIN dw.dim_merchants m
            ON t.merchant_id = m.merchant_id
    """

    with engine.connect() as conn:
        conn.execute(text(insert_sql))
        conn.commit()

    log_table_count("dw.fact_transactions")

    # Report any rows that used the sentinel key (-1) due to unmatched dimensions
    with engine.connect() as conn:
        sentinel_count = conn.execute(text("""
            SELECT COUNT(*) FROM dw.fact_transactions
            WHERE customer_sk = -1 OR card_sk = -1 OR merchant_sk = -1
        """)).scalar()
    if sentinel_count > 0:
        log.warning(f"    {sentinel_count:,} fact rows used sentinel key (-1) — "
                    f"these transactions had no matching dimension record")
    else:
        log.info("    All fact rows matched to dimension records — no sentinel keys used")


def run_warehouse() -> None:
    """
    Main warehouse build entry point.
    Executes DDL, then loads all dimensions and the fact table in order.
    """
    log.info("=" * 60)
    log.info("CLEARSPEND PIPELINE — LAYER 3: WAREHOUSE BUILD")
    log.info("=" * 60)

    print()
    execute_ddl(DDL_PATH)
    print()

    load_dim_customers()
    print()
    load_dim_cards()
    print()
    load_dim_merchants()
    print()
    load_fact_transactions()
    print()

    log.info("=" * 60)
    log.info("WAREHOUSE BUILD COMPLETE")
    log.info("=" * 60)


if __name__ == "__main__":
    run_warehouse()
