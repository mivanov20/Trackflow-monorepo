"""services/job_runner.py: the pending -> processing -> completed | failed state machine, the lock and the idempotency.

Runs on SQLite always, and on PostgreSQL too when TEST_POSTGRES_URL is set (a scratch database: the tests drop job_runs).
"""

import ast
import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import StaticPool, create_engine, insert, text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel

from services import job_runner
from services.job_runner import JOB_NIGHTLY_EXPORT as JOB
from services.models import JobRun

REPO_ROOT = Path(__file__).resolve().parents[2]
DAY = date(2026, 11, 20)
OTHER_DAY = date(2026, 11, 19)
table = JobRun.__table__


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request):
    if request.param == "sqlite":
        database = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(database)
    else:
        url = os.getenv("TEST_POSTGRES_URL")
        if not url:
            pytest.skip("TEST_POSTGRES_URL is not set")
        database = create_engine(url)
        with database.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS job_runs CASCADE"))
        SQLModel.metadata.create_all(database)
    yield database
    database.dispose()


def imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    names |= {("." * node.level) + (node.module or "") for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    return names


def imported_modules(path: Path) -> set[str]:
    """Top-level module names a file imports (relative imports are `services`-local and are listed by imported_names)."""
    return {name.split(".")[0] for name in imported_names(path) if not name.startswith(".")}


def insert_run(engine, status="completed", target_date=DAY, job_name=JOB, started_ago=None):
    started = datetime.now(timezone.utc) - started_ago if started_ago else None
    with engine.begin() as connection:
        connection.execute(insert(table).values(id=uuid.uuid4(), job_name=job_name, target_date=target_date, status=status, started_at=started, created_at=datetime.now(timezone.utc)))


def runs(engine, job_name=JOB):
    return job_runner.list_runs(engine, job_name)


def statuses(engine):
    return [run["status"] for run in runs(engine)]


# --- the state machine -----------------------------------------------------------------------------------------------------


def test_a_successful_run_goes_pending_processing_completed_and_records_the_day(engine, monkeypatch):
    seen = []
    real_mark_processing = job_runner.mark_processing

    def watch(engine_, run_id):
        seen.append(("before processing", job_runner.get_run(engine_, run_id)["status"]))
        result = real_mark_processing(engine_, run_id)
        seen.append(("after processing", job_runner.get_run(engine_, run_id)["status"]))
        return result

    monkeypatch.setattr(job_runner, "mark_processing", watch)
    inside = []
    outcome = job_runner.run_job(engine, JOB, DAY, lambda: inside.append(statuses(engine)))

    assert outcome == "completed"
    assert seen == [("before processing", "pending"), ("after processing", "processing")]  # created pending, processing before any work
    assert inside == [["processing"]]  # while the work runs, the row says so
    (run,) = runs(engine)
    assert run["status"] == "completed" and run["job_name"] == JOB and run["target_date"] == DAY
    assert run["started_at"] is not None and run["finished_at"] is not None and run["error_message"] is None and run["created_at"] is not None


def test_a_failure_ends_as_failed_with_the_message_and_is_raised_again(engine):
    def broken():
        raise RuntimeError("the export disk is full")

    with pytest.raises(RuntimeError, match="disk is full"):
        job_runner.run_job(engine, JOB, DAY, broken)
    (run,) = runs(engine)
    assert run["status"] == "failed" and run["finished_at"] is not None
    assert run["error_message"] == "RuntimeError: the export disk is full"
    assert not job_runner.has_processing_lock(engine, JOB)  # the lock is released


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_an_interruption_also_ends_as_failed_never_as_processing(engine, interruption):
    def interrupted():
        raise interruption()

    with pytest.raises(interruption):
        job_runner.run_job(engine, JOB, DAY, interrupted)
    assert statuses(engine) == ["failed"]


def test_if_recording_the_failure_fails_once_the_finally_block_still_leaves_the_row_failed(engine, monkeypatch):
    real = job_runner.mark_failed
    calls = []

    def flaky(engine_, run_id, error):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("the connection dropped while recording the failure")
        real(engine_, run_id, error)

    monkeypatch.setattr(job_runner, "mark_failed", flaky)
    with pytest.raises(ConnectionError):
        job_runner.run_job(engine, JOB, DAY, lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert statuses(engine) == ["failed"] and len(calls) == 2


# --- the lock is the processing status -------------------------------------------------------------------------------------


def test_a_second_instance_aborts_silently_while_one_is_processing(engine, caplog):
    insert_run(engine, status="processing", target_date=OTHER_DAY, started_ago=timedelta(minutes=5))
    worked = []
    with caplog.at_level(logging.INFO, logger="job_runner"):
        outcome = job_runner.run_job(engine, JOB, DAY, lambda: worked.append(1))
    assert outcome == "skipped_locked" and worked == []
    assert statuses(engine) == ["processing"]  # no new row, nothing changed
    assert any("cancelled" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)


def test_the_lock_is_per_job(engine):
    insert_run(engine, status="processing", job_name="some_other_job", started_ago=timedelta(minutes=5))
    assert job_runner.run_job(engine, JOB, DAY, lambda: None) == "completed"
    assert not job_runner.has_processing_lock(engine, JOB) and job_runner.has_processing_lock(engine, "some_other_job")


def test_the_database_refuses_a_second_processing_row_for_the_same_job(engine):
    insert_run(engine, status="processing", started_ago=timedelta(minutes=1))
    with pytest.raises(IntegrityError):
        insert_run(engine, status="processing", target_date=OTHER_DAY, started_ago=timedelta(minutes=1))
    insert_run(engine, status="completed", target_date=OTHER_DAY)  # any number of finished rows is fine


def test_two_instances_that_both_pass_the_check_cannot_both_process(engine, monkeypatch):
    """The race: the other instance takes the lock between this one's check and its transition to processing."""
    insert_run(engine, status="processing", target_date=OTHER_DAY, started_ago=timedelta(minutes=1))
    monkeypatch.setattr(job_runner, "has_processing_lock", lambda *_a, **_k: False)  # this instance saw nothing
    worked = []
    assert job_runner.run_job(engine, JOB, DAY, lambda: worked.append(1)) == "skipped_locked"
    assert worked == [] and statuses(engine) == ["processing"]  # the loser deleted its own pending row: no zombie


def test_a_stale_lock_from_a_dead_process_is_recovered(engine):
    insert_run(engine, status="processing", target_date=OTHER_DAY, started_ago=timedelta(hours=5))
    assert job_runner.run_job(engine, JOB, DAY, lambda: None) == "completed"
    old, new = runs(engine)
    assert (old["status"], new["status"]) == ("failed", "completed")
    assert "Abandoned" in old["error_message"] and old["finished_at"] is not None


def test_a_recent_processing_row_is_not_stale(engine):
    insert_run(engine, status="processing", target_date=OTHER_DAY, started_ago=timedelta(minutes=10))
    assert job_runner.run_job(engine, JOB, DAY, lambda: None) == "skipped_locked"


# --- idempotency per target_date ---------------------------------------------------------------------------------------------


def test_a_completed_day_is_skipped_as_a_duplicate_and_the_work_does_not_run(engine, caplog):
    job_runner.run_job(engine, JOB, DAY, lambda: None)
    worked = []
    with caplog.at_level(logging.INFO, logger="job_runner"):
        outcome = job_runner.run_job(engine, JOB, DAY, lambda: worked.append(1))
    assert outcome == "skipped_duplicate" and worked == []
    assert statuses(engine) == ["completed"]  # still one row: running twice is the same as running once
    assert any("duplicate" in r.getMessage() for r in caplog.records)


def test_idempotency_looks_at_the_day_not_only_the_job_name(engine):
    job_runner.run_job(engine, JOB, OTHER_DAY, lambda: None)
    assert job_runner.has_completed_for_date(engine, JOB, OTHER_DAY) and not job_runner.has_completed_for_date(engine, JOB, DAY)
    worked = []
    assert job_runner.run_job(engine, JOB, DAY, lambda: worked.append(1)) == "completed" and worked == [1]


def test_a_failed_day_can_be_retried(engine):
    with pytest.raises(RuntimeError):
        job_runner.run_job(engine, JOB, DAY, lambda: (_ for _ in ()).throw(RuntimeError("first attempt")))
    assert job_runner.run_job(engine, JOB, DAY, lambda: None) == "completed"
    assert statuses(engine) == ["failed", "completed"]


def test_a_day_completed_while_this_instance_was_starting_is_not_done_twice(engine, monkeypatch):
    real = job_runner.has_completed_for_date
    calls = []

    def finishes_in_between(engine_, job_name, target_date):
        calls.append(1)
        if len(calls) == 1:
            result = real(engine_, job_name, target_date)  # False: nothing yet
            insert_run(engine_, status="completed", target_date=target_date)  # the other instance finishes right after
            return result
        return real(engine_, job_name, target_date)

    monkeypatch.setattr(job_runner, "has_completed_for_date", finishes_in_between)
    worked = []
    assert job_runner.run_job(engine, JOB, DAY, lambda: worked.append(1)) == "skipped_duplicate"
    assert worked == [] and statuses(engine) == ["completed"]


# --- the log ----------------------------------------------------------------------------------------------------------------


def test_every_event_is_logged_with_timestamp_job_and_status(engine, capfd):
    job_runner.configure_logging()
    job_runner.run_job(engine, JOB, DAY, lambda: None)
    with pytest.raises(RuntimeError):
        job_runner.run_job(engine, JOB, OTHER_DAY, lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    job_runner.run_job(engine, JOB, DAY, lambda: None)
    lines = [line for line in capfd.readouterr().err.splitlines() if "job=" in line]
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} (INFO|WARNING|ERROR) job=nightly_export status=(\w+) ")
    assert lines and all(pattern.match(line) for line in lines), lines
    assert {pattern.match(line).group(2) for line in lines} >= {"pending", "processing", "completed", "failed", "skipped"}
    assert any(line.split()[2] == "ERROR" and "boom" in line for line in lines)  # the exception message, at ERROR
    assert all(line.split()[2] == "INFO" for line in lines if "status=completed" in line or "status=skipped" in line)


def test_error_messages_are_cut_and_carry_no_credentials():
    text_ = job_runner.describe_error(RuntimeError("could not connect to postgresql://user:secret@db.example.com:5432/postgres " + "x" * 2000))
    assert "secret" not in text_ and "postgresql://" not in text_ and len(text_) <= job_runner.MAX_ERROR_LENGTH


# --- job_runs is not pipeline_runs -------------------------------------------------------------------------------------------


def test_job_runs_and_pipeline_runs_are_different_tables_for_different_layers():
    from data.pipelines.weekly_performance.schema import pipeline_runs

    assert table.name == "job_runs" and pipeline_runs.name == "pipeline_runs" and pipeline_runs.schema == "reporting" and table.schema is None
    assert {c.name for c in table.columns} == {"id", "job_name", "target_date", "status", "started_at", "finished_at", "error_message", "created_at"}
    assert not {"window_start", "window_end", "records_extracted", "rows_inserted"} & {c.name for c in table.columns}  # ETL fields stay in pipeline_runs
    assert {i.name for i in table.indexes} == {"ix_job_runs_job_name_target_date", "one_processing_run_per_job"}
    assert not imported_modules(REPO_ROOT / "services" / "job_runner.py") & {"data"}  # the job layer does not import the ETL layer


def test_the_state_machine_imports_no_fastapi_and_the_api_runs_no_scheduler():
    imports = imported_modules(REPO_ROOT / "services" / "job_runner.py")
    assert not imports & {"fastapi", "starlette", "uvicorn"}
    assert "services.database" not in {m for m in imported_names(REPO_ROOT / "services" / "job_runner.py")}
    for path in (REPO_ROOT / "services").rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts:
            continue
        code = path.read_text(encoding="utf-8")
        for forbidden in ("apscheduler", "repeat_every", "BackgroundScheduler", "AsyncIOScheduler"):
            assert forbidden not in code, f"{path.name} mentions {forbidden}: the nightly job must not run inside the API"
        if path.name not in {"job_runner.py", "models.py"}:  # the state machine and its table; nothing of the API uses them
            assert "job_runner" not in code and "nightly_export" not in code, f"{path.name} uses the nightly job"
    main = (REPO_ROOT / "services" / "main.py").read_text(encoding="utf-8")
    assert "job_runner" not in main and "lifespan" in main  # the lifespan exists, but no job is started from it


def test_the_sql_file_for_supabase_matches_the_model():
    sql = (REPO_ROOT / "services" / "sql" / "job_runs.sql").read_text(encoding="utf-8")
    for column in ("id            uuid primary key default gen_random_uuid()", "job_name      text not null", "target_date   date not null", "error_message text", "created_at    timestamptz not null default now()"):
        assert column in sql
    assert "create index if not exists ix_job_runs_job_name_target_date on job_runs (job_name, target_date)" in sql
    assert "create unique index if not exists one_processing_run_per_job on job_runs (job_name) where status = 'processing'" in sql
    assert "'pending', 'processing', 'completed', 'failed'" in sql
