"""Nightly telemetry job (ticket DEV-53): export yesterday's telemetry to CSV, trigger the data pipeline, record the run.

Usage (from the repository root, with DATABASE_URL set to the Supabase PostgreSQL connection string):

    python scripts/nightly_export.py
    TARGET_DATE=2026-11-20 python scripts/nightly_export.py      # any day, without touching the code

What one run does, for one UTC day (`TARGET_DATE`, or yesterday):

1. If that day already COMPLETED, it does nothing and says so (idempotent per day).
2. If another instance is `processing`, it aborts silently and says so (the status is the lock).
3. Otherwise: pending -> processing, then
   a. export the rows of `telemetry_events` for the day to `data/raw/telemetry_YYYY-MM-DD.csv`, unless the file exists;
      the CSV is a backup for audit and recovery: the pipeline does not read it,
   b. run the data pipeline as a subprocess (`python data/pipelines/pipeline.py`, which reads `telemetry_events` and
      writes its own `reporting.pipeline_runs`),
   c. processing -> completed. Any exception, or the process being interrupted, ends as failed with the message.

Every event is one log line with a timestamp, the job name and the resulting status. Exit codes: 0 completed or skipped,
1 failed, 2 bad configuration.

This is a separate process. It never imports FastAPI and never runs inside the API. The status logic is
`services/job_runner.py`, the trigger is the crontab in `scripts/nightly_export.crontab`.
"""

import csv
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import Engine, create_engine, select  # noqa: E402

from services import job_runner  # noqa: E402
from services.models import TelemetryEventRecord  # noqa: E402

JOB_NAME = job_runner.JOB_NIGHTLY_EXPORT
EXPORT_DIR = REPO_ROOT / "data" / "raw"
CSV_COLUMNS = ["id", "timestamp", "service", "event_type", "level", "value", "message", "tags"]
# The Milestone 6 entry point: the weekly KPI pipeline. It reads telemetry_events from the database, never the CSV.
DEFAULT_PIPELINE_COMMAND = [sys.executable, str(REPO_ROOT / "data" / "pipelines" / "pipeline.py"), "--trigger", "schedule"]
PIPELINE_TIMEOUT_SECONDS = 30 * 60

class ConfigurationError(RuntimeError):
    """The script cannot start: a missing DATABASE_URL or a TARGET_DATE that is not a usable day."""


class PipelineFailed(RuntimeError):
    """The pipeline subprocess ended with an error."""


# --- the day --------------------------------------------------------------------------------------------------------------


def resolve_target_date(environ=os.environ, today: date | None = None) -> date:
    """TARGET_DATE (YYYY-MM-DD) if set, otherwise yesterday in UTC. A future day is refused."""
    today = today or datetime.now(timezone.utc).date()
    raw = (environ.get("TARGET_DATE") or "").strip()
    if not raw:
        return today - timedelta(days=1)
    try:
        target = date.fromisoformat(raw)
    except ValueError as error:
        raise ConfigurationError(f"TARGET_DATE must be YYYY-MM-DD, got {raw!r}.") from error
    if target > today:
        raise ConfigurationError(f"TARGET_DATE {target} is in the future.")
    return target


# --- export ---------------------------------------------------------------------------------------------------------------


def export_directory(environ=os.environ) -> Path:
    """`data/raw`, or `NIGHTLY_EXPORT_DIR` when set (for tests, or a mounted backup volume)."""
    override = (environ.get("NIGHTLY_EXPORT_DIR") or "").strip()
    return Path(override) if override else EXPORT_DIR


def csv_path(target_date: date, directory: Path = EXPORT_DIR) -> Path:
    return directory / f"telemetry_{target_date.isoformat()}.csv"


def _iso(moment: datetime) -> str:
    moment = moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def export_telemetry_csv(engine: Engine, target_date: date, directory: Path = EXPORT_DIR) -> tuple[Path, int | None]:
    """Writes the telemetry of one UTC day to `telemetry_YYYY-MM-DD.csv`, unless the file already exists.

    Returns the path and the number of rows written, or `None` for the rows when the file was already there (it is never
    overwritten, so a second run cannot change a backup). The file is written under a temporary name and renamed at the
    end, so an interrupted export cannot leave a truncated file that later counts as done.
    """
    path = csv_path(target_date, directory)
    if path.exists():
        return path, None
    directory.mkdir(parents=True, exist_ok=True)
    start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
    table = TelemetryEventRecord.__table__
    statement = (
        select(*[table.c[name] for name in CSV_COLUMNS])
        .where(table.c.timestamp >= start, table.c.timestamp < start + timedelta(days=1))
        .order_by(table.c.timestamp, table.c.id)
    )
    partial = path.with_suffix(".csv.part")
    rows = 0
    try:
        with partial.open("w", encoding="utf-8", newline="") as handle, engine.connect() as connection:
            writer = csv.writer(handle)
            writer.writerow(CSV_COLUMNS)
            for row in connection.execution_options(stream_results=True).execute(statement):
                writer.writerow([
                    str(row.id), _iso(row.timestamp), row.service, row.event_type, row.level,
                    "" if row.value is None else row.value, row.message or "", json.dumps(row.tags, sort_keys=True, ensure_ascii=False),
                ])
                rows += 1
        os.replace(partial, path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return path, rows


# --- pipeline -------------------------------------------------------------------------------------------------------------


def pipeline_command(environ=os.environ) -> list[str]:
    """The pipeline command; `NIGHTLY_PIPELINE_COMMAND` replaces it (for tests, or another entry point)."""
    override = (environ.get("NIGHTLY_PIPELINE_COMMAND") or "").strip()
    return shlex.split(override) if override else list(DEFAULT_PIPELINE_COMMAND)


def run_pipeline(command: list[str], timeout: int = PIPELINE_TIMEOUT_SECONDS) -> None:
    """Runs the data pipeline as a subprocess and raises PipelineFailed if it fails or runs out of time."""
    try:
        result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout, env=os.environ.copy())
    except subprocess.TimeoutExpired as error:
        raise PipelineFailed(f"the pipeline did not finish within {timeout} seconds") from error
    if result.returncode != 0:
        tail = [line for line in (result.stderr or result.stdout or "").strip().splitlines() if line.strip()][-1:] or ["no output"]
        raise PipelineFailed(f"the pipeline exited with code {result.returncode}: {tail[0]}")


# --- the job --------------------------------------------------------------------------------------------------------------


def make_work(engine: Engine, target_date: date, directory: Path, command: list[str]):
    """The body of one run: export the CSV if needed, then trigger the pipeline."""

    def work() -> None:
        path, rows = export_telemetry_csv(engine, target_date, directory)
        if rows is None:
            job_runner_log(logging.INFO, "processing", f"CSV {path.name} already exists, not exported again")
        else:
            job_runner_log(logging.INFO, "processing", f"exported {rows} telemetry row(s) to {path.name}")
        run_pipeline(command)
        job_runner_log(logging.INFO, "processing", "the data pipeline finished")

    return work


def job_runner_log(level: int, status: str, message: str) -> None:
    job_runner.log_event(level, JOB_NAME, status, message)


def make_engine(environ=os.environ) -> Engine:
    url = (environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise ConfigurationError("DATABASE_URL must point to the TrackFlow Supabase PostgreSQL database.")
    options = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **options)


def main() -> int:
    job_runner.configure_logging()
    # A termination request must still run the `finally` of the state machine, so the row never stays `processing`.
    signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(143))
    try:
        target_date = resolve_target_date()
        engine = make_engine()
    except ConfigurationError as error:
        job_runner_log(logging.ERROR, "failed", f"not started: {error}")
        return 2
    if target_date == datetime.now(timezone.utc).date():
        job_runner_log(logging.WARNING, "pending", f"{target_date} is today: the day is not over, so the export is partial")
    try:
        outcome = job_runner.run_job(engine, JOB_NAME, target_date, make_work(engine, target_date, export_directory(), pipeline_command()))
    except Exception:  # noqa: BLE001 - already recorded as failed and logged at ERROR by run_job
        return 1
    job_runner_log(logging.INFO, outcome, f"exiting after {outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
