"""The Celery tasks, and the dead letter queue.

Imported by the worker (and by the API, which only calls `.delay`). Nothing here imports FastAPI.

A task that raises is retried with exponential backoff: `max_retries=3` retries after the first attempt, waiting 5, 10 and
20 seconds. If the last retry also fails the task is dead: one row is written to `dead_letter_tasks` with the task id, the
attempt number, the error and the time. The row is written by `on_failure`, which Celery calls once, only when the task
will not be retried again.
"""

import logging
import os
import uuid
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any

from celery import Task
from sqlalchemy import Engine, create_engine, insert

from .celery_app import app
from .error_text import describe_error
from .models import DeadLetterTask

logger = logging.getLogger("trackflow.tasks")

MAX_RETRIES = 3
# Exponential backoff: the n-th retry waits BACKOFF * 2**(n-1) seconds: 5, 10, 20 by default. Never an immediate retry.
RETRY_BACKOFF_SECONDS = float(os.getenv("TASK_RETRY_BACKOFF_SECONDS", "5"))
RETRY_BACKOFF_MAX_SECONDS = 600

RUN_WEEKLY_PERFORMANCE_PIPELINE = "trackflow.run_weekly_performance_pipeline"


@lru_cache(maxsize=1)
def _engine() -> Engine:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL must point to the TrackFlow database so failed tasks can be recorded.")
    return create_engine(url, pool_pre_ping=True, **({"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}))


def record_dead_letter(engine: Engine, *, task_id: str, task_name: str, attempt: int, error: BaseException, payload: dict[str, Any]) -> None:
    """Writes the dead letter row: which task, which attempt, what went wrong and when."""
    table = DeadLetterTask.__table__
    table.create(engine, checkfirst=True)
    with engine.begin() as connection:
        connection.execute(
            insert(table).values(
                id=uuid.uuid4(), task_id=task_id, task_name=task_name, attempt=attempt, error_message=describe_error(error)[:2000],
                payload=payload, failed_at=datetime.now(timezone.utc),
            )
        )


class DeadLetterOnFailure(Task):
    """Base class: retry with backoff, and record the task in the dead letter queue when the retries run out."""

    autoretry_for = (Exception,)
    max_retries = MAX_RETRIES
    retry_backoff = RETRY_BACKOFF_SECONDS
    retry_backoff_max = RETRY_BACKOFF_MAX_SECONDS
    retry_jitter = False  # a predictable schedule: 5, 10, 20 seconds

    def on_failure(self, exc, task_id, args, kwargs, einfo):  # noqa: D401 - Celery hook
        attempt = int(self.request.retries or 0) + 1
        payload = {"args": list(args or ()), "kwargs": dict(kwargs or {})}
        try:
            record_dead_letter(_engine(), task_id=task_id, task_name=self.name, attempt=attempt, error=exc, payload=payload)
            logger.error("task_id=%s task=%s attempt=%d status=dead_letter recorded after %d retries", task_id, self.name, attempt, self.max_retries)
        except Exception:  # noqa: BLE001 - the task is lost either way; keep every detail in the log
            logger.exception("task_id=%s task=%s attempt=%d status=dead_letter COULD NOT BE RECORDED error=%s", task_id, self.name, attempt, describe_error(exc))


@app.task(
    bind=True,
    base=DeadLetterOnFailure,
    name=RUN_WEEKLY_PERFORMANCE_PIPELINE,
    soft_time_limit=25 * 60,
    time_limit=30 * 60,
)
def run_weekly_performance_pipeline(self, run_id: str, week_start: str | None = None, weeks_back: int = 2, triggered_by: str | None = None) -> dict:
    """Runs the Weekly Warehouse & Client Performance pipeline for a manual run the API already opened.

    The message carries identifiers and two small parameters only: the id of the run (a row of `reporting.pipeline_runs`), the
    week to recompute and who asked. The pipeline reads `telemetry_events` itself. The heavy work is `data/pipelines`.
    """
    from data.pipelines import pipeline  # imported here: it pulls Prefect, which only the worker needs

    return pipeline.run_manual_flow(run_id, date.fromisoformat(week_start) if week_start else None, weeks_back, triggered_by)
