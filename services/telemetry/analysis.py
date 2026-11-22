"""Operational metrics from the events stored in `telemetry_events`.

This is a technical radar for the engineering team (volume, errors, latency, availability, sign-in health),
not a business report. Every metric is one independent, side-effect-free function that follows the same order:

    load (SQL) -> refine (Pandas) -> convert types -> group -> aggregate -> serve

* load: only the columns and the event types the metric needs, inside the period, filtered in SQL
  (`timestamp >= start AND timestamp < end`, inclusive start, exclusive end, UTC). Never the whole table.
* refine: extract the `tags` dimensions, drop rows whose dimension is null.
* convert types: `pd.to_datetime(..., utc=True)` BEFORE any grouping. Grouping date-looking strings gives wrong
  groups without any error.
* group and aggregate: only Pandas operations (`groupby`, `agg`, `size`, `sum`, `mean`, `quantile`), no loops.
* serve: a list of dicts that serialises to JSON.

The period is resolved once by the caller (`report.py`) and passed in; the functions never apply a default window.
"""

import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlmodel import Session

from ..models import TelemetryEventRecord

TABLE = TelemetryEventRecord.__table__

# A safety net, not a filter: one metric never loads more than this many rows into memory.
MAX_ROWS_PER_METRIC = 200_000

Records = list[dict[str, Any]]


# --- shared steps -------------------------------------------------------------------------------


def _utc(moment: datetime) -> datetime:
    """The period bound as an aware UTC datetime (a naive one is taken as UTC)."""
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _load(session: Session, columns: list[str], start_date: datetime, end_date: datetime, event_types: list[str] | None = None) -> pd.DataFrame:
    """LOAD: the needed columns of the needed event types inside [start_date, end_date), filtered in SQL."""
    statement = select(*[TABLE.c[name] for name in columns]).where(TABLE.c.timestamp >= _utc(start_date), TABLE.c.timestamp < _utc(end_date))
    if event_types:
        statement = statement.where(TABLE.c.event_type.in_(event_types))
    return pd.read_sql_query(statement.limit(MAX_ROWS_PER_METRIC), session.connection())


def _as_dict(value: Any) -> dict[str, Any]:
    """`tags` arrives as a dict from PostgreSQL (jsonb) and as text from SQLite."""
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value) if isinstance(value, (str, bytes)) else None
    except ValueError:
        parsed = None
    return parsed if isinstance(parsed, dict) else {}


def _tag_fields(df: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    """REFINE: the `tags` keys a metric needs as columns (missing keys become null)."""
    expanded = pd.json_normalize(df["tags"].map(_as_dict).tolist()).reindex(columns=names).set_axis(df.index)
    return df.drop(columns="tags").join(expanded)


def _add_time(df: pd.DataFrame) -> pd.DataFrame:
    """CONVERT TYPES: timestamps to timezone-aware datetimes, then the calendar day (UTC) to group by."""
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["date"] = df["timestamp"].dt.date
    return df


def _records(df: pd.DataFrame) -> Records:
    """SERVE: a list of dicts; NaN becomes null so the result is valid JSON."""
    out = df.reset_index(drop=True)
    if "date" in out.columns:
        out["date"] = out["date"].astype(str)  # YYYY-MM-DD, so the result is plain JSON
    out = out.astype(object)
    return out.where(out.notna(), None).to_dict(orient="records")


def _rate(part: pd.Series, whole: pd.Series) -> pd.Series:
    return (part / whole).round(4)


# --- metrics --------------------------------------------------------------------------------------


def events_per_day(session: Session, start_date: datetime, end_date: datetime) -> Records:
    """How much telemetry does the system produce, and of which kind? Event count by day and event type."""
    df = _load(session, ["timestamp", "event_type"], start_date, end_date)
    df = _add_time(df)
    out = df.groupby(["date", "event_type"]).size().rename("count").reset_index()
    return _records(out)


def error_rate_by_day(session: Session, start_date: datetime, end_date: datetime) -> Records:
    """Is the system getting worse? Per day: error and warning events as a share of all events."""
    df = _load(session, ["timestamp", "level"], start_date, end_date)
    df = _add_time(df)
    df["is_error"] = df["level"].eq("error")
    df["is_warning"] = df["level"].eq("warn")
    out = df.groupby("date").agg(events=("level", "size"), errors=("is_error", "sum"), warnings=("is_warning", "sum")).reset_index()
    out["error_rate"] = _rate(out["errors"], out["events"])
    out["warning_rate"] = _rate(out["warnings"], out["events"])
    return _records(out)


def error_rate_by_type(session: Session, start_date: datetime, end_date: datetime) -> Records:
    """Which failures dominate? Each warning or error event type with its count and its share of ALL events."""
    df = _load(session, ["timestamp", "event_type", "level"], start_date, end_date)
    df = _add_time(df)
    total = len(df)
    out = df.groupby(["event_type", "level"]).agg(events=("level", "size"), last_seen=("timestamp", "max")).reset_index()
    out = out[out["level"].isin(["warn", "error"])].copy()
    out["share_of_all_events"] = _rate(out["events"], pd.Series(total, index=out.index))
    out["last_seen"] = out["last_seen"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return _records(out.sort_values("events", ascending=False))


def page_load_latency_by_day(session: Session, start_date: datetime, end_date: datetime) -> Records:
    """How fast do screens load? Time to first byte (and largest paint, when measured) by day and section."""
    df = _load(session, ["timestamp", "tags"], start_date, end_date, ["page_load_recorded"])
    df = _tag_fields(df, ["section", "ttfb_ms", "lcp_ms"])
    df = df.dropna(subset=["section"])
    df[["ttfb_ms", "lcp_ms"]] = df[["ttfb_ms", "lcp_ms"]].apply(pd.to_numeric, errors="coerce")
    df = _add_time(df)
    out = df.groupby(["date", "section"]).agg(
        loads=("ttfb_ms", "size"),
        ttfb_avg_ms=("ttfb_ms", "mean"),
        ttfb_p95_ms=("ttfb_ms", lambda values: values.quantile(0.95)),
        lcp_p75_ms=("lcp_ms", lambda values: values.quantile(0.75)),
    ).reset_index()
    out[["ttfb_avg_ms", "ttfb_p95_ms", "lcp_p75_ms"]] = out[["ttfb_avg_ms", "ttfb_p95_ms", "lcp_p75_ms"]].round(1)
    return _records(out)


def network_failures_by_route(session: Session, start_date: datetime, end_date: datetime) -> Records:
    """Where does the browser lose the API? Failed calls by day, API route and kind (unreachable or timeout)."""
    df = _load(session, ["timestamp", "tags"], start_date, end_date, ["network_request_failed"])
    df = _tag_fields(df, ["route_template", "failure_kind", "duration_ms", "occurrences"])
    df = df.dropna(subset=["route_template", "failure_kind"])
    df[["duration_ms", "occurrences"]] = df[["duration_ms", "occurrences"]].apply(pd.to_numeric, errors="coerce")
    df = _add_time(df)
    out = df.groupby(["date", "route_template", "failure_kind"]).agg(
        failures=("occurrences", "sum"), reports=("occurrences", "size"), avg_duration_ms=("duration_ms", "mean")
    ).reset_index()
    out["avg_duration_ms"] = out["avg_duration_ms"].round(1)
    return _records(out)


def frontend_errors_by_route(session: Session, start_date: datetime, end_date: datetime) -> Records:
    """Which screens crash? Uncaught browser errors by day, route and error class, with distinct defects."""
    df = _load(session, ["timestamp", "tags"], start_date, end_date, ["frontend_error_caught"])
    df = _tag_fields(df, ["route_template", "error_name", "error_fingerprint", "occurrences", "handled_by_boundary"])
    df = df.dropna(subset=["route_template", "error_name"])
    df["occurrences"] = pd.to_numeric(df["occurrences"], errors="coerce")
    df["handled_by_boundary"] = df["handled_by_boundary"].fillna(False).astype(bool)
    df = _add_time(df)
    out = df.groupby(["date", "route_template", "error_name"]).agg(
        occurrences=("occurrences", "sum"), distinct_errors=("error_fingerprint", "nunique"), boundary_hits=("handled_by_boundary", "sum")
    ).reset_index()
    return _records(out)


def auth_failure_rate(session: Session, start_date: datetime, end_date: datetime) -> Records:
    """Can people sign in? Per day: failed sign-ins divided by all sign-in attempts (failed + succeeded)."""
    df = _load(session, ["timestamp", "event_type"], start_date, end_date, ["login_attempt_failed", "login_succeeded"])
    df = _add_time(df)
    df["failed"] = df["event_type"].eq("login_attempt_failed")
    out = df.groupby("date").agg(attempts=("failed", "size"), failures=("failed", "sum")).reset_index()
    out["failure_rate"] = _rate(out["failures"], out["attempts"])
    return _records(out)


# The report serves them under these keys, in this order.
METRICS = {
    "events_per_day": events_per_day,
    "error_rate_by_day": error_rate_by_day,
    "error_rate_by_type": error_rate_by_type,
    "page_load_latency_by_day": page_load_latency_by_day,
    "network_failures_by_route": network_failures_by_route,
    "frontend_errors_by_route": frontend_errors_by_route,
    "auth_failure_rate": auth_failure_rate,
}
