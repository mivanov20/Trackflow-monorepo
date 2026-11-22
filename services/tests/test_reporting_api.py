"""services/reporting: the three endpoints of the weekly performance pipeline."""

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from prefect.testing.utilities import prefect_test_harness
from reporting_fixtures import EXPECTED, WEEK_1, WEEK_2, WEEKS, seed_events
from sqlalchemy import insert

from data.pipelines import pipeline
from data.pipelines.weekly_performance import config, extract, load, runs, transform, window
from data.pipelines.weekly_performance.schema import ensure_reporting, pipeline_runs
from services.accounts import Role, User
from services.auth import get_current_user
from services.main import app
from services.models import TelemetryEventRecord
from services.tasks import run_weekly_performance_pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]
KPI_FIELDS = {"warehouse", "client_id", "inbound_units_count", "outbound_orders_count", "stockout_events_count", "discrepancy_events_count", "discrepancy_rate"}


@pytest.fixture(scope="module", autouse=True)
def prefect_server():
    with prefect_test_harness():
        yield


@pytest.fixture
def api(incident_client, incident_engine):
    """A signed-in operative over an in-memory database that already has the telemetry table and the reporting schema."""
    ensure_reporting(incident_engine)
    with config.use_engine(incident_engine):
        yield incident_client


@pytest.fixture
def computed(api, incident_engine):
    """The fixture events extracted, transformed and loaded for the two weeks (what a finished run leaves)."""
    seed_events(incident_engine)
    start, end = window.window_bounds(WEEKS)
    result = transform.weekly_rollup(extract.extract_events(incident_engine, start, end), WEEKS)
    for week in WEEKS:
        load.load_week(incident_engine, week, result["rollups"][week])
    return api


@pytest.fixture
def as_manager(api):
    """The same account with the manager role, which is allowed to start the pipeline."""
    me = api.get("/auth/me").json()
    manager = User(id=me["id"], email=me["email"], hashed_password="not-used", role=Role.manager, created_at=datetime.now(timezone.utc))
    app.dependency_overrides[get_current_user] = lambda: manager
    yield api
    app.dependency_overrides.pop(get_current_user, None)


# --- GET /reporting/weekly-warehouse-client-performance ----------------------------------------------------------------


def test_kpi_endpoint_returns_the_contract_of_the_company_context(computed):
    response = computed.get("/reporting/weekly-warehouse-client-performance")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"week_start", "entries"}
    assert body["week_start"] == WEEK_2.isoformat()  # the most recent computed week
    assert all(set(entry) == KPI_FIELDS for entry in body["entries"])
    assert {(e["warehouse"], e["client_id"]) for e in body["entries"]} == {("zaragoza", "glowlab-cosmetics"), ("los_angeles", "purestep-footwear")}


def test_kpi_endpoint_for_an_explicit_week_matches_the_computed_values(computed):
    body = computed.get("/reporting/weekly-warehouse-client-performance", params={"week_start": WEEK_1.isoformat()}).json()
    assert body["week_start"] == WEEK_1.isoformat()
    values = {(e["warehouse"], e["client_id"]): (e["inbound_units_count"], e["outbound_orders_count"], e["stockout_events_count"], e["discrepancy_events_count"], e["discrepancy_rate"]) for e in body["entries"]}
    assert values == {(w, c): v for (w, c, week), v in EXPECTED.items() if week == WEEK_1}
    assert isinstance(body["entries"][0]["discrepancy_rate"], float)


def test_kpi_endpoint_errors(computed, api):
    assert computed.get("/reporting/weekly-warehouse-client-performance", params={"week_start": "2026-11-10"}).status_code == 422  # not a Monday
    assert computed.get("/reporting/weekly-warehouse-client-performance", params={"week_start": "not-a-date"}).status_code == 422
    empty_week = computed.get("/reporting/weekly-warehouse-client-performance", params={"week_start": "2026-10-05"})
    assert empty_week.status_code == 200 and empty_week.json() == {"week_start": "2026-10-05", "entries": []}


def test_kpi_endpoint_is_404_before_any_week_is_computed(api):
    assert api.get("/reporting/weekly-warehouse-client-performance").status_code == 404


# --- GET /reporting/pipeline-runs/latest -------------------------------------------------------------------------------


def test_latest_run_is_404_before_the_first_run_then_returns_its_metadata(api, incident_engine):
    assert api.get("/reporting/pipeline-runs/latest").status_code == 404
    run_id = runs.open_run(incident_engine, trigger="schedule", triggered_by=None, window_start=WEEK_1, window_end=WEEK_2)
    runs.close_run(incident_engine, run_id, status="completed", counters={"records_extracted": 14, "rows_inserted": 4})
    body = api.get("/reporting/pipeline-runs/latest").json()
    assert body["run_id"] == str(run_id) and body["status"] == "completed" and body["trigger"] == "schedule"
    assert body["records_extracted"] == 14 and body["rows_inserted"] == 4
    assert body["started_at"].endswith("Z") and body["finished_at"].endswith("Z")
    assert (body["window_start"], body["window_end"]) == (WEEK_1.isoformat(), WEEK_2.isoformat())
    assert body["error_stage"] is None


# --- POST /reporting/pipeline-runs ---------------------------------------------------------------------------------------


def test_trigger_needs_authentication_and_the_manager_or_admin_role(api):
    assert api.post("/reporting/pipeline-runs").status_code == 403  # an operative with the plain `user` role
    api.headers.pop("Authorization")
    assert api.post("/reporting/pipeline-runs").status_code == 401
    assert api.get("/reporting/pipeline-runs/latest").status_code == 401
    assert api.get("/reporting/weekly-warehouse-client-performance").status_code == 401


class FakeTask:
    """What `.delay` returns."""

    id = "00000000-0000-4000-8000-000000000001"


def enqueue_stub(monkeypatch, execute=False):
    """Replaces `.delay`. With execute=True the worker's job is done on the spot, so the test can read the result."""
    calls = []

    def delay(run_id, week_start=None, weeks_back=2, triggered_by=None):
        calls.append((run_id, week_start, weeks_back, triggered_by))
        if execute:
            from datetime import date as _date

            pipeline.run_manual_flow(run_id, _date.fromisoformat(week_start) if week_start else None, weeks_back, triggered_by)
        return FakeTask()

    monkeypatch.setattr(run_weekly_performance_pipeline, "delay", delay)
    return calls


def test_trigger_enqueues_the_task_and_the_worker_job_runs_the_flow_of_data_pipelines(as_manager, incident_engine, monkeypatch, tmp_path):
    monkeypatch.setenv(config.SNAPSHOT_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(transform, "TRANSFORM_VERSION", f"api-{time.time_ns()}")
    monkeypatch.setattr(pipeline.window, "weeks_for_run", lambda week_start=None, weeks_back=2, now=None: [week_start] if week_start else WEEKS)
    calls = enqueue_stub(monkeypatch, execute=True)  # the worker's job is done before answering, so the test can read the result
    seed_events(incident_engine)
    response = as_manager.post("/reporting/pipeline-runs", json={"weeks_back": 2})
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert response.json()["task_id"] == FakeTask.id and calls[0][0] == run_id
    latest = as_manager.get("/reporting/pipeline-runs/latest").json()
    assert latest["run_id"] == run_id and latest["status"] == "completed" and latest["trigger"] == "manual"
    assert latest["triggered_by"] and latest["rows_inserted"] == 4 and latest["records_extracted"] == 14
    kpi = as_manager.get("/reporting/weekly-warehouse-client-performance", params={"week_start": WEEK_1.isoformat()}).json()
    assert len(kpi["entries"]) == 2


def test_trigger_refuses_a_second_run_and_validates_the_week_first(as_manager, incident_engine, monkeypatch):
    enqueue_stub(monkeypatch)  # the run is opened and nobody finishes it
    assert as_manager.post("/reporting/pipeline-runs", json={}).status_code == 202
    assert as_manager.post("/reporting/pipeline-runs", json={}).status_code == 409
    # A request that is wrong in itself is refused as such (422) whether or not a run is in progress.
    assert as_manager.post("/reporting/pipeline-runs", json={"week_start": "2026-11-10"}).status_code == 422
    assert as_manager.post("/reporting/pipeline-runs", json={"weeks_back": 99}).status_code == 422
    with incident_engine.begin() as connection:
        connection.execute(pipeline_runs.delete())
    assert as_manager.post("/reporting/pipeline-runs", json={"week_start": "2026-11-09"}).status_code == 202


def test_the_response_to_a_trigger_is_immediate_and_names_the_run(as_manager, monkeypatch):
    enqueue_stub(monkeypatch)
    body = as_manager.post("/reporting/pipeline-runs").json()
    assert set(body) == {"task_id", "run_id", "status"} and body["status"] == "pending"
    assert as_manager.get("/reporting/pipeline-runs/latest").json()["run_id"] == body["run_id"]


# --- the module keeps its promises -----------------------------------------------------------------------------------------


def test_the_endpoints_import_the_pipeline_and_hold_no_etl_logic():
    source = (REPO_ROOT / "services" / "reporting" / "router.py").read_text(encoding="utf-8")
    assert "from data.pipelines import pipeline" in source and "from data.pipelines.weekly_performance import" in source
    for call in ("pipeline.open_manual_run", "run_weekly_performance_pipeline.delay", "runs.get_latest_run", "queries.get_weekly_performance"):
        assert call in source
    for etl in ("import pandas", "groupby", "insert(", "update(", "delete(", "select(", "telemetry_events"):
        assert etl not in source, etl


def test_the_technical_report_files_are_untouched_by_name():
    for path in ("services/telemetry/analysis.py", "services/telemetry/report.py", "services/telemetry/cache.py"):
        assert (REPO_ROOT / path).is_file()
    assert "reporting" not in (REPO_ROOT / "services" / "telemetry" / "analysis.py").read_text(encoding="utf-8")


def test_routes_are_registered_under_reporting():
    # FastAPI wraps included routers lazily, so the OpenAPI document is the stable way to list what is served.
    served = {(path, method.upper()) for path, item in app.openapi()["paths"].items() if path.startswith("/reporting") for method in item}
    assert served == {
        ("/reporting/weekly-warehouse-client-performance", "GET"),
        ("/reporting/pipeline-runs/latest", "GET"),
        ("/reporting/pipeline-runs", "POST"),
    }
