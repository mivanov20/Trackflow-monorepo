"""The `reporting` schema: SQLAlchemy definitions of the destination table and the run log, and how they get created.

On PostgreSQL (Supabase) the tables are created from `reporting_schema.sql`, which holds the company CONTEXT's DDL
exactly. SQLite (used only by tests and local experiments) has no schemas, so the `reporting` schema is attached as a
second database file and the tables are created from the definitions below.
"""

import threading
import uuid
from pathlib import Path

from sqlalchemy import (
    CheckConstraint, Column, Date, DateTime, Engine, Index, Integer, MetaData, Numeric, Table, Text, UniqueConstraint, Uuid, event, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import JSON

SQL_FILE = Path(__file__).with_name("reporting_schema.sql")

metadata = MetaData(schema="reporting")

weekly_performance = Table(
    "weekly_warehouse_client_performance",
    metadata,
    Column("id", Uuid, primary_key=True, default=uuid.uuid4),
    Column("warehouse", Text, nullable=False),
    Column("client_id", Text, nullable=False),
    Column("week_start", Date, nullable=False),
    Column("inbound_units_count", Integer, nullable=False, default=0),
    Column("outbound_orders_count", Integer, nullable=False, default=0),
    Column("stockout_events_count", Integer, nullable=False, default=0),
    Column("discrepancy_events_count", Integer, nullable=False, default=0),
    Column("discrepancy_rate", Numeric(asdecimal=False), nullable=False, default=0),
    Column("computed_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("warehouse", "client_id", "week_start"),
    CheckConstraint("warehouse IN ('los_angeles', 'zaragoza')", name="ck_weekly_performance_warehouse"),
)

pipeline_runs = Table(
    "pipeline_runs",
    metadata,
    Column("run_id", Uuid, primary_key=True, default=uuid.uuid4),
    Column("pipeline_name", Text, nullable=False),
    Column("trigger", Text, nullable=False),
    Column("triggered_by", Uuid),
    Column("status", Text, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("window_start", Date, nullable=False),
    Column("window_end", Date, nullable=False),
    Column("source_max_timestamp", DateTime(timezone=True)),
    Column("records_extracted", Integer),
    Column("duplicates_dropped", Integer),
    Column("records_discarded", Integer),
    Column("rows_inserted", Integer),
    Column("rows_updated", Integer),
    Column("rows_unchanged", Integer),
    Column("rows_deleted", Integer),
    Column("optional_steps_failed", Integer, nullable=False, default=0),
    Column("error_stage", Text),
    Column("error_type", Text),
    Column("error_message", Text),
    Column("prefect_flow_run_id", Text),
    CheckConstraint("trigger IN ('schedule', 'manual', 'backfill')", name="ck_pipeline_runs_trigger"),
    CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_pipeline_runs_status"),
    # At most one run in progress per pipeline: the database refuses a second `running` row.
    Index("one_running_run", "pipeline_name", unique=True, postgresql_where=text("status = 'running'"), sqlite_where=text("status = 'running'")),
)

# Read-only view of the source table. Only the columns the pipeline needs; it is never created or written here.
telemetry_source = Table(
    "telemetry_events",
    MetaData(),
    Column("timestamp", DateTime(timezone=True)),
    Column("event_type", Text),
    Column("value", Numeric(asdecimal=False)),
    Column("tags", JSON().with_variant(JSONB(), "postgresql")),
)

_lock = threading.Lock()


def _attach_sqlite(engine: Engine) -> None:
    """Gives SQLite a `reporting` schema by attaching a second database (in memory, or a sibling file)."""
    main = engine.url.database
    target = ":memory:" if not main or main == ":memory:" else f"{main}.reporting"

    def attach(connection) -> None:
        cursor = connection.cursor()
        attached = {row[1] for row in cursor.execute("PRAGMA database_list").fetchall()}
        if "reporting" not in attached:
            cursor.execute("ATTACH DATABASE ? AS reporting", (target,))
        cursor.close()

    if not getattr(engine, "_reporting_listener", False):
        event.listen(engine, "connect", lambda connection, _record: attach(connection))
        engine._reporting_listener = True
    with engine.connect() as connection:
        attach(connection.connection.dbapi_connection)


def ensure_reporting(engine: Engine) -> None:
    """Creates the `reporting` schema and its two tables if they do not exist (idempotent, done once per engine)."""
    if getattr(engine, "_reporting_ready", False):
        return
    with _lock:
        if getattr(engine, "_reporting_ready", False):
            return
        if engine.dialect.name == "postgresql":
            with engine.begin() as connection:
                connection.exec_driver_sql(SQL_FILE.read_text(encoding="utf-8"))
        elif engine.dialect.name == "sqlite":
            _attach_sqlite(engine)
            metadata.create_all(engine)
        else:
            raise NotImplementedError(f"The reporting schema supports PostgreSQL and SQLite, not {engine.dialect.name}.")
        engine._reporting_ready = True
