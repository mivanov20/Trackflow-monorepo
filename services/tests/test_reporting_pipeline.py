"""Weekly Warehouse & Client Performance pipeline: extract, transform, load, run log and the Prefect flow.

Runs on SQLite always, and also on PostgreSQL when TEST_POSTGRES_URL is set (the database of the real deployment).
"""

import ast
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from prefect.testing.utilities import prefect_test_harness
from reporting_fixtures import EXPECTED, EVENTS, WEEK_1, WEEK_2, WEEKS, add_event, clear_reporting, kpis, make_engine, seed_events, table_rows, utc
from sqlalchemy import insert, select, update
from sqlalchemy.exc import OperationalError

from data.pipelines import pipeline
from data.pipelines.weekly_performance import config, extract, load, runs, schema, snapshot, transform, window
from data.pipelines.weekly_performance.schema import pipeline_runs, weekly_performance

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module", autouse=True)
def prefect_server():
    """A temporary Prefect backend, so the flow tests do not touch any real one."""
    with prefect_test_harness():
        yield


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request):
    database = make_engine(request.param)
    with config.use_engine(database):
        yield database
    database.dispose()


@pytest.fixture
def seeded(engine):
    seed_events(engine)
    return engine


@pytest.fixture
def two_weeks(monkeypatch):
    """The flow normally derives its weeks from today's date; the tests pin them to the fixture's two weeks."""
    monkeypatch.setattr(pipeline.window, "weeks_for_run", lambda week_start=None, weeks_back=2, now=None: [week_start] if week_start else WEEKS)


@pytest.fixture
def fresh_cache(monkeypatch):
    """The transform task caches for an hour across runs of the whole test session: give each test its own cache keys."""
    monkeypatch.setattr(transform, "TRANSFORM_VERSION", f"test-{time.time_ns()}")


def latest_run(engine):
    return runs.get_latest_run(engine)


# --- weeks ---------------------------------------------------------------------------------------------------------


def test_default_weeks_are_the_last_two_complete_weeks():
    now = datetime(2026, 11, 23, 4, 0, tzinfo=timezone.utc)  # Monday morning: the week of the 16th just ended
    assert window.weeks_for_run(now=now) == [date(2026, 11, 9), date(2026, 11, 16)]
    assert window.weeks_for_run(weeks_back=1, now=datetime(2026, 11, 22, 23, 59, tzinfo=timezone.utc)) == [date(2026, 11, 9)]  # Sunday: the current week is not complete


def test_an_explicit_week_must_be_a_monday_and_bounds_are_utc():
    assert window.weeks_for_run(date(2026, 11, 9)) == [date(2026, 11, 9)]
    with pytest.raises(ValueError):
        window.weeks_for_run(date(2026, 11, 10))
    with pytest.raises(ValueError):
        window.weeks_for_run(weeks_back=0)
    start, end = window.window_bounds(WEEKS)
    assert (start, end) == (datetime(2026, 11, 9, tzinfo=timezone.utc), datetime(2026, 11, 23, tzinfo=timezone.utc))


# --- extract -------------------------------------------------------------------------------------------------------


def test_extract_reads_only_the_four_event_types_inside_the_window_with_select_only(seeded):
    statements = []

    from sqlalchemy import event as sa_event

    @sa_event.listens_for(seeded, "before_cursor_execute")
    def capture(_c, _cur, statement, _p, _ctx, _many):
        statements.append(" ".join(statement.split()))

    start, end = window.window_bounds([WEEK_1])
    raw = extract.extract_events(seeded, start, end)
    assert set(raw["event_type"]) == {"inbound_order_created", "outbound_order_created", "stock_threshold_triggered", "inventory_discrepancy_detected"}
    assert len(raw) == 11  # week 1 only; the page view and the week-2 events are outside
    assert all(s.upper().startswith("SELECT") for s in statements) and len(statements) == 1
    assert "telemetry_events.timestamp >=" in statements[0] and "telemetry_events.timestamp <" in statements[0] and "event_type IN" in statements[0]


def test_extract_window_is_inclusive_at_the_start_and_exclusive_at_the_end(engine):
    add_event(engine, "2026-11-09T00:00:00Z", "inbound_order_created", {"warehouse": "zaragoza", "client_id": "a", "quantity": 1})
    add_event(engine, "2026-11-08T23:59:59Z", "inbound_order_created", {"warehouse": "zaragoza", "client_id": "a", "quantity": 1})
    add_event(engine, "2026-11-16T00:00:00Z", "inbound_order_created", {"warehouse": "zaragoza", "client_id": "a", "quantity": 1})
    start, end = window.window_bounds([WEEK_1])
    raw = extract.extract_events(engine, start, end)
    assert len(raw) == 1
    assert extract.newest_timestamp(raw) == datetime(2026, 11, 9, tzinfo=timezone.utc)


# --- transform -----------------------------------------------------------------------------------------------------


def extracted(engine):
    start, end = window.window_bounds(WEEKS)
    return extract.extract_events(engine, start, end)


def rollup_values(result):
    rows = pd.concat(result["rollups"].values())
    return {(r.warehouse, r.client_id, r.week_start): (r.inbound_units_count, r.outbound_orders_count, r.stockout_events_count, r.discrepancy_events_count, float(r.discrepancy_rate)) for r in rows.itertuples()}


def test_transform_computes_the_kpis_of_the_company_context(seeded):
    result = transform.weekly_rollup(extracted(seeded), WEEKS)
    assert rollup_values(result) == EXPECTED
    assert result["counters"] == {"records_extracted": 14, "duplicates_dropped": 1, "records_discarded": 1, "value_mismatches": 0}


def test_transform_rules_dispatch_only_dedupe_boundaries_and_rate_zero(seeded):
    values = rollup_values(transform.weekly_rollup(extracted(seeded), WEEKS))
    assert values[("los_angeles", "purestep-footwear", WEEK_1)][1] == 2  # 4 and 6 units orders; the loss and the duplicate are not counted
    assert values[("zaragoza", "glowlab-cosmetics", WEEK_1)][0] == 50  # Sunday 23:59:59 UTC is still week 1
    assert values[("zaragoza", "glowlab-cosmetics", WEEK_2)][1] == 1  # Monday 00:00:00 UTC is week 2
    assert values[("los_angeles", "purestep-footwear", WEEK_2)][4] == 0.0  # a discrepancy and no dispatch: rate 0 by definition
    literal = rollup_values(transform.weekly_rollup(extracted(seeded), WEEKS, count_loss_exits=True))
    assert literal[("los_angeles", "purestep-footwear", WEEK_1)][1] == 3  # the CONTEXT's literal wording counts the loss too


def test_a_row_never_mixes_clients_and_every_requested_week_has_a_frame(seeded):
    result = transform.weekly_rollup(extracted(seeded), WEEKS + [date(2026, 11, 23)])
    assert list(result["rollups"]) == WEEKS + [date(2026, 11, 23)]
    assert result["rollups"][date(2026, 11, 23)].empty
    for frame in result["rollups"].values():
        assert not frame.duplicated(subset=["warehouse", "client_id", "week_start"]).any()


def test_transform_fails_before_loading_when_values_and_quantities_disagree():
    raw = pd.DataFrame({
        "timestamp": [utc("2026-11-10T10:00:00Z")] * 4, "event_type": ["inbound_order_created"] * 4, "value": [1.0, 2.0, 3.0, 4.0],
        "tags": [{"warehouse": "zaragoza", "client_id": "a", "quantity": 9, "_envelope": {"eventId": str(i)}} for i in range(4)],
    })
    with pytest.raises(transform.DataQualityError):
        transform.weekly_rollup(raw, [WEEK_1])


def test_transform_uses_no_loops_over_rows():
    source = (REPO_ROOT / "data" / "pipelines" / "weekly_performance" / "transform.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.For, ast.While))]
    for forbidden in ("iterrows", "itertuples", ".apply("):
        assert forbidden not in source
    assert 'pd.to_datetime(df["timestamp"], utc=True' in source  # converted to UTC datetimes before any grouping


# --- load: idempotent, atomic per week ------------------------------------------------------------------------------


def load_all(engine, raw=None):
    result = transform.weekly_rollup(raw if raw is not None else extracted(engine), WEEKS)
    totals = {"rows_inserted": 0, "rows_updated": 0, "rows_unchanged": 0, "rows_deleted": 0}
    for week in WEEKS:
        for name, amount in load.load_week(engine, week, result["rollups"][week]).items():
            totals[name] += amount
    return totals


def test_load_writes_the_rows_of_the_context_table(seeded):
    assert load_all(seeded) == {"rows_inserted": 4, "rows_updated": 0, "rows_unchanged": 0, "rows_deleted": 0}
    assert kpis(table_rows(seeded)) == EXPECTED


def test_running_twice_leaves_the_table_identical_computed_at_included(seeded):
    load_all(seeded)
    first = table_rows(seeded)
    assert load_all(seeded) == {"rows_inserted": 0, "rows_updated": 0, "rows_unchanged": 4, "rows_deleted": 0}
    assert table_rows(seeded) == first


def test_a_late_event_changes_only_the_row_it_belongs_to(seeded):
    load_all(seeded)
    before = table_rows(seeded)
    add_event(seeded, "2026-11-11T08:00:00Z", "inbound_order_created", {"warehouse": "los_angeles", "client_id": "purestep-footwear", "quantity": 25})
    assert load_all(seeded) == {"rows_inserted": 0, "rows_updated": 1, "rows_unchanged": 3, "rows_deleted": 0}
    after = table_rows(seeded)
    changed = {key for key in after if after[key] != before[key]}
    assert changed == {("los_angeles", "purestep-footwear", WEEK_1)}
    assert after[("los_angeles", "purestep-footwear", WEEK_1)][0] == 175


def test_a_key_the_recompute_no_longer_produces_is_deleted(seeded):
    load_all(seeded)
    with seeded.begin() as connection:
        connection.execute(insert(weekly_performance).values(warehouse="zaragoza", client_id="ghost-client", week_start=WEEK_1, computed_at=datetime.now(timezone.utc)))
    assert load_all(seeded)["rows_deleted"] == 1
    assert ("zaragoza", "ghost-client", WEEK_1) not in table_rows(seeded)


def test_an_empty_recompute_of_a_week_with_rows_is_refused_and_keeps_the_rows(seeded):
    load_all(seeded)
    before = table_rows(seeded)
    empty = transform.weekly_rollup(extracted(seeded).iloc[0:0], WEEKS)["rollups"][WEEK_1]
    with pytest.raises(load.EmptyExtractGuard):
        load.load_week(seeded, WEEK_1, empty)
    assert table_rows(seeded) == before


def test_a_failure_inside_the_week_transaction_rolls_the_whole_week_back(seeded, monkeypatch):
    load_all(seeded)
    before = table_rows(seeded)
    add_event(seeded, "2026-11-11T08:00:00Z", "inbound_order_created", {"warehouse": "los_angeles", "client_id": "purestep-footwear", "quantity": 25})
    with seeded.begin() as connection:
        connection.execute(insert(weekly_performance).values(warehouse="zaragoza", client_id="ghost-client", week_start=WEEK_1, computed_at=datetime.now(timezone.utc)))
    before = table_rows(seeded)

    def boom(*_a, **_k):
        raise ConnectionError("the connection dropped before the stale delete")

    monkeypatch.setattr(load, "delete", boom)  # the upsert has already run when this raises
    with pytest.raises(ConnectionError):
        load.load_week(seeded, WEEK_1, transform.weekly_rollup(extracted(seeded), WEEKS)["rollups"][WEEK_1])
    assert table_rows(seeded) == before  # the upsert was undone: never half a week
    monkeypatch.undo()
    load_all(seeded)
    assert table_rows(seeded)[("los_angeles", "purestep-footwear", WEEK_1)][0] == 175  # the rerun repairs it


def test_a_week_with_no_events_is_a_valid_empty_load(engine):
    """No activity at all (an empty extract, an empty stored week) must complete, not fail on empty frames."""
    start, end = window.window_bounds(WEEKS)
    raw = extract.extract_events(engine, start, end)
    assert raw.empty
    result = transform.weekly_rollup(raw, WEEKS)
    assert result["counters"] == {"records_extracted": 0, "duplicates_dropped": 0, "records_discarded": 0, "value_mismatches": 0}
    for week in WEEKS:
        assert result["rollups"][week].empty
        assert load.load_week(engine, week, result["rollups"][week]) == {"rows_inserted": 0, "rows_updated": 0, "rows_unchanged": 0, "rows_deleted": 0}
    assert table_rows(engine) == {}


def test_the_destination_refuses_an_unknown_warehouse(engine):
    schema.ensure_reporting(engine)
    with pytest.raises(Exception):
        with engine.begin() as connection:
            connection.execute(insert(weekly_performance).values(warehouse="madrid", client_id="x", week_start=WEEK_1, computed_at=datetime.now(timezone.utc)))


# --- run log -------------------------------------------------------------------------------------------------------


def test_only_one_run_can_be_running_and_an_abandoned_one_is_recovered(engine):
    first = runs.open_run(engine, trigger="schedule", triggered_by=None, window_start=WEEK_1, window_end=WEEK_2)
    with pytest.raises(runs.RunAlreadyRunning):
        runs.open_run(engine, trigger="manual", triggered_by=None, window_start=WEEK_1, window_end=WEEK_2)
    with engine.begin() as connection:  # the first run's worker died 40 minutes ago
        connection.execute(update(pipeline_runs).where(pipeline_runs.c.run_id == first).values(started_at=datetime.now(timezone.utc) - timedelta(minutes=40)))
    second = runs.open_run(engine, trigger="manual", triggered_by=None, window_start=WEEK_1, window_end=WEEK_2)
    with engine.connect() as connection:
        old = connection.execute(select(pipeline_runs).where(pipeline_runs.c.run_id == first)).mappings().one()
    assert (old["status"], old["error_stage"], old["error_type"]) == ("failed", "abandoned", "abandoned")
    assert second != first


def test_close_run_records_status_counters_and_a_sanitised_error(engine):
    run_id = runs.open_run(engine, trigger="schedule", triggered_by=None, window_start=WEEK_1, window_end=WEEK_2)
    error = OperationalError("select 1", {}, Exception("could not connect to postgresql://user:secret@db.example.com:5432/postgres\nmore detail"))
    runs.close_run(engine, run_id, status="failed", counters={"records_extracted": 12, "rows_inserted": 3}, error_stage="load", error=error)
    latest = latest_run(engine)
    assert latest["status"] == "failed" and latest["error_stage"] == "load" and latest["error_type"] == "OperationalError"
    assert "secret" not in latest["error_message"] and "postgresql://" not in latest["error_message"]
    assert latest["records_extracted"] == 12 and latest["rows_inserted"] == 3 and latest["finished_at"] and latest["started_at"]


# --- the flow ------------------------------------------------------------------------------------------------------


def run_flow(**kwargs):
    return pipeline.weekly_warehouse_client_performance_flow(trigger="manual", **kwargs)


def test_the_flow_runs_extract_transform_load_and_records_the_run(seeded, two_weeks, fresh_cache, tmp_path, monkeypatch):
    monkeypatch.setenv(config.SNAPSHOT_DIR_ENV, str(tmp_path))
    summary = run_flow()
    assert summary["status"] == "completed" and summary["rows_inserted"] == 4 and summary["duplicates_dropped"] == 1
    assert kpis(table_rows(seeded)) == EXPECTED
    run = latest_run(seeded)
    assert run["status"] == "completed" and run["trigger"] == "manual"
    assert run["started_at"] and run["finished_at"] and run["records_extracted"] == 14 and run["rows_inserted"] == 4
    assert run["error_stage"] is None and run["error_message"] is None and run["optional_steps_failed"] == 0
    assert (run["window_start"], run["window_end"]) == (WEEK_1.isoformat(), WEEK_2.isoformat())
    assert run["source_max_timestamp"] == "2026-11-18T10:00:00Z"
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"weekly_warehouse_client_performance_{w.isoformat()}.json" for w in WEEKS]


def test_running_the_flow_twice_gives_the_same_table(seeded, two_weeks, fresh_cache, tmp_path, monkeypatch):
    monkeypatch.setenv(config.SNAPSHOT_DIR_ENV, str(tmp_path))
    run_flow()
    first = table_rows(seeded)
    clear_runs = latest_run(seeded)
    second_summary = run_flow()
    assert table_rows(seeded) == first  # identical, computed_at included
    assert (second_summary["rows_inserted"], second_summary["rows_updated"], second_summary["rows_unchanged"]) == (0, 0, 4)
    assert latest_run(seeded)["run_id"] != clear_runs["run_id"] and latest_run(seeded)["status"] == "completed"


def test_the_transform_is_not_repeated_within_the_hour_for_the_same_events(seeded, two_weeks, fresh_cache, tmp_path, monkeypatch):
    monkeypatch.setenv(config.SNAPSHOT_DIR_ENV, str(tmp_path))
    calls = []
    original = transform.prepare_events
    monkeypatch.setattr(transform, "prepare_events", lambda *a, **k: (calls.append(1), original(*a, **k))[1])
    run_flow()
    run_flow()
    assert len(calls) == 1  # the second run reused the cached result
    add_event(seeded, "2026-11-11T08:00:00Z", "inbound_order_created", {"warehouse": "los_angeles", "client_id": "purestep-footwear", "quantity": 25})
    run_flow()
    assert len(calls) == 2  # one new event changes the key: recomputed
    assert table_rows(seeded)[("los_angeles", "purestep-footwear", WEEK_1)][0] == 175


def test_a_flow_over_weeks_with_no_events_completes_with_nothing_loaded(engine, two_weeks, fresh_cache, tmp_path, monkeypatch):
    monkeypatch.setenv(config.SNAPSHOT_DIR_ENV, str(tmp_path))
    summary = run_flow()
    assert summary["status"] == "completed" and summary["records_extracted"] == 0 and summary["rows_inserted"] == 0
    assert latest_run(engine)["status"] == "completed" and latest_run(engine)["source_max_timestamp"] is None


def test_a_failing_optional_step_does_not_stop_the_flow(seeded, two_weeks, fresh_cache, monkeypatch):
    def broken(*_a, **_k):
        raise OSError("the snapshot directory is read-only")

    monkeypatch.setattr(snapshot, "write_snapshot", broken)
    summary = run_flow()
    assert summary["status"] == "completed" and summary["optional_steps_failed"] == 1
    assert kpis(table_rows(seeded)) == EXPECTED  # the KPIs were loaded anyway
    assert latest_run(seeded)["status"] == "completed" and latest_run(seeded)["optional_steps_failed"] == 1


def test_a_transient_extract_error_is_retried_then_recorded_and_a_deterministic_one_is_not(seeded, two_weeks, fresh_cache, monkeypatch):
    attempts = []

    def flaky(*_a, **_k):
        attempts.append(1)
        raise OperationalError("select", {}, Exception("connection lost"))

    monkeypatch.setattr(extract, "extract_events", flaky)
    monkeypatch.setattr(pipeline, "extract_telemetry_events", pipeline.extract_telemetry_events.with_options(retries=2, retry_delay_seconds=0))
    with pytest.raises(pipeline.PipelineStageFailed):
        run_flow()
    assert len(attempts) == 3  # the first try and two retries
    run = latest_run(seeded)
    assert (run["status"], run["error_stage"], run["error_type"]) == ("failed", "extract", "OperationalError")
    assert table_rows(seeded) == {}  # nothing was written

    attempts.clear()
    monkeypatch.setattr(extract, "extract_events", lambda *a, **k: (attempts.append(1), (_ for _ in ()).throw(ValueError("bad window")))[1])
    with pytest.raises(pipeline.PipelineStageFailed):
        run_flow()
    assert len(attempts) == 1  # a ValueError would fail again: not retried


def test_a_data_quality_failure_stops_before_the_load_and_is_recorded(engine, two_weeks, fresh_cache):
    for day in range(10, 14):  # four movements whose stored value disagrees with their quantity
        add_event(engine, f"2026-11-{day}T10:00:00Z", "inbound_order_created", {"warehouse": "zaragoza", "client_id": "a", "quantity": 5}, value=999)
    with pytest.raises(pipeline.PipelineStageFailed):
        run_flow()
    run = latest_run(engine)
    assert (run["status"], run["error_stage"], run["error_type"]) == ("failed", "transform", "DataQualityError")
    assert table_rows(engine) == {}


def test_one_week_failing_to_load_does_not_stop_the_other_and_the_rerun_repairs_it(seeded, two_weeks, fresh_cache, tmp_path, monkeypatch):
    monkeypatch.setenv(config.SNAPSHOT_DIR_ENV, str(tmp_path))
    real_load = load.load_week

    def fail_second_week(engine, week, rollup, now=None):
        if week == WEEK_2:
            raise ConnectionError("the connection dropped")
        return real_load(engine, week, rollup, now)

    monkeypatch.setattr(load, "load_week", fail_second_week)
    monkeypatch.setattr(pipeline, "load_weekly_rollup", pipeline.load_weekly_rollup.with_options(retries=1, retry_delay_seconds=0))
    with pytest.raises(pipeline.PartialLoadFailure):
        run_flow()
    stored = kpis(table_rows(seeded))
    assert {key for key in stored} == {k for k in EXPECTED if k[2] == WEEK_1}  # week 1 committed, week 2 absent: never half a week
    run = latest_run(seeded)
    assert (run["status"], run["error_stage"], run["error_type"]) == ("failed", "load", "PartialLoadFailure")
    assert run["rows_inserted"] == 2 and WEEK_2.isoformat() in run["error_message"]

    monkeypatch.setattr(load, "load_week", real_load)  # the connection is back: the rerun
    summary = run_flow()
    assert summary["status"] == "completed" and summary["rows_unchanged"] == 2 and summary["rows_inserted"] == 2
    assert kpis(table_rows(seeded)) == EXPECTED


def test_a_second_run_cannot_start_while_one_is_running(seeded, two_weeks, fresh_cache):
    runs.open_run(seeded, trigger="schedule", triggered_by=None, window_start=WEEK_1, window_end=WEEK_2)
    with pytest.raises(runs.RunAlreadyRunning):
        run_flow()


def test_the_flow_has_the_subflows_tasks_retries_cache_and_optional_steps_of_the_design():
    assert pipeline.weekly_warehouse_client_performance_flow.name == "weekly_warehouse_client_performance_flow"
    subflows = {f.name for f in (
        pipeline.extract_weekly_performance_events_flow, pipeline.transform_weekly_warehouse_client_kpis_flow,
        pipeline.load_weekly_warehouse_client_performance_flow, pipeline.check_telemetry_silence_flow, pipeline.publish_weekly_performance_snapshot_flow,
    )}
    assert subflows == {
        "extract_weekly_performance_events_flow", "transform_weekly_warehouse_client_kpis_flow", "load_weekly_warehouse_client_performance_flow",
        "check_telemetry_silence_flow", "publish_weekly_performance_snapshot_flow",
    }
    for task in (pipeline.open_run, pipeline.extract_telemetry_events, pipeline.load_weekly_rollup, pipeline.close_run):
        assert task.retries > 0 and task.retry_delay_seconds
    assert pipeline.prepare_telemetry_events.cache_key_fn is pipeline.transform_cache_key
    assert pipeline.prepare_telemetry_events.cache_expiration == timedelta(hours=1)
    source = (REPO_ROOT / "data" / "pipelines" / "pipeline.py").read_text(encoding="utf-8")
    assert "publish_weekly_performance_snapshot_flow(weeks, return_state=True)" in source and "check_telemetry_silence_flow(" in source
    assert 'if __name__ == "__main__":' in source


def test_each_subflow_runs_on_its_own_with_explicit_inputs_and_outputs(seeded, fresh_cache, tmp_path, monkeypatch):
    monkeypatch.setenv(config.SNAPSHOT_DIR_ENV, str(tmp_path))
    start, end = window.window_bounds(WEEKS)
    extracted_ = pipeline.extract_weekly_performance_events_flow(start, end)
    assert set(extracted_) == {"raw", "newest"} and len(extracted_["raw"]) == 14

    transformed = pipeline.transform_weekly_warehouse_client_kpis_flow(extracted_["raw"], WEEKS)
    assert set(transformed) == {"rollups", "counters"} and rollup_values(transformed) == EXPECTED

    loaded = pipeline.load_weekly_warehouse_client_performance_flow(transformed["rollups"], WEEKS)
    assert loaded == {"totals": {"rows_inserted": 4, "rows_updated": 0, "rows_unchanged": 0, "rows_deleted": 0}, "failed_weeks": []}
    assert kpis(table_rows(seeded)) == EXPECTED

    assert len(pipeline.publish_weekly_performance_snapshot_flow(WEEKS)) == 2


def test_the_transform_subflow_touches_no_database(fresh_cache, monkeypatch):
    """Given events and weeks it returns the KPIs; with no engine configured at all it still works."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    raw = pd.DataFrame({
        "timestamp": [utc("2026-11-10T10:00:00Z")], "event_type": ["inbound_order_created"], "value": [7.0],
        "tags": [{"warehouse": "zaragoza", "client_id": "glowlab-cosmetics", "quantity": 7, "_envelope": {"eventId": "e1"}}],
    })
    result = pipeline.transform_weekly_warehouse_client_kpis_flow(raw, [WEEK_1])
    assert result["rollups"][WEEK_1]["inbound_units_count"].tolist() == [7]


def test_a_silent_source_is_reported_by_the_optional_check_without_stopping_the_run(engine, two_weeks, fresh_cache, tmp_path, monkeypatch):
    monkeypatch.setenv(config.SNAPSHOT_DIR_ENV, str(tmp_path))
    with pytest.raises(pipeline.TelemetrySilence):
        pipeline.check_telemetry_silence_flow(0)
    pipeline.check_telemetry_silence_flow(3)  # events arrived: no complaint
    summary = run_flow()  # no events at all in the database
    assert summary["status"] == "completed" and summary["optional_steps_failed"] == 1
    assert latest_run(engine)["optional_steps_failed"] == 1


# --- the command line ------------------------------------------------------------------------------------------------


def test_the_command_line_runs_the_whole_pipeline_and_reports_failures(seeded, two_weeks, fresh_cache, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(config.SNAPSHOT_DIR_ENV, str(tmp_path))
    assert pipeline.main(["--trigger", "backfill"]) == 0
    assert "\"status\": \"completed\"" in capsys.readouterr().out
    assert kpis(table_rows(seeded)) == EXPECTED and latest_run(seeded)["trigger"] == "backfill"
    clear_reporting(seeded)
    assert pipeline.main(["--week-start", "2026-11-09"]) == 0
    assert {k[2] for k in table_rows(seeded)} == {WEEK_1}


def test_the_command_line_rejects_a_week_that_is_not_a_monday(capsys):
    assert pipeline.main(["--week-start", "2026-11-10"]) == 1
    output = capsys.readouterr().out
    assert '"status": "failed"' in output and "Monday" in output


# --- what the pipeline must never do ---------------------------------------------------------------------------------


def test_the_pipeline_never_writes_to_the_source_or_touches_the_technical_report():
    root = REPO_ROOT / "data" / "pipelines"
    code = "\n".join(p.read_text(encoding="utf-8") for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    for forbidden in ("insert(telemetry_source", "update(telemetry_source", "delete(telemetry_source", "INSERT INTO telemetry_events", "UPDATE telemetry_events", "DELETE FROM telemetry_events"):
        assert forbidden not in code
    assert "services.telemetry" not in code and "services/telemetry" not in code.replace("services/telemetry/analysis.py", "")


# --- the code and the design document agree ----------------------------------------------------------------------------


def test_the_code_matches_the_design_document():
    design = (REPO_ROOT / "data" / "pipelines" / "PIPELINE_DESIGN.md").read_text(encoding="utf-8")
    log_section = design.split("### 3.3 Execution log")[1].split("Example of a completed row")[0]
    documented = set(re.findall(r"^\| `(\w+)` \|", log_section, re.M))
    assert documented == {column.name for column in pipeline_runs.columns}, "every column of the run log is documented, and no more"
    assert [c.name for c in weekly_performance.columns] == [
        "id", "warehouse", "client_id", "week_start", "inbound_units_count", "outbound_orders_count",
        "stockout_events_count", "discrepancy_events_count", "discrepancy_rate", "computed_at",
    ]  # the destination table of the company CONTEXT, column for column
    assert weekly_performance.schema == "reporting" and pipeline_runs.schema == "reporting"
    for name in (
        "weekly_warehouse_client_performance_flow", "extract_weekly_performance_events_flow", "transform_weekly_warehouse_client_kpis_flow",
        "load_weekly_warehouse_client_performance_flow", "check_telemetry_silence_flow", "publish_weekly_performance_snapshot_flow",
        "open_run", "extract_telemetry_events", "prepare_telemetry_events", "compute_inbound_volume", "compute_outbound_throughput",
        "compute_stockout_frequency", "compute_discrepancy_rate", "load_weekly_rollup", "close_run", "write_report_snapshot",
    ):
        assert name in design
    assert config.EVENT_TYPES == ["inbound_order_created", "outbound_order_created", "stock_threshold_triggered", "inventory_discrepancy_detected"]
    sql = (REPO_ROOT / "data" / "pipelines" / "weekly_performance" / "reporting_schema.sql").read_text(encoding="utf-8")
    assert "unique (warehouse, client_id, week_start)" in sql and "create schema if not exists reporting" in sql
