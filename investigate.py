"""
investigate.py  —  Zero-amount transaction investigation for ClearSpend
=======================================================================
Queries fact_transactions for all rows where amount = 0 and prints:
  1. A flag breakdown (is_refund / is_online / is_error) with counts
  2. 20 exact rows with full dimensional context
  3. If any clean rows exist (no error, no refund), a separate table for those

Usage
-----
  python investigate.py
  python investigate.py --db mydb --user jonah
  python investigate.py --dsn "postgresql://user:pw@host/db"

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
    p = argparse.ArgumentParser(description="Investigate zero-amount transactions")
    p.add_argument("--dsn",      default=None)
    p.add_argument("--host",     default="localhost")
    p.add_argument("--port",     default=5432, type=int)
    p.add_argument("--db",       default="clearspend")
    p.add_argument("--user",     default=None)
    p.add_argument("--password", default=None)
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


# ─────────────────────────────────────────────────────────────────────────────
# PRINT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def hdr(title):
    print(f"\n{BOLD}{'=' * 74}{RS}")
    print(f"{BOLD}  {title}{RS}")
    print(f"{BOLD}{'=' * 74}{RS}")

def sub(title):
    print(f"\n{CYN}{BOLD}>>  {title}{RS}")

def ok(msg):   print(f"  {GRN}OK {RS}  {msg}")
def warn(msg): print(f"  {YEL}!! {RS}  {msg}")
def info(msg): print(f"       {msg}")

def print_rows(cur, max_col_width=22):
    """Print the current cursor result as an aligned grid using cursor.description for headers."""
    headers = [d[0] for d in cur.description]
    rows    = cur.fetchall()

    if not rows:
        info("(no rows)")
        return

    # Compute column widths from header and data, capped at max_col_width
    widths = [
        min(max_col_width, max(len(str(h)), max(len(str(v)) for v in col)))
        for h, col in zip(headers, zip(*rows))
    ]

    hdr_line = "  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep_line = "  " + "  ".join("-" * w for w in widths)

    print(f"\n{DIM}{hdr_line}{RS}")
    print(f"{DIM}{sep_line}{RS}")
    for row in rows:
        print("  " + "  ".join(str(v)[:widths[i]].ljust(widths[i]) for i, v in enumerate(row)))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def scalar(cur, sql):
    cur.execute(sql)
    row = cur.fetchone()
    return row[0] if row else None


# ─────────────────────────────────────────────────────────────────────────────
# INVESTIGATION
# ─────────────────────────────────────────────────────────────────────────────

def investigate_zero_amounts(cur):
    hdr("Zero-amount transactions in dw.fact_transactions")

    total = scalar(cur, "SELECT COUNT(*) FROM dw.fact_transactions WHERE amount = 0")

    if total == 0:
        ok("No zero-amount rows found — warning already resolved")
        return

    warn(f"Total zero-amount rows: {total:,}")

    # ── 1. Flag breakdown ─────────────────────────────────────────────────────
    sub("Flag breakdown — what kind of rows are these?")

    cur.execute("""
        SELECT
            is_refund,
            is_online,
            is_error,
            COALESCE(error_type, '(none)')   AS error_type,
            COUNT(*)                          AS row_count,
            COUNT(DISTINCT customer_sk)       AS distinct_customers,
            COUNT(DISTINCT merchant_sk)       AS distinct_merchants,
            MIN(dd.full_date)                 AS earliest,
            MAX(dd.full_date)                 AS latest
        FROM dw.fact_transactions ft
        JOIN dw.dim_date dd ON ft.date_sk = dd.date_sk
        WHERE ft.amount = 0
        GROUP BY is_refund, is_online, is_error, error_type
        ORDER BY row_count DESC
    """)
    breakdown = cur.fetchall()
    headers   = [d[0] for d in cur.description]

    # Manually print since we already consumed the cursor
    widths  = [min(22, max(len(str(h)), max(len(str(r[i])) for r in breakdown)))
               for i, h in enumerate(headers)]
    hdr_ln  = "  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep_ln  = "  " + "  ".join("-" * w for w in widths)
    print(f"\n{DIM}{hdr_ln}{RS}")
    print(f"{DIM}{sep_ln}{RS}")
    for row in breakdown:
        print("  " + "  ".join(str(v)[:widths[i]].ljust(widths[i]) for i, v in enumerate(row)))
    print()

    error_count  = sum(r[4] for r in breakdown if r[2] is True)
    refund_count = sum(r[4] for r in breakdown if r[0] is True)
    clean_count  = sum(r[4] for r in breakdown if r[2] is False and r[0] is False)

    info(f"is_error  = TRUE : {error_count:,}  — declined/failed auth, $0 is expected")
    info(f"is_refund = TRUE : {refund_count:,}  — full refund zeroing out a sale")
    info(f"no flags (clean) : {clean_count:,}  {'<-- needs further investigation' if clean_count > 0 else '— all accounted for'}")

    # ── 2. 20 exact rows ──────────────────────────────────────────────────────
    sub("20 exact zero-amount rows with full dimensional context")

    cur.execute("""
        SELECT
            ft.transaction_id,
            ft.transaction_sk,
            dd.full_date                         AS date,
            ft.amount,
            ft.is_refund,
            ft.is_online,
            ft.is_error,
            COALESCE(ft.error_type, '(none)')    AS error_type,
            dc.client_id,
            dm.merchant_id,
            dm.merchant_city,
            dm.merchant_state,
            COALESCE(dm.zip, 'NULL')             AS zip,
            COALESCE(dm.mcc_description, 'NULL') AS mcc_description
        FROM dw.fact_transactions ft
        JOIN dw.dim_date      dd ON ft.date_sk     = dd.date_sk
        JOIN dw.dim_customers dc ON ft.customer_sk = dc.customer_sk
        JOIN dw.dim_merchants dm ON ft.merchant_sk = dm.merchant_sk
        WHERE ft.amount = 0
        LIMIT 20
    """)
    print_rows(cur, max_col_width=24)

    # ── 3. Clean rows only — the most concerning subset ───────────────────────
    if clean_count > 0:
        warn(f"{clean_count:,} zero-amount rows carry no error and no refund flag")
        sub(f"20 clean zero-amount rows (is_error=FALSE, is_refund=FALSE)")

        cur.execute("""
            SELECT
                ft.transaction_id,
                dd.full_date                         AS date,
                ft.is_online,
                dc.client_id,
                dm.merchant_id,
                dm.merchant_city,
                dm.merchant_state,
                COALESCE(dm.zip, 'NULL')             AS zip,
                COALESCE(dm.mcc_description, 'NULL') AS mcc_description
            FROM dw.fact_transactions ft
            JOIN dw.dim_date      dd ON ft.date_sk     = dd.date_sk
            JOIN dw.dim_customers dc ON ft.customer_sk = dc.customer_sk
            JOIN dw.dim_merchants dm ON ft.merchant_sk = dm.merchant_sk
            WHERE ft.amount    = 0
              AND ft.is_error  = FALSE
              AND ft.is_refund = FALSE
            LIMIT 20
        """)
        print_rows(cur, max_col_width=24)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    print(f"\n{BOLD}{'=' * 74}{RS}")
    print(f"{BOLD}  CLEARSPEND — ZERO AMOUNT INVESTIGATION{RS}")
    print(f"{BOLD}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RS}")
    print(f"{BOLD}{'=' * 74}{RS}")

    conn_kwargs = {}
    if args.dsn:
        conn_kwargs["dsn"] = args.dsn
    else:
        conn_kwargs.update(dict(host=args.host, port=args.port, dbname=args.db))
        if args.user:     conn_kwargs["user"]     = args.user
        if args.password: conn_kwargs["password"] = args.password

    print(f"\n  Connecting to: {args.dsn or f'{args.host}:{args.port}/{args.db}'}")

    try:
        conn = psycopg2.connect(**conn_kwargs)
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor()
        print(f"  {GRN}Connected{RS}")
    except Exception as e:
        sys.exit(f"\n  {RED}Connection failed:{RS} {e}\n")

    investigate_zero_amounts(cur)

    print(f"\n{BOLD}{'=' * 74}{RS}")
    print(f"{BOLD}  DONE{RS}")
    print(f"{BOLD}{'=' * 74}{RS}\n")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()