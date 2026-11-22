"""State machine of background jobs: the `job_runs` table, the lock and the idempotency check.

Used by `scripts/nightly_export.py`. It deliberately imports nothing from FastAPI and nothing from `services.database`
(which does), so a script that uses it is an independent process that never touches the API.

    pending -> processing -> completed
                          \\-> failed

* pending: the record is created before any work begins.
* processing: set at the very start of the work. THE LOCK IS THIS STATUS: a partial unique index on
  `job_runs (job_name) WHERE status = 'processing'` makes the check-and-set atomic, so two instances started together
  cannot both take it. There is no lock table and no lock column.
* completed: only if everything finished.
* failed: any exception, including the process being interrupted. A row never stays `processing` after a run that ends.

A process killed without any chance to react (SIGKILL, power loss) cannot update its row. Such a row is recovered by lease
expiry: a `processing` row older than `stale_after` is marked `failed` by the next run, so the lock never stays held forever.
"""

import logging
import re
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import Engine, delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from .models import JobRun

JOB_NIGHTLY_EXPORT = "nightly_export"

# A nightly export takes seconds to minutes. A `processing` row older than this belongs to a process that died.
STALE_AFTER = timedelta(hours=3)
MAX_ERROR_LENGTH = 1000

LOG_FORMAT = "%(asctime)s %(levelname)s job=%(job_name)s status=%(status)s %(message)s"

Outcome = Literal["completed", "skipped_duplicate", "skipped_locked"]

table = JobRun.__table__


class JobLockHeld(RuntimeError):
    """Another instance of the job is `processing`."""


def configure_logging(level: int = logging.INFO) -> None:
    """One line per event with a timestamp, the job name and the resulting status, on standard output."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT, defaults={"job_name": "-", "status": "-"}))
    root = logging.getLogger("job_runner")
    root.handlers = [handler]
    root.setLevel(level)
    root.propagate = False


def log_event(level: int, job_name: str, status: str, message: str) -> None:
    """One log line: the event with the job name and the resulting status (the format adds the timestamp and the level)."""
    logging.getLogger("job_runner").log(level, message, extra={"job_name": job_name, "status": status})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def describe_error(error: BaseException) -> str:
    """The text stored in `error_message`: the class and the message, without URLs or credentials, at most 1000 characters."""
    text = f"{type(error).__name__}: {error}".strip()
    cleaned = re.sub(r"\b[a-z][a-z0-9+.-]*://\S+", "<url>", text, flags=re.I)
    return cleaned[:MAX_ERROR_LENGTH]


# --- the table ----------------------------------------------------------------------------------------------------------


def ensure_job_runs_table(engine: Engine) -> None:
    """Creates `job_runs` (with its index and the one-processing-row index) if it does not exist."""
    table.create(engine, checkfirst=True)


def create_job_run(engine: Engine, job_name: str, target_date: date) -> uuid.UUID:
    """Records a new run as `pending` and returns its id."""
    run_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(insert(table).values(id=run_id, job_name=job_name, target_date=target_date, status="pending", created_at=_now()))
    return run_id


def has_processing_lock(engine: Engine, job_name: str) -> bool:
    """True when a run of this job is `processing`: the lock is held."""
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(table).where(table.c.job_name == job_name, table.c.status == "processing")).scalar_one() > 0


def has_completed_for_date(engine: Engine, job_name: str, target_date: date) -> bool:
    """True when this job already completed for this day. Checking the job name alone would not be enough."""
    with engine.connect() as connection:
        return (
            connection.execute(
                select(func.count()).select_from(table).where(table.c.job_name == job_name, table.c.target_date == target_date, table.c.status == "completed")
            ).scalar_one()
            > 0
        )


def mark_processing(engine: Engine, run_id: uuid.UUID) -> bool:
    """pending -> processing, atomically. False when another run holds the lock (the database refuses a second one)."""
    try:
        with engine.begin() as connection:
            changed = connection.execute(
                update(table).where(table.c.id == run_id, table.c.status == "pending").values(status="processing", started_at=_now())
            ).rowcount
        return changed == 1
    except IntegrityError:
        return False


def mark_completed(engine: Engine, run_id: uuid.UUID) -> None:
    """processing -> completed. This releases the lock."""
    with engine.begin() as connection:
        connection.execute(update(table).where(table.c.id == run_id, table.c.status == "processing").values(status="completed", finished_at=_now(), error_message=None))


def mark_failed(engine: Engine, run_id: uuid.UUID, error: BaseException | str) -> None:
    """processing (or pending) -> failed, with the error message. This releases the lock."""
    message = error if isinstance(error, str) else describe_error(error)
    with engine.begin() as connection:
        connection.execute(
            update(table).where(table.c.id == run_id, table.c.status.in_(["pending", "processing"])).values(status="failed", finished_at=_now(), error_message=message[:MAX_ERROR_LENGTH])
        )


def delete_unstarted_run(engine: Engine, run_id: uuid.UUID) -> None:
    """Removes a `pending` row whose run never started (it lost the race for the lock, or its day was already done)."""
    with engine.begin() as connection:
        connection.execute(delete(table).where(table.c.id == run_id, table.c.status.in_(["pending", "processing"]), table.c.finished_at.is_(None)))


def fail_stale_processing(engine: Engine, job_name: str, stale_after: timedelta = STALE_AFTER) -> int:
    """Marks `processing` rows older than `stale_after` as failed: their process died and could not do it itself."""
    with engine.begin() as connection:
        return connection.execute(
            update(table)
            .where(table.c.job_name == job_name, table.c.status == "processing", table.c.started_at < _now() - stale_after)
            .values(status="failed", finished_at=_now(), error_message="Abandoned: the process stopped without finishing and the run was recovered by a later one.")
        ).rowcount


def get_run(engine: Engine, run_id: uuid.UUID) -> dict | None:
    with engine.connect() as connection:
        row = connection.execute(select(table).where(table.c.id == run_id)).mappings().first()
    return dict(row) if row else None


def list_runs(engine: Engine, job_name: str) -> list[dict]:
    """Every run of a job, oldest first."""
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(table).where(table.c.job_name == job_name).order_by(table.c.created_at, table.c.id)).mappings()]


# --- the state machine ------------------------------------------------------------------------------------------------------


def run_job(engine: Engine, job_name: str, target_date: date, work: Callable[[], None], stale_after: timedelta = STALE_AFTER) -> Outcome:
    """Runs `work` for one day of one job under the state machine.

    Returns `completed`, `skipped_duplicate` (this day already completed) or `skipped_locked` (another instance is
    processing); both skips are silent apart from an INFO line. If `work` raises, the run is marked `failed` with the
    message and the exception is raised again. Whatever happens, the row never stays `processing`.
    """
    ensure_job_runs_table(engine)
    recovered = fail_stale_processing(engine, job_name, stale_after)
    if recovered:
        log_event(logging.WARNING, job_name, "failed", f"recovered {recovered} abandoned run(s) that had been processing for over {stale_after}")

    if has_completed_for_date(engine, job_name, target_date):
        log_event(logging.INFO, job_name, "skipped", f"skipped as a duplicate: {target_date} is already completed")
        return "skipped_duplicate"
    if has_processing_lock(engine, job_name):
        log_event(logging.INFO, job_name, "cancelled", f"cancelled for {target_date}: another run is processing")
        return "skipped_locked"

    run_id = create_job_run(engine, job_name, target_date)
    log_event(logging.INFO, job_name, "pending", f"run {run_id} created for {target_date}")

    if not mark_processing(engine, run_id):  # another instance took the lock between the check and now
        delete_unstarted_run(engine, run_id)
        log_event(logging.INFO, job_name, "cancelled", f"cancelled for {target_date}: another run took the lock first")
        return "skipped_locked"
    if has_completed_for_date(engine, job_name, target_date):  # another instance finished this day while we were starting
        delete_unstarted_run(engine, run_id)
        log_event(logging.INFO, job_name, "skipped", f"skipped as a duplicate: {target_date} was completed while this run started")
        return "skipped_duplicate"

    settled = False
    try:
        log_event(logging.INFO, job_name, "processing", f"run {run_id} started for {target_date}")
        work()
        mark_completed(engine, run_id)
        settled = True
        log_event(logging.INFO, job_name, "completed", f"run {run_id} finished for {target_date}")
        return "completed"
    except BaseException as error:  # noqa: BLE001 - includes KeyboardInterrupt and SystemExit: the lock must be released
        log_event(logging.ERROR, job_name, "failed", f"run {run_id} failed for {target_date}: {describe_error(error)}")
        mark_failed(engine, run_id, error)
        settled = True
        raise
    finally:
        if not settled:  # reached only if recording the outcome itself raised: one more try to leave the row as failed
            mark_failed(engine, run_id, "The run ended without recording its outcome.")
