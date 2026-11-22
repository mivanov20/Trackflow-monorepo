"""The execution log: one row per run in `reporting.pipeline_runs` (design 3.3), and who may run at the same time."""

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.exc import IntegrityError

from .config import PIPELINE_NAME, STALE_RUN_MINUTES
from .schema import ensure_reporting, pipeline_runs

COUNTER_FIELDS = (
    "records_extracted", "duplicates_dropped", "records_discarded", "rows_inserted", "rows_updated", "rows_unchanged", "rows_deleted",
)


class RunAlreadyRunning(RuntimeError):
    """Another run of this pipeline is in progress: the database allows one `running` row at a time."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_error(error: BaseException, limit: int = 500) -> tuple[str, str]:
    """(error_type, error_message) safe to store: the class name, and the first line of the message without URLs or credentials."""
    first_line = (str(error).strip().splitlines() or [""])[0]
    cleaned = re.sub(r"\b[a-z][a-z0-9+.-]*://\S+", "<url>", first_line, flags=re.I)
    return type(error).__name__, cleaned[:limit]


def open_run(
    engine: Engine,
    *,
    trigger: str,
    triggered_by: str | None,
    window_start: date,
    window_end: date,
    run_id: uuid.UUID | None = None,
    prefect_flow_run_id: str | None = None,
) -> uuid.UUID:
    """Marks abandoned runs failed, then records this run as `running`. Raises RunAlreadyRunning if one is in progress.

    With a `run_id` (a manual trigger already opened its run) it adopts that row instead of inserting a new one.
    """
    ensure_reporting(engine)
    table = pipeline_runs
    now = _now()
    try:
        with engine.begin() as connection:
            abandoned = [
                table.c.pipeline_name == PIPELINE_NAME,
                table.c.status == "running",
                table.c.started_at < now - timedelta(minutes=STALE_RUN_MINUTES),
            ]
            if run_id is not None:
                abandoned.append(table.c.run_id != run_id)
            connection.execute(
                update(table).where(*abandoned).values(
                    status="failed", finished_at=now, error_stage="abandoned", error_type="abandoned",
                    error_message="The run never closed and was marked failed by a later run.",
                )
            )
            if run_id is not None:
                connection.execute(update(table).where(table.c.run_id == run_id).values(prefect_flow_run_id=prefect_flow_run_id))
                return run_id
            new_id = uuid.uuid4()
            connection.execute(
                insert(table).values(
                    run_id=new_id, pipeline_name=PIPELINE_NAME, trigger=trigger,
                    triggered_by=uuid.UUID(triggered_by) if triggered_by else None,
                    status="running", started_at=now, window_start=window_start, window_end=window_end,
                    optional_steps_failed=0, prefect_flow_run_id=prefect_flow_run_id,
                )
            )
            return new_id
    except IntegrityError as error:
        raise RunAlreadyRunning("Another run of this pipeline is already in progress.") from error


def close_run(
    engine: Engine,
    run_id: uuid.UUID,
    *,
    status: str,
    counters: dict[str, int | None] | None = None,
    source_max_timestamp: datetime | None = None,
    optional_steps_failed: int = 0,
    error_stage: str | None = None,
    error: BaseException | None = None,
) -> None:
    """Writes the final state of a run: status, end time, counters and, for a failure, where and why."""
    error_type, error_message = sanitize_error(error) if error is not None else (None, None)
    values: dict[str, Any] = {
        "status": status, "finished_at": _now(), "source_max_timestamp": source_max_timestamp,
        "optional_steps_failed": optional_steps_failed, "error_stage": error_stage, "error_type": error_type, "error_message": error_message,
    }
    values.update({name: (counters or {}).get(name) for name in COUNTER_FIELDS})
    ensure_reporting(engine)
    with engine.begin() as connection:
        connection.execute(update(pipeline_runs).where(pipeline_runs.c.run_id == run_id).values(**values))


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return value.isoformat()


def get_latest_run(engine: Engine, pipeline_name: str = PIPELINE_NAME) -> dict[str, Any] | None:
    """The newest row of the execution log as a JSON-ready dict, or None before the first run."""
    ensure_reporting(engine)
    with engine.connect() as connection:
        row = connection.execute(
            select(pipeline_runs).where(pipeline_runs.c.pipeline_name == pipeline_name).order_by(pipeline_runs.c.started_at.desc()).limit(1)
        ).mappings().first()
    if row is None:
        return None
    record = dict(row)
    for name in ("run_id", "triggered_by"):
        record[name] = str(record[name]) if record[name] else None
    for name in ("started_at", "finished_at", "source_max_timestamp", "window_start", "window_end"):
        record[name] = _iso(record[name])
    return record
