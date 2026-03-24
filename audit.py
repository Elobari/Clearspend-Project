"""
clearspend_audit.py  —  Full pipeline audit for the ClearSpend star schema
===========================================================================
Covers all four layers with checks derived from the actual SQL source files:

  Layer 1  raw.*                 row counts, NULLs, PK dupes, amount/date shapes
  Layer 2  dw.stg_*              row counts, NULLs, flag distributions, MCC drop check
  Layer 3  dw.dim_* + fact       row counts, FK orphans, SCD-2, revenue reconcile
  Layer 4  mart.*                row counts, revenue reconcile, NULL scan
  HEAD(N)  every table / view across all layers

Usage
-----
  python audit.py                            # localhost / db=clearspend
  python audit.py --db mydb --user jonah
  python audit.py --dsn "postgresql://user:pw@host/db"
  python audit.py --layer raw               # one layer only
  python audit.py --head 5                  # fewer HEAD rows
  python audit.py --no-head                 # skip HEAD previews (much faster)

Requires: psycopg2-binary  (already in your venv)
"""

import argparse
import sys
from datetime import datetime

try:
    import psycopg2
except ImportError:
    sys.exit("psycopg2 not found. Run: pip install psycopg2-binary")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="ClearSpend pipeline audit")
    p.add_argument("--dsn",      default=None)
    p.add_argument("--host",     default="localhost")
    p.add_argument("--port",     default=5432, type=int)
    p.add_argument("--db",       default="clearspend")
    p.add_argument("--user",     default=None)
    p.add_argument("--password", default=None)
    p.add_argument("--head",     default=20, type=int,  help="Rows in HEAD preview (default 20)")
    p.add_argument("--no-head",  action="store_true",   help="Skip HEAD previews entirely")
    p.add_argument("--layer",    default="all",
                   choices=["all", "raw", "stg", "dw", "mart"])
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# TERMINAL COLOURS
# ─────────────────────────────────────────────────────────────────────────────

RS   = "\033[0m"
BOLD = "\033[1m"
RED  = "\033[91m"
YEL  = "\033[93m"
GRN  = "\033[92m"
CYN  = "\033[96m"
DIM  = "\033[2m"

def hdr(title, char="="):
    w = 74
    print(f"\n{BOLD}{char*w}{RS}\n{BOLD}  {title}{RS}\n{BOLD}{char*w}{RS}")

def sub(title):
    print(f"\n{CYN}{BOLD}>>  {title}{RS}")

def ok(msg):   print(f"  {GRN}OK {RS}  {msg}")
def warn(msg): print(f"  {YEL}!! {RS}  {msg}")
def fail(msg): print(f"  {RED}XX {RS}  {msg}")
def info(msg): print(f"       {msg}")

def result(label, value, expected=None, bad_if_nonzero=False, warn_if_nonzero=False):
    if isinstance(value, (int, float)):
        val = f"{value:,}"
    else:
        val = str(value)
    exp = ""
    if isinstance(expected, int):
        exp = f"  (expected ~{expected:,})"
    elif expected is not None:
        exp = f"  (expected {expected})"
    if bad_if_nonzero and isinstance(value, int) and value > 0:
        fail(f"{label}: {val}{exp}")
    elif warn_if_nonzero and isinstance(value, int) and value > 0:
        warn(f"{label}: {val}{exp}")
    else:
        ok(f"{label}: {val}{exp}")


# ─────────────────────────────────────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def scalar(cur, sql, params=None):
    cur.execute(sql, params)
    r = cur.fetchone()
    return r[0] if r else None

def qrows(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchall()

def tbl_exists(cur, schema, table):
    return scalar(cur, """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema=%s AND table_name=%s
        )""", (schema, table))

def col_exists(cur, schema, table, column):
    return scalar(cur, """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema=%s AND table_name=%s AND column_name=%s
        )""", (schema, table, column))

def get_columns(cur, schema, table):
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema=%s AND table_name=%s
        ORDER BY ordinal_position
    """, (schema, table))
    return [r[0] for r in cur.fetchall()]

def count_nulls(cur, schema, table, column):
    return scalar(cur, f"SELECT COUNT(*) FROM {schema}.{table} WHERE {column} IS NULL")


# ─────────────────────────────────────────────────────────────────────────────
# HEAD PREVIEW
# ─────────────────────────────────────────────────────────────────────────────

def print_head(cur, schema, table, n):
    if not tbl_exists(cur, schema, table):
        warn(f"HEAD skipped -- {schema}.{table} not found")
        return
    cur.execute(f"SELECT * FROM {schema}.{table} LIMIT %s", (n,))
    cols = [d[0] for d in cur.description]
    data = cur.fetchall()
    if not data:
        info(f"HEAD {schema}.{table}: (empty table)")
        return
    MAX_W = 26
    str_data = [
        [str(v)[:MAX_W] if v is not None else "NULL" for v in row]
        for row in data
    ]
    widths = [
        min(MAX_W, max(len(c), max((len(r[i]) for r in str_data), default=0)))
        for i, c in enumerate(cols)
    ]
    hdr_row = "  " + "  ".join(c[:widths[i]].ljust(widths[i]) for i, c in enumerate(cols))
    sep_row = "  " + "  ".join("-" * w for w in widths)

    print(f"\n  {DIM}HEAD({n})  {schema}.{table}{RS}")
    print(f"  {DIM}{hdr_row.strip()}{RS}")
    print(f"  {DIM}{sep_row.strip()}{RS}")
    for row in str_data:
        print("  " + "  ".join(v.ljust(widths[i]) for i, v in enumerate(row)))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 1 -- RAW
# Confirmed schema for raw.transactions:
#   id (BIGINT), date (TEXT "2010-01-01 00:00:00"), client_id, card_id,
#   amount (TEXT "$20.74" / "$-77.00"), use_chip (TEXT), merchant_id,
#   merchant_city (TEXT), merchant_state (TEXT), zip (TEXT), mcc (INTEGER),
#   errors (TEXT)
# ─────────────────────────────────────────────────────────────────────────────

def audit_raw(cur, head_n, no_head):
    hdr("LAYER 1 -- raw schema")

    sub("Row counts")
    counts = {
        "transactions": 13_305_915,
        "users":        2_020,
        "cards":        6_207,
        "mcc":          127,
    }
    for tbl, expected in counts.items():
        if not tbl_exists(cur, "raw", tbl):
            fail(f"raw.{tbl} -- TABLE NOT FOUND")
            continue
        cnt = scalar(cur, f"SELECT COUNT(*) FROM raw.{tbl}")
        result(f"raw.{tbl}", cnt, expected)

    if tbl_exists(cur, "raw", "transactions"):

        sub("raw.transactions -- PK integrity (id column)")
        null_ids = scalar(cur, "SELECT COUNT(*) FROM raw.transactions WHERE id IS NULL")
        result("NULL id (PK)", null_ids, 0, bad_if_nonzero=True)

        dup_ids = scalar(cur, """
            SELECT COUNT(*) FROM (
                SELECT id FROM raw.transactions
                GROUP BY id HAVING COUNT(*) > 1
            ) x
        """)
        result("Duplicate id values", dup_ids, 0, bad_if_nonzero=True)

        sub("raw.transactions -- date column (TEXT timestamp)")
        row = qrows(cur, """
            SELECT
                MIN(date::TIMESTAMP::DATE),
                MAX(date::TIMESTAMP::DATE),
                COUNT(DISTINCT DATE_TRUNC('month', date::TIMESTAMP::DATE)),
                COUNT(*) FILTER (WHERE date IS NULL OR TRIM(date) = '')
            FROM raw.transactions
        """)
        if row and row[0][0]:
            earliest, latest, months, nulls = row[0]
            ok(f"Earliest date:   {earliest}")
            ok(f"Latest date:     {latest}")
            ok(f"Distinct months: {months}  (expected 118)")
            result("NULL/blank date", nulls, 0, bad_if_nonzero=True)

        sub("raw.transactions -- amount column (TEXT '$20.74' / '$-77.00')")
        row = qrows(cur, """
            SELECT
                COUNT(*) FILTER (WHERE amount IS NULL OR TRIM(amount) = '')   AS nulls,
                COUNT(*) FILTER (WHERE amount NOT LIKE '$%')                   AS bad_prefix,
                COUNT(*) FILTER (WHERE REPLACE(amount,'$','')::NUMERIC < 0)   AS negatives,
                MIN(REPLACE(amount,'$','')::NUMERIC)                           AS min_amt,
                MAX(REPLACE(amount,'$','')::NUMERIC)                           AS max_amt
            FROM raw.transactions
        """)
        if row:
            nulls, bad_pfx, negs, mn, mx = row[0]
            result("NULL/blank amount", nulls, 0, bad_if_nonzero=True)
            result("Amount without '$' prefix", bad_pfx, 0, warn_if_nonzero=True)
            info(f"Negative amounts (refunds): {negs:,}  (expected ~660,049)")
            info(f"Amount range: {mn} -> {mx}")

        sub("raw.transactions -- use_chip distinct values")
        chip_vals = qrows(cur, """
            SELECT use_chip, COUNT(*) AS cnt
            FROM raw.transactions
            GROUP BY use_chip ORDER BY cnt DESC
        """)
        for val, cnt in chip_vals:
            info(f"use_chip = {repr(val)}: {cnt:,}")
        expected_chip = {"Swipe Transaction", "Online Transaction", "Chip Transaction"}
        actual_chip = {v for v, _ in chip_vals if v is not None}
        unexpected = actual_chip - expected_chip
        if unexpected:
            warn(f"Unexpected use_chip values: {unexpected}")
        else:
            ok("All use_chip values are known variants")

        sub("raw.transactions -- errors column")
        row = qrows(cur, """
            SELECT
                COUNT(*) FILTER (WHERE errors IS NULL)        AS null_errors,
                COUNT(*) FILTER (WHERE errors IS NOT NULL)    AS populated_errors,
                COUNT(*) FILTER (WHERE TRIM(errors) = '')     AS blank_errors
            FROM raw.transactions
        """)
        if row:
            nulls, populated, blanks = row[0]
            info(f"NULL errors (clean transactions): {nulls:,}")
            info(f"Populated errors (failures):      {populated:,}  (expected ~211,393)")
            result("Blank non-NULL errors", blanks, 0, warn_if_nonzero=True)

        sub("raw.transactions -- mcc join to raw.mcc")
        if tbl_exists(cur, "raw", "mcc"):
            # raw.transactions.mcc is INTEGER; raw.mcc.code is TEXT -- cast needed
            unmatched = scalar(cur, """
                SELECT COUNT(DISTINCT t.mcc)
                FROM raw.transactions t
                LEFT JOIN raw.mcc m ON t.mcc::TEXT = m.code
                WHERE t.mcc IS NOT NULL AND m.code IS NULL
            """)
            result("MCC codes in transactions not in raw.mcc", unmatched, 0, warn_if_nonzero=True)

        sub("raw.transactions -- NULL FK columns")
        for col in ["client_id", "card_id", "merchant_id"]:
            n = count_nulls(cur, "raw", "transactions", col)
            result(f"NULL {col}", n, 0, warn_if_nonzero=True)

    if tbl_exists(cur, "raw", "mcc"):
        sub("raw.mcc -- code format variants (explains 127 raw -> 109 stg)")
        variants = qrows(cur, r"""
            SELECT
                COUNT(*) FILTER (WHERE code ~ '^\d+$')                    AS plain_int,
                COUNT(*) FILTER (WHERE code LIKE '"%"')                    AS quoted,
                COUNT(*) FILTER (WHERE UPPER(code) LIKE 'MCC%')           AS mcc_prefix,
                COUNT(*) FILTER (WHERE code IS NULL OR TRIM(code) = '')    AS null_blank,
                COUNT(*) FILTER (
                    WHERE code NOT LIKE '"%"'
                      AND UPPER(code) NOT LIKE 'MCC%'
                      AND (code IS NULL OR code !~ '^\d+$')
                )                                                          AS unparseable
            FROM raw.mcc
        """)
        if variants:
            plain, quoted, prefix, nulls, unparseable = variants[0]
            info(f"Plain integer codes:  {plain}")
            info(f"Quoted codes:         {quoted}   e.g. '\"3000\"'")
            info(f"MCC-prefixed codes:   {prefix}  e.g. 'MCC3066'")
            info(f"NULL/blank codes:     {nulls}")
            if unparseable and unparseable > 0:
                warn(f"Unparseable codes:    {unparseable}  -- these are dropped by stg_mcc")

    sub("raw.rejected -- rejection log")
    if not tbl_exists(cur, "raw", "rejected"):
        warn("raw.rejected -- TABLE NOT FOUND")
    else:
        total_rejected = scalar(cur, "SELECT COUNT(*) FROM raw.rejected")
        if total_rejected == 0:
            ok("raw.rejected: 0 rows -- no rejections logged")
        else:
            warn(f"raw.rejected: {total_rejected:,} total rejection(s) logged")

            # Break down by source table and reason
            breakdown = qrows(cur, """
                SELECT source_table, rejection_reason, COUNT(*) AS cnt
                FROM raw.rejected
                GROUP BY source_table, rejection_reason
                ORDER BY cnt DESC
            """)
            info("Breakdown by source table and reason:")
            for src, reason, cnt in breakdown:
                print(f"         {src}  |  {reason}  |  {cnt:,} rows")

            # Show the 5 most recent rejections with a preview of the row content
            recent = qrows(cur, """
                SELECT source_table, rejection_reason, ingested_at,
                       LEFT(source_row, 120) AS row_preview
                FROM raw.rejected
                ORDER BY ingested_at DESC
                LIMIT 5
            """)
            info("5 most recent rejections:")
            for src, reason, ts, preview in recent:
                print(f"         [{ts}]  {src}  --  {reason}")
                print(f"           {preview}")

    if not no_head:
        sub("HEAD previews -- raw layer")
        for tbl in ["transactions", "users", "cards", "mcc", "rejected"]:
            if tbl_exists(cur, "raw", tbl):
                print_head(cur, "raw", tbl, head_n)


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 2 -- STAGING
# Columns confirmed from stg_transactions.sql, stg_cards.sql,
# stg_users.sql, stg_mcc.sql
# ─────────────────────────────────────────────────────────────────────────────

def audit_stg(cur, head_n, no_head):
    hdr("LAYER 2 -- staging (dw.stg_*)")

    sub("Row counts")
    stg_counts = {
        "stg_mcc":          125,
        "stg_users":        2_020,
        "stg_cards":        6_207,
        "stg_transactions": 13_305_915,
    }
    for tbl, expected in stg_counts.items():
        if not tbl_exists(cur, "dw", tbl):
            fail(f"dw.{tbl} -- TABLE NOT FOUND")
            continue
        cnt = scalar(cur, f"SELECT COUNT(*) FROM dw.{tbl}")
        result(f"dw.{tbl}", cnt, expected)

    # stg_mcc
    if tbl_exists(cur, "dw", "stg_mcc"):
        sub("stg_mcc -- code quality after cleaning")
        result("NULL mcc_code", count_nulls(cur, "dw", "stg_mcc", "mcc_code"), 0, bad_if_nonzero=True)
        dups = scalar(cur, """
            SELECT COUNT(*) FROM (
                SELECT mcc_code FROM dw.stg_mcc
                GROUP BY mcc_code HAVING COUNT(*) > 1
            ) x
        """)
        result("Duplicate mcc_code after DISTINCT OFF", dups, 16, bad_if_nonzero=True)
        trailing = scalar(cur, """
            SELECT COUNT(*) FROM dw.stg_mcc WHERE mcc_description != TRIM(mcc_description)
        """)
        result("mcc_description with trailing spaces", trailing, 0, warn_if_nonzero=True)
        raw_cnt = scalar(cur, "SELECT COUNT(*) FROM raw.mcc") if tbl_exists(cur, "raw", "mcc") else 127
        stg_cnt = scalar(cur, "SELECT COUNT(*) FROM dw.stg_mcc")
        info(f"Rows dropped in stg_mcc: {raw_cnt - stg_cnt} (raw {raw_cnt} -> stg {stg_cnt})")

    # stg_users
    if tbl_exists(cur, "dw", "stg_users"):
        sub("stg_users -- NULLs and canonical value distributions")
        result("NULL client_id", count_nulls(cur, "dw", "stg_users", "client_id"), 0, bad_if_nonzero=True)
        for col in ["yearly_income", "per_capita_income", "total_debt"]:
            n = count_nulls(cur, "dw", "stg_users", col)
            result(f"NULL {col} (parse fail)", n, 0, warn_if_nonzero=True)

        for col, label in [("employment_status", "employment"), ("education_level", "education")]:
            dist = qrows(cur, f"""
                SELECT {col}, COUNT(*) FROM dw.stg_users
                GROUP BY {col} ORDER BY COUNT(*) DESC
            """)
            info(f"{label} distribution:")
            for val, cnt in dist:
                marker = "  !!" if val == "Unknown" else "    "
                print(f"  {marker}  {val}: {cnt:,}")

    # stg_cards
    if tbl_exists(cur, "dw", "stg_cards"):
        sub("stg_cards -- NULLs and canonical values")
        result("NULL card_id", count_nulls(cur, "dw", "stg_cards", "card_id"), 0, bad_if_nonzero=True)
        result("NULL credit_limit (parse fails)", count_nulls(cur, "dw", "stg_cards", "credit_limit"), 130, warn_if_nonzero=True)
        result("NULL acct_open_date (parse fails)", count_nulls(cur, "dw", "stg_cards", "acct_open_date"), 61, warn_if_nonzero=True)
        result("NULL expires", count_nulls(cur, "dw", "stg_cards", "expires"), 0, warn_if_nonzero=True)

        for col, label in [("card_brand", "brand"), ("card_type", "type")]:
            dist = qrows(cur, f"""
                SELECT {col}, COUNT(*) FROM dw.stg_cards
                GROUP BY {col} ORDER BY COUNT(*) DESC
            """)
            info(f"card_{label} distribution:")
            for val, cnt in dist:
                marker = "  !!" if val == "Unknown" else "    "
                print(f"  {marker}  {val}: {cnt:,}")

        # Known issue: 'P' pattern catches Platinum/Premium/Personal
        prepaid = scalar(cur, "SELECT COUNT(*) FROM dw.stg_cards WHERE card_type = 'Prepaid'")
        if prepaid:
            info(f"Prepaid cards: {prepaid:,} -- verify none are 'Platinum'/'Personal' misclassified by the '%P%' pattern")

    # stg_transactions
    if tbl_exists(cur, "dw", "stg_transactions"):
        sub("stg_transactions -- NULLs on key columns")
        result("NULL transaction_id", count_nulls(cur, "dw", "stg_transactions", "transaction_id"), 0, bad_if_nonzero=True)
        result("NULL amount", count_nulls(cur, "dw", "stg_transactions", "amount"), 0, bad_if_nonzero=True)
        zeros = scalar(cur, "SELECT COUNT(*) FROM dw.stg_transactions WHERE amount = 0")
        result("Zero amount rows", zeros, 0, warn_if_nonzero=True)
        n_mcc = count_nulls(cur, "dw", "stg_transactions", "mcc_code")
        info(f"NULL mcc_code: {n_mcc:,}  (NULLs expected for online/no-MCC transactions)")

        sub("stg_transactions -- flag distributions")
        row = qrows(cur, """
            SELECT
                SUM(CASE WHEN is_refund THEN 1 ELSE 0 END),
                SUM(CASE WHEN is_online THEN 1 ELSE 0 END),
                SUM(CASE WHEN is_error  THEN 1 ELSE 0 END)
            FROM dw.stg_transactions
        """)
        if row:
            ref, onl, err = row[0]
            result("is_refund = TRUE", ref, 660_049)
            result("is_online = TRUE", onl, 1_557_912)
            result("is_error  = TRUE", err, 211_393)

        sub("stg_transactions -- merchant state/city consistency")
        row = qrows(cur, """
            SELECT
                SUM(CASE WHEN merchant_city  = 'ONLINE'  THEN 1 ELSE 0 END),
                SUM(CASE WHEN merchant_state = 'ONLINE'  THEN 1 ELSE 0 END),
                SUM(CASE WHEN merchant_state = 'UNKNOWN' THEN 1 ELSE 0 END)
            FROM dw.stg_transactions
        """)
        if row:
            oc, os, us = row[0]
            info(f"merchant_city  = 'ONLINE':  {oc:,}  (expected 1,563,700)")
            info(f"merchant_state = 'ONLINE':  {os:,}")
            info(f"merchant_state = 'UNKNOWN': {us:,}  (expected 1,563,700)")
            if oc == us:
                ok("online_city count == unknown_state count")
            else:
                warn(f"Mismatch: online_city={oc:,} vs unknown_state={us:,}")

        sub("stg_transactions -- date_sk coverage in dim_date")
        if tbl_exists(cur, "dw", "dim_date"):
            unmatched = scalar(cur, """
                SELECT COUNT(DISTINCT st.date_sk)
                FROM dw.stg_transactions st
                LEFT JOIN dw.dim_date dd ON st.date_sk = dd.date_sk
                WHERE dd.date_sk IS NULL
            """)
            result("Distinct date_sk values not in dim_date", unmatched, 0, bad_if_nonzero=True)

    if not no_head:
        sub("HEAD previews -- staging layer")
        for tbl in ["stg_mcc", "stg_users", "stg_cards", "stg_transactions"]:
            if tbl_exists(cur, "dw", tbl):
                print_head(cur, "dw", tbl, head_n)


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 3 -- WAREHOUSE
# Schema confirmed from 02_dw_schema.sql
# ─────────────────────────────────────────────────────────────────────────────

def audit_dw(cur, head_n, no_head):
    hdr("LAYER 3 -- warehouse (dw.*)")

    sub("Row counts")
    dw_counts = {
        "dim_date":          None,
        "dim_customers":     2_000,
        "dim_cards":         6_146,
        "dim_merchants":     74_831,
        "fact_transactions": 13_305_915,
    }
    for tbl, expected in dw_counts.items():
        if not tbl_exists(cur, "dw", tbl):
            fail(f"dw.{tbl} -- TABLE NOT FOUND")
            continue
        cnt = scalar(cur, f"SELECT COUNT(*) FROM dw.{tbl}")
        result(f"dw.{tbl}", cnt, expected)

    # dim_date
    if tbl_exists(cur, "dw", "dim_date"):
        sub("dim_date -- range and known issues")
        row = qrows(cur, "SELECT MIN(full_date), MAX(full_date), COUNT(*) FROM dw.dim_date")
        if row and row[0][0]:
            mn, mx, cnt = row[0]
            ok(f"Date range: {mn} -> {mx}  (expected 1900-01-01 -> 2999-12-31)")
            ok(f"Total rows: {cnt:,}")

        # KNOWN ISSUE: TO_CHAR(d,'Month') pads with trailing spaces
        trailing_month = scalar(cur, "SELECT COUNT(*) FROM dw.dim_date WHERE month_name != TRIM(month_name)")
        trailing_day   = scalar(cur, "SELECT COUNT(*) FROM dw.dim_date WHERE day_name   != TRIM(day_name)")
        if trailing_month and trailing_month > 0:
            warn(f"Trailing spaces in month_name: {trailing_month:,} rows -- fix: use TO_CHAR(d,'FMMonth')")
        else:
            ok("No trailing spaces in month_name")
        if trailing_day and trailing_day > 0:
            warn(f"Trailing spaces in day_name: {trailing_day:,} rows -- fix: use TO_CHAR(d,'FMDay')")
        else:
            ok("No trailing spaces in day_name")

        # ISO week edge: Jan 1-3 showing week 52/53
        iso_edge = scalar(cur, """
            SELECT COUNT(*) FROM dw.dim_date
            WHERE EXTRACT(MONTH FROM full_date) = 1
              AND EXTRACT(DAY FROM full_date) <= 3
              AND week >= 52
        """)
        if iso_edge and iso_edge > 0:
            info(f"ISO week edge cases (Jan 1-3 with week=52/53): {iso_edge} -- add iso_year column to resolve ambiguity")

    # dim_customers
    if tbl_exists(cur, "dw", "dim_customers"):
        sub("dim_customers -- SCD Type 2 integrity")
        total = scalar(cur, "SELECT COUNT(*) FROM dw.dim_customers")
        current = scalar(cur, "SELECT COUNT(*) FROM dw.dim_customers WHERE is_current = TRUE")
        unique_clients = scalar(cur, "SELECT COUNT(DISTINCT client_id) FROM dw.dim_customers")
        ok(f"Total rows (all SCD versions): {total:,}")
        ok(f"Current rows (is_current=TRUE): {current:,}")
        result("Unique client_id values", unique_clients, 2_000)

        multi_current = scalar(cur, """
            SELECT COUNT(*) FROM (
                SELECT client_id FROM dw.dim_customers
                WHERE is_current = TRUE GROUP BY client_id HAVING COUNT(*) > 1
            ) x
        """)
        result("Clients with >1 current row (SCD-2 violation)", multi_current, 0, bad_if_nonzero=True)

        bad_valid_to = scalar(cur, """
            SELECT COUNT(*) FROM dw.dim_customers WHERE is_current = TRUE AND valid_to IS NOT NULL
        """)
        result("Current rows with non-NULL valid_to", bad_valid_to, 0, bad_if_nonzero=True)

        hist_null_to = scalar(cur, """
            SELECT COUNT(*) FROM dw.dim_customers WHERE is_current = FALSE AND valid_to IS NULL
        """)
        result("Historical rows with NULL valid_to", hist_null_to, 0, warn_if_nonzero=True)

    # dim_cards
    if tbl_exists(cur, "dw", "dim_cards"):
        sub("dim_cards -- key integrity")
        dups = scalar(cur, """
            SELECT COUNT(*) FROM (
                SELECT card_id FROM dw.dim_cards GROUP BY card_id HAVING COUNT(*) > 1
            ) x
        """)
        result("Duplicate card_id", dups, 0, bad_if_nonzero=True)
        result("NULL credit_limit (2 drops of duplicates) now", count_nulls(cur, "dw", "dim_cards", "credit_limit"), 128, warn_if_nonzero=True)

    # dim_merchants
    if tbl_exists(cur, "dw", "dim_merchants"):
        sub("dim_merchants -- MCC join quality")
        result("NULL mcc_description (MCC join failed)", count_nulls(cur, "dw", "dim_merchants", "mcc_description"), 0, warn_if_nonzero=True)
 
        sub("dim_merchants -- zip coverage")
        if not col_exists(cur, "dw", "dim_merchants", "zip"):
            warn("zip column NOT present in dim_merchants -- zip-level analysis impossible")
            info("Fix: add zip TEXT to dim_merchants DDL and include t.zip in the load INSERT")
        else:
            total     = scalar(cur, "SELECT COUNT(*) FROM dw.dim_merchants")
            null_zip  = count_nulls(cur, "dw", "dim_merchants", "zip")
            has_zip   = total - null_zip
            online    = scalar(cur, "SELECT COUNT(*) FROM dw.dim_merchants WHERE merchant_state = 'ONLINE'")
            pct       = round(100 * has_zip / total, 1) if total else 0
 
            result("NULL zip (online/unknown merchants)", null_zip, online, warn_if_nonzero=False)
            ok(f"Merchants with zip: {has_zip:,} / {total:,}  ({pct}%)")
 
            if null_zip != online:
                warn(f"NULL zip count ({null_zip:,}) != ONLINE merchant count ({online:,}) -- some physical merchants may be missing a zip")
 
            # Top 10 zip codes by merchant count
            top_zips = qrows(cur, """
                SELECT zip, COUNT(*) AS cnt
                FROM dw.dim_merchants
                WHERE zip IS NOT NULL
                GROUP BY zip
                ORDER BY cnt DESC
                LIMIT 10
            """)
            if top_zips:
                info("Top 10 zip codes by merchant count:")
                for zip_code, cnt in top_zips:
                    print(f"         {zip_code}: {cnt:,}")
 
        top_states = qrows(cur, """
            SELECT merchant_state, COUNT(*) AS cnt
            FROM dw.dim_merchants GROUP BY merchant_state ORDER BY cnt DESC LIMIT 5
        """)
        info("Top 5 merchant_state values:")
        for state, cnt in top_states:
            print(f"         {state or 'NULL'}: {cnt:,}")

    # fact_transactions
    if tbl_exists(cur, "dw", "fact_transactions"):
        sub("fact_transactions -- referential integrity (FK orphan check)")
        fk_checks = [
            ("date_sk",     "dim_date",      "date_sk"),
            ("customer_sk", "dim_customers", "customer_sk"),
            ("card_sk",     "dim_cards",     "card_sk"),
            ("merchant_sk", "dim_merchants", "merchant_sk"),
        ]
        for fk_col, dim_tbl, dim_col in fk_checks:
            if not tbl_exists(cur, "dw", dim_tbl):
                warn(f"dw.{dim_tbl} not found -- skipping {fk_col} FK check")
                continue
            orphans = scalar(cur, f"""
                SELECT COUNT(*) FROM dw.fact_transactions ft
                LEFT JOIN dw.{dim_tbl} d ON ft.{fk_col} = d.{dim_col}
                WHERE d.{dim_col} IS NULL
            """)
            result(f"Orphan rows: {fk_col} -> {dim_tbl}", orphans, 0, bad_if_nonzero=True)

        sub("fact_transactions -- measure quality")
        neg = scalar(cur, "SELECT COUNT(*) FROM dw.fact_transactions WHERE amount < 0")
        result("Negative amounts (should be 0 -- ABS applied at staging)", neg, 0, bad_if_nonzero=True)
        zeros = scalar(cur, "SELECT COUNT(*) FROM dw.fact_transactions WHERE amount = 0")
        result("Zero amounts", zeros, 0, warn_if_nonzero=True)

        row = qrows(cur, """
            SELECT
                ROUND(SUM(amount) FILTER (WHERE is_refund = FALSE)::NUMERIC, 2),
                ROUND(SUM(amount) FILTER (WHERE is_refund = TRUE )::NUMERIC, 2),
                COUNT(*) FILTER (WHERE is_refund = FALSE),
                COUNT(*) FILTER (WHERE is_refund = TRUE),
                COUNT(*) FILTER (WHERE is_error  = TRUE)
            FROM dw.fact_transactions
        """)
        if row and row[0][0]:
            gross, refunds, sales, ref_cnt, err_cnt = row[0]
            ok(f"Gross revenue (non-refund):  ${gross:,}  (expect ~$639M)")
            info(f"Total refunds:               ${refunds:,}")
            info(f"Sale count:                  {sales:,}")
            info(f"Refund count:                {ref_cnt:,}  (expect 660,049)")
            info(f"Error count:                 {err_cnt:,}  (expect 211,393)")

        sub("fact_transactions -- suspicious_transactions partition note")
        dupe_groups = scalar(cur, """
            SELECT COUNT(*) FROM (
                SELECT customer_sk, date_sk, amount, merchant_sk
                FROM dw.fact_transactions WHERE is_refund = FALSE
                GROUP BY customer_sk, date_sk, amount, merchant_sk HAVING COUNT(*) > 1
            ) x
        """)
        info(f"Customer + day + amount groups with duplicates: {dupe_groups:,}")


    if not no_head:
        sub("HEAD previews -- warehouse layer")
        for tbl in ["dim_date", "dim_customers", "dim_cards", "dim_merchants", "fact_transactions"]:
            if tbl_exists(cur, "dw", tbl):
                print_head(cur, "dw", tbl, head_n)


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 4 -- MARTS
# Views confirmed from 03_mart_schema.sql
# ─────────────────────────────────────────────────────────────────────────────

def audit_mart(cur, head_n, no_head):
    hdr("LAYER 4 -- data marts (mart.*)")

    mart_views = {
        "finance_summary":          118,
        "finance_by_state":         188,
        "finance_by_category":      109,
        "customer_analytics":       1_219,
        "suspicious_transactions":  91_551,
        "merchant_summary":         74_831,
        "merchant_category_growth": 12_786,
    }

    sub("Row counts")
    for view, expected in mart_views.items():
        if not tbl_exists(cur, "mart", view):
            fail(f"mart.{view} -- VIEW NOT FOUND")
            continue
        cnt = scalar(cur, f"SELECT COUNT(*) FROM mart.{view}")
        result(f"mart.{view}", cnt, expected)

    sub("Revenue reconciliation")
    fs_rev = ms_rev = state_rev = None

    if tbl_exists(cur, "mart", "finance_summary"):
        fs_rev = scalar(cur, "SELECT ROUND(SUM(total_revenue)::NUMERIC, 2) FROM mart.finance_summary")
        ok(f"finance_summary   total_revenue: ${fs_rev:,}  (expect ~$639M)")

    if tbl_exists(cur, "mart", "merchant_summary"):
        ms_rev = scalar(cur, "SELECT ROUND(SUM(total_revenue)::NUMERIC, 2) FROM mart.merchant_summary")
        ok(f"merchant_summary  total_revenue: ${ms_rev:,}  (expect ~$639M)")

    if fs_rev and ms_rev:
        if fs_rev == ms_rev:
            ok("finance_summary == merchant_summary revenue")
        else:
            warn(f"Revenue mismatch: finance_summary={fs_rev:,} vs merchant_summary={ms_rev:,}")

    if tbl_exists(cur, "mart", "finance_by_state"):
        state_rev = scalar(cur, "SELECT ROUND(SUM(total_revenue)::NUMERIC, 2) FROM mart.finance_by_state")
        info(f"finance_by_state  total_revenue: ${state_rev:,}  (ONLINE/UNKNOWN excluded by design)")
        if fs_rev and state_rev:
            gap = round(float(fs_rev) - float(state_rev), 2)
            info(f"Revenue gap (ONLINE+UNKNOWN states): ${gap:,.2f}")
            info("This gap is intentional but undocumented -- consider adding a comment to the view.")

    sub("customer_analytics -- CLV sanity")
    if tbl_exists(cur, "mart", "customer_analytics"):
        row = qrows(cur, """
            SELECT COUNT(*), ROUND(AVG(lifetime_value)::NUMERIC,2),
                   MIN(lifetime_value), MAX(lifetime_value),
                   COUNT(*) FILTER (WHERE lifetime_value IS NULL)
            FROM mart.customer_analytics
        """)
        if row:
            customers, avg_ltv, mn, mx, null_ltv = row[0]
            result("Customer count", customers, 1_219)
            ok(f"Avg LTV: ${avg_ltv:,}  (expect ~$524K)")
            info(f"LTV range: ${mn:,} -> ${mx:,}")
            result("NULL lifetime_value", null_ltv, 0, bad_if_nonzero=True)

    sub("suspicious_transactions -- flag analysis")
    if tbl_exists(cur, "mart", "suspicious_transactions"):
        row = qrows(cur, """
            SELECT COUNT(*), MIN(duplicate_count), MAX(duplicate_count),
                   ROUND(AVG(duplicate_count)::NUMERIC, 2)
            FROM mart.suspicious_transactions
        """)
        if row:
            total, mn, mx, avg = row[0]
            result("Total flagged rows", total, 102_668)
            info(f"duplicate_count range: {mn} -> {mx},  avg: {avg}")

    sub("merchant_category_growth -- MoM growth sanity")
    if tbl_exists(cur, "mart", "merchant_category_growth"):
        row = qrows(cur, """
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE prev_month_revenue IS NULL),
                   MAX(ABS(mom_growth_pct))
            FROM mart.merchant_category_growth
        """)
        if row:
            total, first_nulls, max_growth = row[0]
            result("Total rows", total, 12_786)
            info(f"First-month rows (NULL prev_revenue -- expected): {first_nulls:,}")
            if max_growth and max_growth > 10_000:
                warn(f"Max |MoM growth|: {max_growth:,}%  -- extreme outlier, check for near-zero prev_month_revenue denominator")
            else:
                ok(f"Max |MoM growth|: {max_growth:,}%")

    sub("NULL scan -- first 4 columns of each mart view")
    for view in mart_views:
        if not tbl_exists(cur, "mart", view):
            continue
        cols = get_columns(cur, "mart", view)[:4]
        null_hits = []
        for col in cols:
            n = count_nulls(cur, "mart", view, col)
            if n and n > 0:
                null_hits.append(f"{col}={n:,}")
        if null_hits:
            warn(f"mart.{view} NULLs: {', '.join(null_hits)}")
        else:
            ok(f"mart.{view}: no NULLs in first 4 columns")

    if not no_head:
        sub("HEAD previews -- mart layer")
        for view in mart_views:
            if tbl_exists(cur, "mart", view):
                print_head(cur, "mart", view, head_n)


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA DISCOVERY
# ─────────────────────────────────────────────────────────────────────────────

def schema_discovery(cur):
    hdr("SCHEMA DISCOVERY -- all tables & views", char="-")
    rows = qrows(cur, """
        SELECT table_schema, table_type, table_name,
               pg_size_pretty(
                   pg_total_relation_size(
                       quote_ident(table_schema)||'.'||quote_ident(table_name)
                   )
               ) AS size
        FROM information_schema.tables
        WHERE table_schema IN ('raw','dw','mart','public')
          AND table_type IN ('BASE TABLE','VIEW')
        ORDER BY table_schema, table_type DESC, table_name
    """)
    current_schema = None
    for schema, ttype, tname, size in rows:
        if schema != current_schema:
            print(f"\n  {CYN}{schema}{RS}")
            current_schema = schema
        tag = "VIEW " if ttype == "VIEW" else "TABLE"
        print(f"    {DIM}{tag}{RS}  {tname:<50} {DIM}{size or ''}{RS}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    print(f"\n{BOLD}{'='*74}{RS}")
    print(f"{BOLD}  CLEARSPEND DATABASE AUDIT{RS}")
    print(f"{BOLD}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RS}")
    print(f"{BOLD}{'='*74}{RS}")

    conn_kwargs = {}
    if args.dsn:
        conn_kwargs["dsn"] = args.dsn
    else:
        conn_kwargs.update(dict(host=args.host, port=args.port, dbname=args.db))
        if args.user:     conn_kwargs["user"]     = args.user
        if args.password: conn_kwargs["password"] = args.password

    target = args.dsn or f"{args.host}:{args.port}/{args.db}"
    print(f"\n  Connecting to: {target}")

    try:
        conn = psycopg2.connect(**conn_kwargs)
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor()
        print(f"  {GRN}Connected{RS}")
    except Exception as e:
        sys.exit(
            f"\n  {RED}Connection failed:{RS} {e}\n"
            f"  Try: python audit.py --db <name> --user <user>\n"
            f"       python audit.py --dsn 'postgresql://user:pw@localhost/db'\n"
        )

    schema_discovery(cur)

    layer = args.layer
    h, nh = args.head, args.no_head

    if layer in ("all", "raw"):  audit_raw(cur, h, nh)
    if layer in ("all", "stg"):  audit_stg(cur, h, nh)
    if layer in ("all", "dw"):   audit_dw(cur, h, nh)
    if layer in ("all", "mart"): audit_mart(cur, h, nh)

    print(f"\n{BOLD}{'='*74}{RS}")
    print(f"{BOLD}  AUDIT COMPLETE{RS}")
    print(f"{BOLD}{'='*74}{RS}\n")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()