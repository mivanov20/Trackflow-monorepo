import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import DDL, CheckConstraint, Column, Date, DateTime, Index, Integer, JSON, Numeric, Text, Uuid, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel
from trackflow_shared.incidents import BRANCHES, CATEGORIES, ORIGINS, STATUSES


def utc_now() -> datetime:
    """Devuelve la hora UTC actual para registrar movimientos."""
    return datetime.now(timezone.utc)


class SKU(SQLModel, table=True):
    __tablename__ = "sku"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    sku: str = Field(index=True)
    client_name: str = Field(index=True)
    category: str = Field(index=True)
    warehouse: str = Field(index=True)


class StockEntry(SQLModel, table=True):
    __tablename__ = "stockentry"

    id: int | None = Field(default=None, primary_key=True)
    sku_id: int = Field(foreign_key="sku.id", index=True)
    quantity: int
    reference: str
    warehouse: str = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    user_uid: str = Field(index=True)


class StockExit(SQLModel, table=True):
    __tablename__ = "stockexit"

    id: int | None = Field(default=None, primary_key=True)
    sku_id: int = Field(foreign_key="sku.id", index=True)
    quantity: int
    exit_type: str
    tracking_number: str | None = None
    warehouse: str = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    user_uid: str = Field(index=True)

def _allowed_values(column: str, values) -> CheckConstraint:
    """Build a CHECK constraint restricting `column` to `values`."""
    quoted = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=f"ck_incident_{column}")


class Incident(SQLModel, table=True):
    """Operational incident; status, origin, category and branch are constrained in the database."""

    __tablename__ = "incident"
    __table_args__ = (
        _allowed_values("status", STATUSES),
        _allowed_values("origin", ORIGINS),
        _allowed_values("category", CATEGORIES),
        _allowed_values("branch", tuple(BRANCHES)),
    )

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=120)
    description: str
    category: str = Field(index=True)
    status: str = Field(index=True)
    origin: str = Field(index=True)
    branch: str = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)


class IncidentSeedLedger(SQLModel, table=True):
    """CSV ids already imported by `scripts/seed_incidents.py`.

    The analyzer's `incident_id` is not part of the incident model, so this ledger is
    what makes the seed script idempotent.
    """

    __tablename__ = "incident_seed_ledger"

    source_id: str = Field(primary_key=True)


TELEMETRY_LEVELS = ("info", "warn", "error")

# gen_random_uuid() exists in PostgreSQL (Supabase) but not in SQLite, which the tests and local development may use.
_POSTGRES = (os.getenv("DATABASE_URL") or "").startswith("postgres")


class TelemetryEventRecord(SQLModel, table=True):
    """One telemetry event. Rows are immutable facts: the application only ever inserts them.

    There is deliberately no update or delete anywhere in the code, and on PostgreSQL a trigger refuses both.
    The mapping from a `TelemetryEvent` to these columns is documented in docs/telemetry/telemetry-plan.md.
    """

    __tablename__ = "telemetry_events"
    __table_args__ = (
        CheckConstraint("level IN ('info', 'warn', 'error')", name="ck_telemetry_events_level"),
        Index("ix_telemetry_events_timestamp", "timestamp"),
        Index("ix_telemetry_events_event_type", "event_type"),
        Index("ix_telemetry_events_tags", "tags", postgresql_using="gin"),
    )

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()") if _POSTGRES else None),
    )
    timestamp: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    service: str = Field(sa_column=Column(Text, nullable=False))
    event_type: str = Field(sa_column=Column(Text, nullable=False))
    level: str = Field(default="info", sa_column=Column(Text, nullable=False, server_default=text("'info'")))
    value: Decimal | None = Field(default=None, sa_column=Column(Numeric, nullable=True))
    message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    tags: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False, server_default=text("'{}'")),
    )


# PostgreSQL only: refuse UPDATE and DELETE at the database, and keep the table closed to Supabase's public API.
# The API connects with a role that bypasses row level security, so it can still insert.
_TELEMETRY_TABLE = TelemetryEventRecord.__table__
for _statement in (
    """CREATE OR REPLACE FUNCTION telemetry_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'telemetry_events is append-only: % is not allowed', TG_OP;
END;
$$""",
    "CREATE TRIGGER telemetry_events_no_update_delete BEFORE UPDATE OR DELETE ON telemetry_events FOR EACH ROW EXECUTE FUNCTION telemetry_events_immutable()",
    "ALTER TABLE telemetry_events ENABLE ROW LEVEL SECURITY",
):
    event.listen(_TELEMETRY_TABLE, "after_create", DDL(_statement.replace("%", "%%")).execute_if(dialect="postgresql"))


JOB_STATUSES = ("pending", "processing", "completed", "failed")


class JobRun(SQLModel, table=True):
    """One execution of a background job (the nightly telemetry script), with its state machine.

    pending -> processing -> completed | failed. The `processing` status is the lock: the partial unique index below makes
    the check-and-set atomic, so two instances can never both be processing the same job. This table is the nightly
    orchestration layer; `reporting.pipeline_runs` (the ETL layer, Milestone 6) is a different table with a different job.
    """

    __tablename__ = "job_runs"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'processing', 'completed', 'failed')", name="ck_job_runs_status"),
        # Idempotency checks ("did this job complete for this day?") read this pair.
        Index("ix_job_runs_job_name_target_date", "job_name", "target_date"),
        # At most one `processing` row per job: the status itself is the lock, and the database makes taking it atomic.
        Index("one_processing_run_per_job", "job_name", unique=True, postgresql_where=text("status = 'processing'"), sqlite_where=text("status = 'processing'")),
    )

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()") if _POSTGRES else None),
    )
    job_name: str = Field(sa_column=Column(Text, nullable=False))
    target_date: date = Field(sa_column=Column(Date, nullable=False))
    status: str = Field(default="pending", sa_column=Column(Text, nullable=False, server_default=text("'pending'")))
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class DeadLetterTask(SQLModel, table=True):
    """The dead letter queue: a Celery task that failed on every attempt, recorded once with why and when.

    The message that failed carries identifiers only (never a data payload), so `payload` holds those identifiers.
    Rows are written by the worker, never updated or deleted by the application.
    """

    __tablename__ = "dead_letter_tasks"
    __table_args__ = (Index("ix_dead_letter_tasks_task_id", "task_id"), Index("ix_dead_letter_tasks_failed_at", "failed_at"))

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()") if _POSTGRES else None),
    )
    task_id: str = Field(sa_column=Column(Text, nullable=False))
    task_name: str = Field(sa_column=Column(Text, nullable=False))
    attempt: int = Field(sa_column=Column(Integer, nullable=False))
    error_message: str = Field(sa_column=Column(Text, nullable=False))
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False, server_default=text("'{}'")))
    failed_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
