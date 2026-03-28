"""
pipeline.py
===========
Single entry point for the complete ClearSpend data pipeline.

PIPELINE LAYERS:
    Layer 1 — Ingest:     CSV → raw schema           (ingest.py)
    Layer 2 — Transform:  raw → dw staging tables    (transform.py)
    Layer 3 — Warehouse:  staging → star schema      (warehouse.py)
    Layer 4 — Marts:      warehouse → mart views     (marts.py)

Each layer is designed to be runnable independently (for debugging or
partial re-runs), but this file runs all four in sequence for a full
end-to-end pipeline execution.

USAGE:
    python src/pipeline.py              # full run
    python src/ingest.py                # layer 1 only
    python src/transform.py             # layer 2 only
    python src/warehouse.py             # layer 3 only
    python src/marts.py                 # layer 4 only

FAILURE BEHAVIOUR:
    If any layer raises an unhandled exception, the pipeline stops immediately.
    Partial runs leave the database in whatever state the failed layer left it.
    Re-running the full pipeline from scratch is always safe — all DDL uses
    DROP ... CASCADE before recreating schemas.

AUTHOR:     Jonah Knief (i6263747) | Arthem Vysotskyi (i6327809) | Lyan Eleraky (I6320604) | Loredana Lazari (I6346545)
COURSE:     Data Engineering and Data Compliance
UNIVERSITY: Maastricht University
"""

import logging
import os
import sys
import time

# ---------------------------------------------------------------------------
# ENCODING — force UTF-8 on all platforms.
# Windows defaults to the system locale encoding (e.g. cp1252 on German
# systems), which crashes on the box-drawing characters used in log output.
# reconfigure() is available on Python 3.7+ when stdout is a real terminal;
# the hasattr guard keeps it safe when stdout is redirected to a file.
# ---------------------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from ingest    import run_ingestion
from transform import run_transform
from warehouse import run_warehouse
from marts     import run_marts

# ---------------------------------------------------------------------------
# TEST SUITE PATHS
# ---------------------------------------------------------------------------
_TESTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'tests')

def run_tests(test_file: str) -> None:
    """
    Run a pytest test file and raise if any tests fail.
    Called after each layer to catch data quality regressions immediately
    rather than letting bad data silently propagate downstream.
    """
    import pytest
    test_path = os.path.join(_TESTS_DIR, test_file)
    log.info(f"  Running tests: {test_file}")
    exit_code = pytest.main([test_path, "-q", "--tb=short"])
    if exit_code != 0:
        raise RuntimeError(
            f"Tests failed in {test_file} (pytest exit code {exit_code}). "
            "Pipeline halted — fix the data quality issue before continuing."
        )

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)


def run_pipeline() -> None:
    """
    Execute all four pipeline layers in order.
    Logs total elapsed time for each layer and the full pipeline.
    """
    pipeline_start = time.time()

    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info("║         CLEARSPEND DATA PIPELINE — FULL RUN              ║")
    log.info("╚══════════════════════════════════════════════════════════╝")

    layers = [
        ("Layer 1: Ingestion",      run_ingestion,  "test_ingestion.py"),
        ("Layer 2: Transformation", run_transform,  "test_transforms.py"),
        ("Layer 3: Warehouse Build", run_warehouse, "test_warehouse.py"),
        ("Layer 4: Data Marts",     run_marts,      "test_marts.py"),
    ]

    for layer_name, layer_fn, test_file in layers:
        layer_start = time.time()
        log.info(f"▶  Starting {layer_name}")

        try:
            layer_fn()
        except Exception as e:
            # Log the failure clearly and re-raise to halt the pipeline.
            # Continuing after a layer failure would produce silently corrupt
            # downstream results, which is worse than a visible crash.
            log.error(f"✗  {layer_name} FAILED: {e}")
            raise

        if test_file:
            try:
                run_tests(test_file)
            except RuntimeError as e:
                log.error(f"✗  {layer_name} — test suite FAILED: {e}")
                raise

        elapsed = time.time() - layer_start
        log.info(f"✓  {layer_name} completed in {elapsed:.1f}s \n")

    total_elapsed = time.time() - pipeline_start
    log.info("")
    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info(f"║  PIPELINE COMPLETE — total time: {total_elapsed:.1f}s".ljust(59) + "║")
    log.info("╚══════════════════════════════════════════════════════════╝")


if __name__ == "__main__":
    run_pipeline()