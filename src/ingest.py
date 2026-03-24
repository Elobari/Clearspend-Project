"""
ingest.py
=========
Layer 1 of the ClearSpend data pipeline: Ingestion.

RESPONSIBILITY:
    Read all four source CSV files and load them into the PostgreSQL raw schema
    as-is (typed, but not cleaned). No business logic or transformations here.

DESIGN DECISIONS:
    - All files are loaded fully into memory — the machine has sufficient RAM.
    - Rows that fail basic validation (null primary key) are written to
      raw.rejected rather than being silently dropped.
    - PostgreSQL COPY protocol is used for bulk inserts — much faster than
      multi-row INSERT statements.
    - The raw schema DDL is executed first to ensure tables exist before load.

USAGE:
    python src/ingest.py                 # run standalone
    called automatically by pipeline.py

AUTHOR:     Jonah Knief (i6263747) | Artem Vysotskyi (...) | Lyan Eleraky (...) | Loredana Lazari
COURSE:     Data Engineering and Data Compliance
UNIVERSITY: Maastricht University
"""

import os
import io
import json
import logging
import sys
import pandas as pd

# Force UTF-8 console output on all platforms (Windows defaults to cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# LOGGING SETUP
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
# FILE PATHS
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')

FILES = {
    "transactions": os.path.join(DATA_DIR, "transactions_data.csv"),
    "users":        os.path.join(DATA_DIR, "users_data.csv"),
    "cards":        os.path.join(DATA_DIR, "cards_data.csv"),
    "mcc":          os.path.join(DATA_DIR, "mcc_data.csv"),
}


# ---------------------------------------------------------------------------
# COLUMN DEFINITIONS
# Explicit dtype maps prevent pandas from silently misinterpreting columns.
# Most critically:
#   - card_number must be STRING to preserve leading zeros
#   - amount must be STRING because of the '$' prefix
#   - zip must be STRING to preserve leading zeros (e.g. "01234")
# ---------------------------------------------------------------------------
DTYPES = {
    "transactions": {
        "id":             "Int64",
        "client_id":      "Int64",
        "card_id":        "Int64",
        "amount":         "str",
        "use_chip":       "str",
        "merchant_id":    "Int64",
        "merchant_city":  "str",
        "merchant_state": "str",
        "zip":            "str",
        "mcc":            "Int64",
        "errors":         "str",
    },
    "users": {
        "id":                "Int64",
        "per_capita_income": "str",
        "yearly_income":     "str",
        "total_debt":        "str",
        "employment_status": "str",
        "education_level":   "str",
    },
    "cards": {
        "id":           "Int64",
        "client_id":    "Int64",
        "card_number":  "str",
        "credit_limit": "str",
    },
    "mcc": {
        "code":        "str",
        "description": "str",
    },
}

# Columns to drop on load — internal ops metadata not needed in the warehouse
DROP_COLUMNS = {
    "mcc": ["notes", "updated_by"],
}

# Primary key column per table — used for null PK validation
PK_COLUMNS = {
    "transactions": "id",
    "users":        "id",
    "cards":        "id",
    "mcc":          "code",
}


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
def execute_ddl(ddl_path: str) -> None:
    """Execute a SQL DDL file to (re)create the raw schema."""
    log.info(f"Executing DDL: {ddl_path}")
    with open(ddl_path, "r", encoding="utf-8") as f:
        sql = f.read()
    with engine.connect() as conn:
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()
    log.info("DDL executed successfully.\n")


# ---------------------------------------------------------------------------
# COPY-BASED BULK INSERT
# Uses PostgreSQL's COPY protocol — 10-50x faster than INSERT statements.
# ---------------------------------------------------------------------------
def copy_df_to_table(df: pd.DataFrame, table: str, schema: str, conn) -> None:
    """Stream a DataFrame into PostgreSQL using the COPY protocol."""
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
# REJECTED ROWS
# ---------------------------------------------------------------------------
def write_rejected(rows: list[dict], source_table: str) -> None:
    """Write rejected rows to raw.rejected for traceability."""
    if not rows:
        return
    pd.DataFrame(rows).to_sql(
        "rejected", engine, schema="raw",
        if_exists="append", index=False, method="multi"
    )
    log.warning(f"  Wrote {len(rows)} rejected rows from '{source_table}' to raw.rejected")


# ---------------------------------------------------------------------------
# LOAD FILE — single function for all four source files
# ---------------------------------------------------------------------------
def load_file(table_name: str, file_path: str) -> None:
    """
    Load a CSV file into its raw schema table.

    Handles all four source files uniformly:
        - Reads the full file into memory
        - Drops any columns marked for exclusion
        - Validates the primary key column (rejects null PKs)
        - Bulk loads clean rows via PostgreSQL COPY
        - Writes any rejected rows to raw.rejected

    Args:
        table_name: Target table name in the raw schema (without schema prefix)
        file_path:  Path to the source CSV file
    """
    log.info(f"Loading '{table_name}' from {file_path}")

    try:
        df = pd.read_csv(file_path, dtype=DTYPES.get(table_name, {}), low_memory=False, encoding="utf-8-sig")
        log.info(f"  Read {len(df):,} rows, {len(df.columns)} columns")

        # Drop columns marked for exclusion (e.g. mcc.notes, mcc.updated_by)
        cols_to_drop = [c for c in DROP_COLUMNS.get(table_name, []) if c in df.columns]
        if cols_to_drop:
            df.drop(columns=cols_to_drop, inplace=True)
            log.info(f"  Dropped columns: {cols_to_drop}")

        # Clean card_number: strip trailing decimal points caused by CSV float storage
        # e.g. '377303000000000.' → '377303000000000'
        if table_name == "cards" and "card_number" in df.columns:
            df["card_number"] = (
                df["card_number"]
                .str.replace(r'\.0*$', '', regex=True)
                .str.strip()
            )
            log.info("  Cleaned card_number: stripped trailing decimal points")
        
        # Validate primary key — reject rows where PK is null
        rejected = []
        pk_col = PK_COLUMNS.get(table_name)
        if pk_col and pk_col in df.columns:
            mask_invalid = df[pk_col].isna()
            if mask_invalid.any():
                for _, row in df[mask_invalid].iterrows():
                    rejected.append({
                        "source_table":     f"raw.{table_name}",
                        "source_row":       json.dumps(row.to_dict(), default=str),
                        "rejection_reason": f"Null primary key in column '{pk_col}'"
                    })
                df = df[~mask_invalid]

        # Bulk load into raw schema via COPY
        #with engine.begin() as conn:
         #   conn.execute(text(f"TRUNCATE TABLE raw.{table_name}"))
          #  copy_df_to_table(df, table_name, "raw", conn)

        # Bulk load into raw schema via COPY
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE TABLE raw.{table_name}"))
            log.info(f"  Truncated raw.{table_name} — loading fresh data")
            copy_df_to_table(df, table_name, "raw", conn)

        log.info(f"  Inserted {len(df):,} rows into raw.{table_name}")
        if rejected:
            write_rejected(rejected, table_name)
        else:
            log.info(f"  Rejected rows: 0 — all rows passed PK validation")

    except FileNotFoundError:
        log.error(f"  File not found: {file_path}")
        raise
    except Exception as e:
        log.error(f"  Failed to load '{table_name}': {e}")
        raise


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
def run_ingestion() -> None:
    log.info("=" * 60)
    log.info("CLEARSPEND PIPELINE — LAYER 1: INGESTION")
    log.info("=" * 60)

    print("")

    ddl_path = os.path.join(os.path.dirname(__file__), '..', 'sql', 'ddl', '01_raw_schema.sql')
    execute_ddl(ddl_path)

    for table_name, file_path in FILES.items():
        load_file(table_name, file_path)
        print("")

    log.info("=" * 60)
    log.info("INGESTION COMPLETE")
    log.info("=" * 60)


if __name__ == "__main__":
    run_ingestion()
