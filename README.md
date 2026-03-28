# ClearSpend Data Pipeline

**Authors:** Jonah Knief (i6263747) | Arthem Vysotskyi (i6327809) | Lyan Eleraky

**Course:** Data Engineering and Data Compliance

**University:** Maastricht University

---

## Overview

ClearSpend is a four-layer end-to-end data pipeline built in Python and PostgreSQL. It ingests raw financial transaction data, cleans and transforms it, loads it into a star schema data warehouse, and exposes business-facing analytical views via data marts.

The pipeline processes:
- **13.3 million** credit card transactions
- **2,000** unique customers
- **6,207** cards
- **74,831** merchants across the United States

---

## Architecture

```
Raw CSVs
   │
   ▼
Layer 1 — Ingestion    (ingest.py)
   │  CSV → raw schema (PostgreSQL)
   │
   ▼
Layer 2 — Transformation    (transform.py)
   │  raw → dw staging tables (SQL transforms)
   │
   ▼
Layer 3 — Warehouse Build    (warehouse.py)
   │  staging → star schema (dim + fact tables)
   │
   ▼
Layer 4 — Data Marts    (marts.py)
      warehouse → PostgreSQL views for each business team
```

Each layer runs a dedicated test suite (pytest) after completing. The pipeline halts immediately if any test fails.

---

## Star Schema

The warehouse follows a classic star schema design (Adamson, *Star Schema: The Complete Reference*):

```
                    dim_date
                       │
dim_customers ──── fact_transactions ──── dim_merchants
                       │
                    dim_cards
```

### Dimension Tables

| Table | Description |
|---|---|
| `dw.dim_date` | Pre-populated calendar dimension (1900–2999), YYYYMMDD surrogate key |
| `dw.dim_customers` | Customer demographics — **SCD Type 2** on `yearly_income` and `employment_status` |
| `dw.dim_cards` | Card metadata (brand, type, credit limit, issuer) |
| `dw.dim_merchants` | Merchant details with MCC category codes |

### Fact Table

| Table | Grain | Rows |
|---|---|---|
| `dw.fact_transactions` | One row per transaction | ~13.4 million |

---

## Data Marts

Three PostgreSQL view-based marts serve different business teams:

### Finance (`mart.finance_summary`, `mart.finance_by_location`, `mart.finance_by_state`, `mart.finance_by_category`, `mart.finance_by_zip`)
- Monthly revenue, refund rate, error rate, average transaction value
- Revenue split by location type (US / International / Online) with share percentages
- Revenue breakdown by US state or country
- Revenue and peak month by merchant category (MCC)
- Revenue by ZIP code (physical US transactions only)

### Customer Analytics (`mart.customer_analytics`, `mart.suspicious_transactions`, `mart.card_testing_alerts`)
- Customer lifetime value (LTV), online vs in-store spend split
- Active card count per customer
- **Duplicate-charge detection** (`mart.suspicious_transactions`): flags same customer, same amount, same merchant, same calendar day. Note: source data has date-level precision only — sub-minute windowing is not possible without a timestamp field.
- **Card testing detection** (`mart.card_testing_alerts`): flags customers with 3+ transactions under $10 at 2+ distinct merchants on the same day — the typical pattern of probing stolen card numbers before escalating to high-value fraud. This catches varying-amount multi-merchant patterns that the duplicate-charge view misses.

### Customers without Transactions (`mart.customers_without_transactions`)
- Lists registered customers who have never made a transaction, enabling re-engagement campaigns and identifying data quality gaps (e.g. test accounts or onboarding dropouts).

### Error Analysis (`mart.error_analysis`)
- Breaks down the `error_type` column from `fact_transactions` (populated from the raw `errors` field) by type, showing error count, affected customers and merchants, average amount, and share percentage. Enables targeted fraud and operational investigations by error category (e.g. "Insufficient Balance" vs "Bad PIN").

### Merchant Partnerships (`mart.merchant_summary`, `mart.merchant_category_growth`)
- Transaction volume, revenue, error rate, and refund rate per merchant
- Month-over-month revenue growth by industry category

---

## Project Structure

```
clearspend/
├── src/
│   ├── pipeline.py        # Single entry point — runs all four layers in sequence
│   ├── ingest.py          # Layer 1: CSV → raw schema
│   ├── transform.py       # Layer 2: raw → staging tables (runs SQL transforms)
│   ├── warehouse.py       # Layer 3: staging → star schema
│   └── marts.py           # Layer 4: mart views + smoke tests
│
├── sql/
│   ├── ddl/
│   │   ├── 01_raw_schema.sql     # Raw ingestion tables
│   │   ├── 02_dw_schema.sql      # Star schema (dimensions + fact)
│   │   └── 03_mart_schema.sql    # Mart views
│   └── transforms/
│       ├── stg_mcc.sql           # MCC reference data
│       ├── stg_users.sql         # Customer staging + cleaning
│       ├── stg_cards.sql         # Card staging + cleaning
│       └── stg_transactions.sql  # Transaction staging + cleaning
│
├── tests/
│   ├── test_ingestion.py   # Layer 1 tests (row counts, PK uniqueness, nulls)
│   ├── test_transforms.py  # Layer 2 tests (data quality after transforms)
│   ├── test_warehouse.py   # Layer 3 tests (referential integrity, SCD2 rules)
│   └── test_marts.py       # Layer 4 tests (mart smoke tests)
│
├── data/
│   ├── users_data.csv               # Raw CSV files (see note below)
│   ├── cards_data.csv
│   ├── mcc_data.csv
│   └── transactions_data.csv        # NOT in repo — too large for GitHub (see Data Files)
│
├── .env                   # Database credentials (not committed)
├── requirements.txt
└── README.md
```

---

## Data Files

> **Note:** `transactions_data.csv` (~13.3 million rows) is **not included in this repository** due to GitHub's file size limits. The remaining raw files (`users_data.csv`, `cards_data.csv`, `mcc_data.csv`) are included.

To run the pipeline you will need to obtain `transactions_data.csv` separately and place it in `data/`.

---

## Setup

### Prerequisites

- **Python 3.11 or 3.12** (recommended — see note below)
- PostgreSQL 14+ running locally
- A database named `clearspend` (or update `.env` accordingly)

> **Python version note:** Use Python **3.11 or 3.12**. Python 3.13+ has known compatibility issues with pandas/numpy at the time of writing. Python 3.10 and below are not supported (the code uses 3.10+ type hint syntax).

### Installation

```bash
# Clone the repo
git clone <repo-url>
cd clearspend

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate           # macOS/Linux
.venv\Scripts\activate              # Windows CMD
.venv\Scripts\Activate.ps1          # Windows PowerShell

# Install dependencies
pip install -r requirements.txt
```

### Configuration

Copy the example below into a `.env` file in the project root and fill in your credentials:

```env
DB_NAME=clearspend
DB_USER=your_postgres_user
DB_PASSWORD=your_password
DB_HOST=localhost
DB_PORT=5432
```

### Running the Pipeline

```bash
# Full pipeline (all four layers + tests)
python src/pipeline.py

# Individual layers (for debugging or partial re-runs)
python src/ingest.py       # Layer 1 only
python src/transform.py    # Layer 2 only
python src/warehouse.py    # Layer 3 only
python src/marts.py        # Layer 4 only
```

The pipeline is **idempotent** — all DDL uses `DROP ... CASCADE` before recreating schemas, so it is always safe to re-run from scratch.

---

## Tests

Tests run automatically after each layer as part of `pipeline.py`. To run a specific suite manually:

```bash
cd clearspend
pytest tests/test_ingestion.py   -v
pytest tests/test_transforms.py  -v
pytest tests/test_warehouse.py   -v
pytest tests/test_marts.py       -v
```

| Suite | Checks |
|---|---|
| `test_ingestion.py` | Row counts match source CSVs, no null PKs, no duplicates |
| `test_transforms.py` | Canonical value mapping, non-negative credit limits, no null transaction IDs |
| `test_warehouse.py` | SCD2 integrity, referential integrity, dim row counts, fact grain |
| `test_marts.py` | Mart views return data, amounts non-negative, key business metrics present |

---

## Cross-Platform Compatibility

The pipeline is tested on **macOS and Windows 10/11** and is designed to run identically on both.

| Issue | Solution |
|---|---|
| Windows console defaults to `cp1252` (e.g. German locale), crashing on the `╔ ║ ╚` characters in log output | `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` is called at the top of every module before any logging |
| SQL files contain UTF-8 characters (`—` em-dash in comments) | All `open()` calls use `encoding="utf-8"` explicitly |
| CSV files saved by Excel on Windows may have a UTF-8 BOM header | `pd.read_csv(..., encoding="utf-8-sig")` handles both BOM and non-BOM UTF-8 transparently |
| `.env` file not found when running from a non-root directory | All modules use `load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))` with an explicit absolute-relative path rather than relying on the working directory |

---

## Key Design Decisions

- **SCD Type 2** on `dim_customers` tracks changes to `yearly_income` and `employment_status` over time, enabling accurate point-in-time LTV calculations. Change detection uses a pandas merge; the resulting batch of changed rows is expired in a **single `UPDATE ... WHERE customer_sk = ANY(array)`** call rather than per-row loops.
- **COPY protocol** (via `psycopg2.copy_expert`) is used for bulk dimension loads instead of row-by-row `INSERT`, giving 10–50x faster throughput.
- **Server-side SQL INSERT** for `fact_transactions` — joining 13M rows entirely inside PostgreSQL avoids pulling data into Python and back.
- **`DISTINCT ON (merchant_id)`** in the merchant staging query ensures exactly one row per merchant, preventing unique constraint violations from merchants appearing in multiple cities.
- **`TEXT` columns** for uncontrolled string fields (`merchant_city`, `merchant_state`, `mcc_description`) — avoids `StringDataRightTruncation` errors from unexpectedly long source values.
- **Deduplication before dimension inserts** — source data contains duplicate `client_id` and `card_id` rows; these are removed before loading to enforce the one-row-per-key invariant and prevent fact table fan-out.
- **UTF-8 enforced at process startup** — `sys.stdout.reconfigure` is called before any logging to ensure correct output on Windows systems with non-UTF-8 locale encodings (e.g. `cp1252` on German Windows).
- **Partial indexes on `fact_transactions`** — in addition to the per-FK B-tree indexes, partial indexes on `is_refund = FALSE` and `is_error = TRUE` are created. Since most mart views filter to non-refund rows (>95% of the table), the partial index reduces the scan footprint significantly for analytical queries.
- **`dim_merchants` is transaction-derived** — merchants are not loaded from a source file; they are constructed from distinct `merchant_id` values observed in `fact_transactions`. A merchant with zero transactions will not appear in `dim_merchants` or any mart view. This is a known data model constraint; the mart views correctly reflect only merchants with observed activity.

---

## Known Limitations

**Full refresh on every run.** Every pipeline run drops and recreates all schemas (`DROP SCHEMA ... CASCADE`), then reloads all data from scratch. For this project's dataset (~13M rows) a full run takes approximately 8 minutes. In a production setting, an incremental/delta load strategy (watermarks or Change Data Capture on the fact table) would reduce that to seconds for daily batches. The full-refresh approach is a deliberate simplification appropriate for the project scope.

**Date-only precision in source data.** The `transactions_data.csv` source file stores the `date` column at day-level precision only (e.g. `2010-01-01 00:00:00` — the time component is always midnight). As a result, `mart.suspicious_transactions` and `mart.card_testing_alerts` can only flag patterns on the same *calendar day*; they cannot enforce a strict time-window (e.g. same-minute charges) that would be standard in production fraud detection. This is a source data constraint, not a pipeline design flaw.

**`dim_merchants` contains only merchants with at least one transaction.** Because `dim_merchants` is derived from `stg_transactions` rather than loaded from a dedicated merchant source file, any merchant that exists in an external system but has never appeared in a transaction will not be present in the warehouse. All mart views therefore reflect only merchants with observed transaction history.

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `pandas` | 3.0.1 | CSV ingestion and DataFrame manipulation |
| `numpy` | 1.26.4 | Numerical operations (pandas dependency) |
| `sqlalchemy` | 2.0.48 | Database connectivity and ORM |
| `psycopg2-binary` | 2.9.11 | PostgreSQL driver (COPY protocol) |
| `python-dotenv` | 1.2.2 | `.env` credential loading |
| `pytest` | 9.0.2 | Test framework |
