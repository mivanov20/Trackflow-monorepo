"""The operational pipeline (services/telemetry/analysis.py), its cache and GET /telemetry/report."""

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import event as sa_event
from sqlmodel import Session

from services.models import TelemetryEventRecord
from services.telemetry import analysis, report as report_module
from services.telemetry.analysis import METRICS
from services.telemetry.cache import TTLCache

START = datetime(2026, 11, 18, tzinfo=timezone.utc)
END = datetime(2026, 11, 21, tzinfo=timezone.utc)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


# (timestamp, event_type, level, tags). Boundaries: the first event is exactly at START (included); the last two
# are one second before START and exactly at END (both outside the period).
DATASET = [
    ("2026-11-17T23:59:59Z", "page_viewed", "info", {}),
    ("2026-11-18T00:00:00Z", "page_viewed", "info", {}),
    ("2026-11-18T10:00:00Z", "page_viewed", "info", {}),
    ("2026-11-18T11:00:00Z", "inbound_order_created", "info", {"warehouse": "zaragoza", "quantity": 40}),
    ("2026-11-18T12:00:00Z", "login_attempt_failed", "warn", {"failure_reason": "invalid_credentials", "attempt_number": 1}),
    ("2026-11-18T12:01:00Z", "login_succeeded", "info", {"user_role": "user"}),
    ("2026-11-19T01:00:00Z", "page_viewed", "info", {}),
    ("2026-11-19T02:00:00Z", "frontend_error_caught", "error", {"route_template": "/login", "error_name": "TypeError", "error_fingerprint": "aaaaaaaaaaaaaaaa", "occurrences": 3, "handled_by_boundary": False}),
    ("2026-11-19T03:00:00Z", "network_request_failed", "warn", {"route_template": "/inventory/products", "failure_kind": "unreachable", "duration_ms": 100, "occurrences": 1}),
    ("2026-11-19T03:05:00Z", "network_request_failed", "warn", {"route_template": "/inventory/products", "failure_kind": "unreachable", "duration_ms": 300, "occurrences": 2}),
    ("2026-11-19T04:00:00Z", "page_load_recorded", "info", {"section": "inventory_products", "ttfb_ms": 100, "lcp_ms": 1000}),
    ("2026-11-19T04:01:00Z", "page_load_recorded", "info", {"section": "inventory_products", "ttfb_ms": 200, "lcp_ms": None}),
    ("2026-11-19T04:02:00Z", "page_load_recorded", "info", {"section": "inventory_products", "ttfb_ms": 300, "lcp_ms": 2000}),
    ("2026-11-19T05:00:00Z", "login_succeeded", "info", {"user_role": "user"}),
    ("2026-11-19T05:01:00Z", "login_succeeded", "info", {"user_role": "user"}),
    ("2026-11-19T06:00:00Z", "login_attempt_failed", "warn", {"failure_reason": "invalid_credentials", "attempt_number": 1}),
    ("2026-11-19T06:01:00Z", "login_attempt_failed", "warn", {"failure_reason": "network_error", "attempt_number": 2}),
    ("2026-11-20T09:00:00Z", "page_viewed", "info", {}),
    ("2026-11-20T09:01:00Z", "login_succeeded", "info", {"user_role": "user"}),
    ("2026-11-20T23:59:59Z", "frontend_error_caught", "error", {"route_template": "/login", "error_name": "TypeError", "error_fingerprint": "bbbbbbbbbbbbbbbb", "occurrences": 1, "handled_by_boundary": True}),
    ("2026-11-21T00:00:00Z", "page_viewed", "info", {}),
]


@pytest.fixture
def session(incident_engine):
    with Session(incident_engine) as db:
        db.add_all(
            TelemetryEventRecord(timestamp=utc(ts), service="backoffice", event_type=event_type, level=level, tags=tags)
            for ts, event_type, level, tags in DATASET
        )
        db.commit()
        yield db


def by(rows, **match):
    return [row for row in rows if all(row[key] == value for key, value in match.items())]


# --- the metrics ---------------------------------------------------------------------------------


def test_events_per_day_counts_by_day_and_type_with_start_included_and_end_excluded(session):
    rows = analysis.events_per_day(session, START, END)
    assert sum(row["count"] for row in rows) == 19  # 21 events, minus the one before START and the one at END
    assert by(rows, date="2026-11-18", event_type="page_viewed")[0]["count"] == 2
    assert by(rows, date="2026-11-19", event_type="login_succeeded")[0]["count"] == 2
    assert by(rows, date="2026-11-21") == []
    assert {row["date"] for row in rows} == {"2026-11-18", "2026-11-19", "2026-11-20"}


def test_error_rate_by_day(session):
    rows = {row["date"]: row for row in analysis.error_rate_by_day(session, START, END)}
    assert rows["2026-11-18"] == {"date": "2026-11-18", "events": 5, "errors": 0, "warnings": 1, "error_rate": 0.0, "warning_rate": 0.2}
    assert rows["2026-11-19"]["events"] == 11 and rows["2026-11-19"]["errors"] == 1 and rows["2026-11-19"]["warnings"] == 4
    assert rows["2026-11-19"]["error_rate"] == pytest.approx(0.0909, abs=1e-4)
    assert rows["2026-11-20"]["error_rate"] == pytest.approx(0.3333, abs=1e-4)


def test_error_rate_by_type_lists_only_warning_and_error_types_with_their_share_of_all_events(session):
    rows = analysis.error_rate_by_type(session, START, END)
    assert [row["event_type"] for row in rows][0] == "login_attempt_failed"
    assert {row["event_type"] for row in rows} == {"login_attempt_failed", "frontend_error_caught", "network_request_failed"}
    failed = by(rows, event_type="login_attempt_failed")[0]
    assert failed == {"event_type": "login_attempt_failed", "level": "warn", "events": 3, "last_seen": "2026-11-19T06:01:00Z", "share_of_all_events": pytest.approx(0.1579, abs=1e-4)}
    assert by(rows, event_type="frontend_error_caught")[0]["level"] == "error"
    assert by(rows, event_type="frontend_error_caught")[0]["last_seen"] == "2026-11-20T23:59:59Z"


def test_page_load_latency_by_day_and_section(session):
    (row,) = analysis.page_load_latency_by_day(session, START, END)
    assert row == {"date": "2026-11-19", "section": "inventory_products", "loads": 3, "ttfb_avg_ms": 200.0, "ttfb_p95_ms": 290.0, "lcp_p75_ms": 1750.0}


def test_network_failures_by_route(session):
    (row,) = analysis.network_failures_by_route(session, START, END)
    assert row == {"date": "2026-11-19", "route_template": "/inventory/products", "failure_kind": "unreachable", "failures": 3, "reports": 2, "avg_duration_ms": 200.0}


def test_frontend_errors_by_route(session):
    rows = analysis.frontend_errors_by_route(session, START, END)
    assert by(rows, date="2026-11-19") == [{"date": "2026-11-19", "route_template": "/login", "error_name": "TypeError", "occurrences": 3, "distinct_errors": 1, "boundary_hits": 0}]
    assert by(rows, date="2026-11-20")[0]["boundary_hits"] == 1


def test_auth_failure_rate_is_failed_over_all_attempts_per_day(session):
    rows = {row["date"]: row for row in analysis.auth_failure_rate(session, START, END)}
    assert rows["2026-11-18"] == {"date": "2026-11-18", "attempts": 2, "failures": 1, "failure_rate": 0.5}
    assert rows["2026-11-19"] == {"date": "2026-11-19", "attempts": 4, "failures": 2, "failure_rate": 0.5}
    assert rows["2026-11-20"] == {"date": "2026-11-20", "attempts": 1, "failures": 0, "failure_rate": 0.0}


@pytest.mark.parametrize("name", list(METRICS))
def test_an_empty_period_gives_an_empty_json_list_not_an_error(session, name):
    empty_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert METRICS[name](session, empty_start, empty_start + timedelta(days=1)) == []


@pytest.mark.parametrize("name", list(METRICS))
def test_every_metric_returns_plain_json_and_is_repeatable(session, name):
    first = METRICS[name](session, START, END)
    assert isinstance(first, list) and first and all(isinstance(row, dict) for row in first)
    assert json.loads(json.dumps(first)) == first  # no NaN, numpy scalar or date object left
    assert METRICS[name](session, START, END) == first  # side-effect free: same input, same output


def test_every_metric_has_a_grouping_dimension(session):
    for name, metric in METRICS.items():
        for row in metric(session, START, END):
            assert {"date", "event_type"} & set(row), f"{name} is a global number without context"


# --- the formula: load (SQL) -> refine -> convert types -> group -> aggregate ---------------------


def test_timestamps_are_converted_to_utc_datetimes_before_grouping(monkeypatch):
    """Strings with offsets that look like the same day group into different days unless converted first."""

    def fake_load(_session, columns, _start, _end, event_types=None):
        return pd.DataFrame({"timestamp": ["2026-11-20T23:30:00-05:00", "2026-11-21T00:30:00+01:00", "2026-11-20T10:00:00Z"], "event_type": ["page_viewed"] * 3})

    monkeypatch.setattr(analysis, "_load", fake_load)
    rows = {row["date"]: row["count"] for row in analysis.events_per_day(None, START, END)}
    # 23:30 at -05:00 is 04:30 UTC the next day; 00:30 at +01:00 is 23:30 UTC the day before.
    assert rows == {"2026-11-21": 1, "2026-11-20": 2}


def test_the_filters_run_in_sql_and_only_needed_columns_are_loaded(session):
    statements = []

    @sa_event.listens_for(session.get_bind(), "before_cursor_execute")
    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(" ".join(statement.split()))

    analysis.events_per_day(session, START, END)
    analysis.page_load_latency_by_day(session, START, END)
    analysis.auth_failure_rate(session, START, END)
    volume, latency, auth = statements
    for statement in (volume, latency, auth):
        assert "telemetry_events.timestamp >=" in statement and "telemetry_events.timestamp <" in statement
        assert "LIMIT" in statement
        assert "SELECT *" not in statement
    assert "telemetry_events.tags" not in volume  # volume does not need the JSON
    assert "telemetry_events.event_type IN" in latency and "telemetry_events.event_type IN" in auth  # IN (...) in SQL, not in Pandas


def test_metric_functions_use_no_loops():
    tree = ast.parse(Path(analysis.__file__).read_text(encoding="utf-8"))
    names = {metric.__name__ for metric in METRICS.values()}
    forbidden = (ast.For, ast.While, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            assert not [n for n in ast.walk(node) if isinstance(n, forbidden)], f"{node.name} loops"


def test_the_timestamp_is_converted_with_utc_true():
    source = Path(analysis.__file__).read_text(encoding="utf-8")
    assert 'pd.to_datetime(df["timestamp"], utc=True)' in source


# --- the cache -------------------------------------------------------------------------------------


def test_ttl_cache_serves_within_the_ttl_and_recalculates_after_it():
    now = [0.0]
    cache = TTLCache(ttl_seconds=60, clock=lambda: now[0])
    calls = []

    def compute():
        calls.append(1)
        return len(calls)

    assert cache.get_or_compute("k", compute) == (1, False)
    now[0] = 59.9
    assert cache.get_or_compute("k", compute) == (1, True)
    assert cache.get_or_compute("other", compute) == (2, False)
    now[0] = 60.0
    assert cache.get_or_compute("k", compute) == (3, False)


def test_ttl_cache_is_bounded():
    cache = TTLCache(ttl_seconds=60, max_entries=2, clock=lambda: 0.0)
    for key in "abc":
        cache.get_or_compute(key, lambda: key)
    assert len(cache._entries) == 2


# --- the period --------------------------------------------------------------------------------------


def test_default_period_is_the_last_seven_days_in_utc():
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    start, end = report_module.resolve_period(None, None, now=now)
    assert (start, end) == (now - timedelta(days=7), now)


def test_period_rules():
    now = datetime(2026, 11, 22, tzinfo=timezone.utc)
    assert report_module.resolve_period(None, datetime(2026, 11, 10), now=now)[0] == datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert report_module.resolve_period(datetime(2026, 11, 20), None, now=now)[1] == now
    with pytest.raises(report_module.InvalidPeriod):
        report_module.resolve_period(datetime(2026, 11, 20), datetime(2026, 11, 20))
    with pytest.raises(report_module.InvalidPeriod):
        report_module.resolve_period(datetime(2026, 1, 1), datetime(2026, 11, 1))


# --- the endpoint --------------------------------------------------------------------------------------


@pytest.fixture
def api(incident_client, incident_engine, monkeypatch):
    """A signed-in client over the seeded database, with a fresh cache and a counter of pipeline runs."""
    with Session(incident_engine) as db:
        db.add_all(TelemetryEventRecord(timestamp=utc(ts), service="backoffice", event_type=t, level=lvl, tags=tags) for ts, t, lvl, tags in DATASET)
        db.commit()
    runs = []
    monkeypatch.setattr(report_module, "REPORT_CACHE", TTLCache(clock=lambda: clock[0]))
    clock = [0.0]
    original = report_module.build_report

    def counting(session, start, end):
        runs.append((start, end))
        return original(session, start, end)

    monkeypatch.setattr(report_module, "build_report", counting)
    incident_client.runs, incident_client.clock = runs, clock
    return incident_client


PERIOD = {"start_date": "2026-11-18T00:00:00Z", "end_date": "2026-11-21T00:00:00Z"}


def test_report_has_the_period_and_metrics_structure(api):
    response = api.get("/telemetry/report", params=PERIOD)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"period", "metrics"}
    assert body["period"] == {"from": "2026-11-18T00:00:00Z", "to": "2026-11-21T00:00:00Z"}
    assert list(body["metrics"]) == list(METRICS)
    assert sum(row["count"] for row in body["metrics"]["events_per_day"]) == 19
    assert body["metrics"]["auth_failure_rate"][0]["failure_rate"] == 0.5


def test_report_requires_a_signed_in_user(api):
    api.headers.pop("Authorization")
    assert api.get("/telemetry/report").status_code == 401


def test_report_defaults_to_the_last_seven_days(api):
    before = datetime.now(timezone.utc)
    body = api.get("/telemetry/report").json()
    start, end = (utc(body["period"]["from"]), utc(body["period"]["to"]))
    assert abs((end - before).total_seconds()) < 5
    assert end - start - timedelta(days=7) < timedelta(seconds=1)


def test_second_identical_request_is_served_from_the_cache_without_recalculating(api):
    first = api.get("/telemetry/report", params=PERIOD)
    second = api.get("/telemetry/report", params=PERIOD)
    assert (first.headers["X-Cache"], second.headers["X-Cache"]) == ("MISS", "HIT")
    assert second.json() == first.json()
    assert len(api.runs) == 1


def test_default_requests_share_one_cache_entry_and_other_periods_do_not(api):
    api.get("/telemetry/report")
    assert api.get("/telemetry/report").headers["X-Cache"] == "HIT"
    assert api.get("/telemetry/report", params=PERIOD).headers["X-Cache"] == "MISS"
    assert api.get("/telemetry/report", params={**PERIOD, "end_date": "2026-11-20T00:00:00Z"}).headers["X-Cache"] == "MISS"
    assert len(api.runs) == 3


def test_the_cache_expires_after_sixty_seconds(api):
    api.get("/telemetry/report", params=PERIOD)
    api.clock[0] = 59
    assert api.get("/telemetry/report", params=PERIOD).headers["X-Cache"] == "HIT"
    api.clock[0] = 61
    assert api.get("/telemetry/report", params=PERIOD).headers["X-Cache"] == "MISS"
    assert len(api.runs) == 2


def test_the_period_is_resolved_once_and_passed_to_every_metric(api, monkeypatch):
    seen = []
    for name, metric in list(METRICS.items()):
        monkeypatch.setitem(METRICS, name, lambda session, start, end, _m=metric, _n=name: (seen.append((_n, start, end)), _m(session, start, end))[1])
    api.get("/telemetry/report", params=PERIOD)
    assert len(seen) == len(METRICS) and len({(start, end) for _, start, end in seen}) == 1


def test_naive_dates_are_taken_as_utc_and_offsets_are_converted(api):
    body = api.get("/telemetry/report", params={"start_date": "2026-11-18T01:00:00+01:00", "end_date": "2026-11-21"}).json()
    assert body["period"] == {"from": "2026-11-18T00:00:00Z", "to": "2026-11-21T00:00:00Z"}


@pytest.mark.parametrize(
    "params",
    [
        {"start_date": "2026-11-21T00:00:00Z", "end_date": "2026-11-18T00:00:00Z"},
        {"start_date": "2026-11-18T00:00:00Z", "end_date": "2026-11-18T00:00:00Z"},
        {"start_date": "2026-01-01T00:00:00Z", "end_date": "2026-11-01T00:00:00Z"},
        {"start_date": "yesterday"},
        {"end_date": "not-a-date"},
    ],
)
def test_a_bad_period_is_a_422_and_never_reaches_the_pipeline(api, params):
    assert api.get("/telemetry/report", params=params).status_code == 422
    assert api.runs == []
