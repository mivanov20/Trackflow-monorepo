"""scripts/nightly_export.py: the CSV backup, the target date, and the whole script as a real separate process."""

import csv
import importlib.util
import json
import re
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from reporting_fixtures import add_event
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from services import job_runner

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "nightly_export.py"
DAY = date(2026, 11, 20)

_spec = importlib.util.spec_from_file_location("nightly_export_script", SCRIPT)
nightly = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nightly)

LA = {"warehouse": "los_angeles", "client_id": "fashion-co"}

FAKE_PIPELINE = """\
import os, sys, time
marker, seconds, code = sys.argv[1], float(sys.argv[2]), int(sys.argv[3])
with open(marker, "a") as handle:
    handle.write(f"{os.getpid()}\\n")
time.sleep(seconds)
if code:
    print("Traceback (most recent call last):", file=sys.stderr)
    print("RuntimeError: the pipeline lost the database", file=sys.stderr)
sys.exit(code)
"""


@pytest.fixture
def database(tmp_path):
    """A SQLite file shared by the test and by the script processes, with the telemetry events of three days."""
    path = tmp_path / "trackflow.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    add_event(engine, "2026-11-19T23:59:59Z", "page_viewed", {"section": "overview"})  # the day before: not exported
    add_event(engine, "2026-11-20T00:00:00Z", "inbound_order_created", {**LA, "quantity": 40, "order_id": "1"})  # first instant of the day
    add_event(engine, "2026-11-20T12:30:00Z", "outbound_order_created", {**LA, "exit_type": "dispatch", "quantity": 3, "order_id": "2"})
    add_event(engine, "2026-11-20T23:59:59Z", "frontend_error_caught", {"route_template": "/login", "error_name": "TypeError", "occurrences": 1})
    add_event(engine, "2026-11-21T00:00:00Z", "page_viewed", {"section": "overview"})  # the day after: not exported
    yield engine
    engine.dispose()


@pytest.fixture
def workspace(tmp_path, database):
    """Folders and the fake pipeline the script process will use, and a way to run the script."""
    fake = tmp_path / "fake_pipeline.py"
    fake.write_text(FAKE_PIPELINE, encoding="utf-8")
    marker = tmp_path / "pipeline_runs.txt"
    exports = tmp_path / "raw"

    class Workspace:
        engine = database
        export_dir = exports
        runs_marker = marker

        @staticmethod
        def env(pipeline_seconds=0, pipeline_exit=0, **extra):
            python = Path(sys.executable).as_posix()
            return {
                **__import__("os").environ,
                "DATABASE_URL": str(database.url),
                "NIGHTLY_EXPORT_DIR": str(exports),
                "NIGHTLY_PIPELINE_COMMAND": f"{python} {fake.as_posix()} {marker.as_posix()} {pipeline_seconds} {pipeline_exit}",
                "TARGET_DATE": "2026-11-20",
                **extra,
            }

        @staticmethod
        def run(**kwargs):
            return subprocess.run([sys.executable, str(SCRIPT)], cwd=REPO_ROOT, env=Workspace.env(**kwargs), capture_output=True, text=True, timeout=120)

        @staticmethod
        def start(**kwargs):
            return subprocess.Popen([sys.executable, str(SCRIPT)], cwd=REPO_ROOT, env=Workspace.env(**kwargs), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        @staticmethod
        def pipeline_launches():
            return len(marker.read_text().split()) if marker.exists() else 0

        @staticmethod
        def runs():
            return job_runner.list_runs(database, job_runner.JOB_NIGHTLY_EXPORT)

    return Workspace


def read_csv(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


# --- the target date ---------------------------------------------------------------------------------------------------------


def test_the_target_date_is_yesterday_in_utc_unless_target_date_says_otherwise():
    today = date(2026, 11, 22)
    assert nightly.resolve_target_date({}, today) == date(2026, 11, 21)
    assert nightly.resolve_target_date({"TARGET_DATE": ""}, today) == date(2026, 11, 21)
    assert nightly.resolve_target_date({"TARGET_DATE": "2026-01-15"}, today) == date(2026, 1, 15)
    assert nightly.resolve_target_date({"TARGET_DATE": "2026-11-22"}, today) == today  # today is allowed (it is partial; the script warns)
    assert nightly.resolve_target_date({"TARGET_DATE": " 2026-02-01 "}, today) == date(2026, 2, 1)
    for bad in ("yesterday", "2026-13-01", "20-11-2026", "2026-11-23"):
        with pytest.raises(nightly.ConfigurationError):
            nightly.resolve_target_date({"TARGET_DATE": bad}, today)


def test_yesterday_uses_the_utc_calendar_day():
    just_after_midnight_utc = date(2026, 3, 1)  # whatever the server's local date is, the argument is the UTC date
    assert nightly.resolve_target_date({}, just_after_midnight_utc) == date(2026, 2, 28)


# --- the CSV backup ----------------------------------------------------------------------------------------------------------


def test_the_csv_has_the_name_the_columns_and_only_the_events_of_the_day(database, tmp_path):
    path, rows = nightly.export_telemetry_csv(database, DAY, tmp_path)
    assert path.name == "telemetry_2026-11-20.csv" and rows == 3
    table = read_csv(path)
    assert list(table[0]) == ["id", "timestamp", "service", "event_type", "level", "value", "message", "tags"]
    assert [row["event_type"] for row in table] == ["inbound_order_created", "outbound_order_created", "frontend_error_caught"]  # in time order
    assert [row["timestamp"] for row in table] == ["2026-11-20T00:00:00.000Z", "2026-11-20T12:30:00.000Z", "2026-11-20T23:59:59.000Z"]  # both edges of the day in, neighbours out
    first = table[0]
    assert first["service"] == "backoffice" and float(first["value"]) == 40
    tags = json.loads(first["tags"])
    assert tags["warehouse"] == "los_angeles" and tags["client_id"] == "fashion-co" and tags["quantity"] == 40 and "_envelope" in tags


def test_an_existing_csv_is_never_exported_again_or_changed(database, tmp_path):
    path, _ = nightly.export_telemetry_csv(database, DAY, tmp_path)
    before = path.read_bytes()
    add_event(database, "2026-11-20T15:00:00Z", "inbound_order_created", {**LA, "quantity": 99, "order_id": "9"})  # a late event
    again, rows = nightly.export_telemetry_csv(database, DAY, tmp_path)
    assert again == path and rows is None and path.read_bytes() == before  # a backup is not rewritten


def test_a_day_without_events_still_gets_a_header_only_file_and_no_temporary_file_is_left(database, tmp_path):
    path, rows = nightly.export_telemetry_csv(database, date(2026, 10, 1), tmp_path)
    assert rows == 0 and read_csv(path) == [] and path.read_text(encoding="utf-8").startswith("id,timestamp,service")
    assert not list(tmp_path.glob("*.part"))


def test_an_interrupted_export_leaves_no_file_that_would_count_as_done(database, tmp_path, monkeypatch):
    class Boom(Exception):
        pass

    real_writer = nightly.csv.writer

    def failing_writer(handle):
        writer = real_writer(handle)
        calls = {"n": 0}
        original = writer.writerow

        class Wrapped:
            def writerow(self, row):
                calls["n"] += 1
                if calls["n"] == 3:
                    raise Boom("the disk filled up half way")
                return original(row)

        return Wrapped()

    out = tmp_path / "out"
    monkeypatch.setattr(nightly.csv, "writer", failing_writer)
    with pytest.raises(Boom):
        nightly.export_telemetry_csv(database, DAY, out)
    assert list(out.iterdir()) == []  # neither the final file nor the temporary one
    monkeypatch.undo()
    assert nightly.export_telemetry_csv(database, DAY, out)[1] == 3  # the next run exports it properly


# --- the script as a process ---------------------------------------------------------------------------------------------------


def test_a_run_exports_the_csv_triggers_the_pipeline_once_and_records_the_day(workspace):
    result = workspace.run()
    assert result.returncode == 0, result.stderr
    assert (workspace.export_dir / "telemetry_2026-11-20.csv").is_file()
    assert workspace.pipeline_launches() == 1
    (run,) = workspace.runs()
    assert run["status"] == "completed" and run["target_date"] == DAY and run["started_at"] and run["finished_at"] and run["error_message"] is None
    lines = [line for line in result.stderr.splitlines() if "job=nightly_export" in line]
    assert lines and all(re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} (INFO|WARNING|ERROR) job=nightly_export status=\w+ ", line) for line in lines)
    assert any("exported 3 telemetry row(s)" in line for line in lines) and any("status=completed" in line for line in lines)


def test_running_it_twice_for_the_same_day_is_the_same_as_running_it_once(workspace):
    first = workspace.run()
    csv_file = workspace.export_dir / "telemetry_2026-11-20.csv"
    snapshot = (csv_file.read_bytes(), csv_file.stat().st_mtime_ns)
    second = workspace.run()
    assert first.returncode == 0 and second.returncode == 0
    assert "skipped as a duplicate" in second.stderr and "status=skipped" in second.stderr
    assert len(workspace.runs()) == 1 and workspace.runs()[0]["status"] == "completed"
    assert workspace.pipeline_launches() == 1  # the pipeline was not triggered again
    assert (csv_file.read_bytes(), csv_file.stat().st_mtime_ns) == snapshot  # the CSV was not touched
    assert len(list(workspace.export_dir.iterdir())) == 1  # and there is no second file


def test_another_day_is_a_new_run(workspace):
    workspace.run()
    other = workspace.run(TARGET_DATE="2026-11-19")
    assert other.returncode == 0
    assert [r["target_date"] for r in workspace.runs()] == [date(2026, 11, 20), date(2026, 11, 19)] and workspace.pipeline_launches() == 2
    assert sorted(p.name for p in workspace.export_dir.iterdir()) == ["telemetry_2026-11-19.csv", "telemetry_2026-11-20.csv"]


def test_a_failing_pipeline_ends_the_run_as_failed_with_the_message_never_as_processing(workspace):
    result = workspace.run(pipeline_exit=3)
    assert result.returncode == 1
    (run,) = workspace.runs()
    assert run["status"] == "failed" and run["finished_at"] is not None
    assert "exited with code 3" in run["error_message"] and "pipeline lost the database" in run["error_message"]
    assert "ERROR job=nightly_export status=failed" in result.stderr and "exited with code 3" in result.stderr
    assert not job_runner.has_processing_lock(workspace.engine, job_runner.JOB_NIGHTLY_EXPORT)
    assert (workspace.export_dir / "telemetry_2026-11-20.csv").is_file()  # the backup was made before the pipeline failed


def test_a_failed_day_is_retried_by_the_next_run_without_exporting_the_csv_again(workspace):
    workspace.run(pipeline_exit=3)
    csv_file = workspace.export_dir / "telemetry_2026-11-20.csv"
    mtime = csv_file.stat().st_mtime_ns
    retry = workspace.run()
    assert retry.returncode == 0 and "already exists, not exported again" in retry.stderr
    assert [r["status"] for r in workspace.runs()] == ["failed", "completed"] and csv_file.stat().st_mtime_ns == mtime


def test_a_failure_before_the_pipeline_also_ends_as_failed(workspace):
    workspace.export_dir.parent.joinpath("raw").write_text("a file where the export folder should be", encoding="utf-8")
    result = workspace.run()
    assert result.returncode == 1 and [r["status"] for r in workspace.runs()] == ["failed"] and workspace.pipeline_launches() == 0
    assert workspace.runs()[0]["error_message"]


def test_two_instances_started_together_cannot_both_run_the_processing_status_is_the_lock(workspace):
    slow = workspace.start(pipeline_seconds=6)
    deadline = time.time() + 60
    while time.time() < deadline and not job_runner.has_processing_lock(workspace.engine, job_runner.JOB_NIGHTLY_EXPORT):
        time.sleep(0.2)
    assert job_runner.has_processing_lock(workspace.engine, job_runner.JOB_NIGHTLY_EXPORT), "the first instance never reached processing"
    second = workspace.run()  # fires while the first one is still going
    first_out, first_err = slow.communicate(timeout=120)

    assert second.returncode == 0 and "cancelled" in second.stderr and "another run is processing" in second.stderr  # aborted, silently (INFO, no error)
    assert "ERROR" not in second.stderr and "Traceback" not in second.stderr
    assert slow.returncode == 0, first_err
    assert workspace.pipeline_launches() == 1  # only one pipeline process ever started
    (run,) = workspace.runs()  # and only one record: the second instance left nothing behind
    assert run["status"] == "completed"


def test_the_script_is_an_independent_process_that_loads_no_fastapi_code():
    probe = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('s', r'{SCRIPT}')\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "loaded = [n for n in ('fastapi', 'starlette', 'uvicorn', 'services.main', 'services.database') if n in sys.modules]\n"
        "print(','.join(loaded) or 'none')\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    assert result.stdout.strip() == "none", result.stderr


def test_bad_configuration_exits_2_without_touching_the_database(workspace):
    assert workspace.run(TARGET_DATE="not-a-date").returncode == 2
    assert workspace.run(DATABASE_URL="").returncode == 2
    assert workspace.runs() == [] and workspace.pipeline_launches() == 0


# --- the trigger and the pipeline it starts -----------------------------------------------------------------------------------


def test_the_default_pipeline_is_the_milestone_6_entry_point_and_it_reads_the_database_not_the_csv():
    command = nightly.pipeline_command({})
    assert command[0] == sys.executable and Path(command[1]) == REPO_ROOT / "data" / "pipelines" / "pipeline.py" and Path(command[1]).is_file()
    pipeline_source = "\n".join(p.read_text(encoding="utf-8") for p in (REPO_ROOT / "data" / "pipelines").rglob("*.py") if "sales_forecasting" not in p.parts)  # the nightly pipeline only: the sales-forecasting pipeline (a later, separate project) reads its own sales CSV
    assert ".csv" not in pipeline_source.replace("results.csv", "") and "telemetry_20" not in pipeline_source  # the pipeline does not read the export
    assert nightly.pipeline_command({"NIGHTLY_PIPELINE_COMMAND": "echo hello world"}) == ["echo", "hello", "world"]


def test_the_crontab_runs_the_script_every_night_in_utc_without_secrets_or_an_api_call():
    text = (REPO_ROOT / "scripts" / "nightly_export.crontab").read_text(encoding="utf-8")
    entries = [line for line in text.splitlines() if line.strip() and not line.startswith("#") and "=" not in line.split()[0]]
    assert len(entries) == 1
    fields = entries[0].split(None, 5)
    assert fields[:5] == ["0", "1", "*", "*", "*"]  # 01:00 every day
    assert "scripts/nightly_export.py" in fields[5] and "CRON_TZ=UTC" in text
    assert "postgresql" not in text.lower().replace("postgresql+psycopg://...", "") and "curl" not in text and "localhost" not in text
