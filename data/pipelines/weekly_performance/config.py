"""Names, constants and database connections of the Weekly Warehouse & Client Performance pipeline.

Credentials are never in code: the engines are built from environment variables (in a Prefect deployment they come
from the blocks `supabase-telemetry-read` and `supabase-reporting-write`, exported as these variables):

* TELEMETRY_READ_DATABASE_URL  role with SELECT on `telemetry_events` only (falls back to DATABASE_URL)
* REPORTING_DATABASE_URL       role that writes only to the `reporting` schema (falls back to DATABASE_URL)
"""

import os
import threading
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine

PIPELINE_NAME = "weekly_warehouse_client_performance"

# The mandatory telemetry events this pipeline reads (company CONTEXT, section 3). No other event type.
EVENT_TYPES = [
    "inbound_order_created",
    "outbound_order_created",
    "stock_threshold_triggered",
    "inventory_discrepancy_detected",
]
WAREHOUSES = ("los_angeles", "zaragoza")

# A scheduled run recomputes the last two complete weeks: the newest one, and the one before it to absorb late events.
DEFAULT_WEEKS_BACK = 2
MAX_WEEKS_BACK = 12

# A run still `running` after this long is considered dead and is marked failed by the next run (design 3.2).
STALE_RUN_MINUTES = 30

# Outbound Throughput counts dispatches only. `outbound_order_created` also fires for `loss` exits (a write-off),
# which are not orders "picked and dispatched". Flip to True to follow the CONTEXT wording literally (design 2.5).
COUNT_LOSS_EXITS = False

# More than this share of inbound/outbound events whose `value` column differs from `tags.quantity` fails the run.
VALUE_MISMATCH_TOLERANCE = 0.01

# Where the optional snapshot of the loaded KPIs is written.
SNAPSHOT_DIR_ENV = "PIPELINE_SNAPSHOT_DIR"

_override: Engine | None = None
_override_lock = threading.Lock()


@lru_cache(maxsize=8)
def _engine_for(url: str) -> Engine:
    options = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **options)


def _url(*names: str) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    raise RuntimeError(f"Set {' or '.join(names)} to the Supabase PostgreSQL connection string.")


def telemetry_engine() -> Engine:
    """Engine for the READ-ONLY connection to `telemetry_events`."""
    return _override or _engine_for(_url("TELEMETRY_READ_DATABASE_URL", "DATABASE_URL"))


def reporting_engine() -> Engine:
    """Engine for the connection that writes the `reporting` schema."""
    return _override or _engine_for(_url("REPORTING_DATABASE_URL", "DATABASE_URL"))


@contextmanager
def use_engine(engine: Engine):
    """Runs the pipeline against one given engine for both connections (the API uses its own engine; tests use theirs)."""
    global _override
    with _override_lock:
        previous, _override = _override, engine
    try:
        yield
    finally:
        with _override_lock:
            _override = previous
