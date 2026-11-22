"""Shared helpers of the reporting tests: a database with the telemetry table, and a fixture of KPI events."""

import os
import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import StaticPool, create_engine, delete, insert, text
from sqlmodel import SQLModel

from data.pipelines.weekly_performance import config
from data.pipelines.weekly_performance.schema import ensure_reporting
from services.models import TelemetryEventRecord

WEEK_1 = date(2026, 11, 9)
WEEK_2 = date(2026, 11, 16)
WEEKS = [WEEK_1, WEEK_2]

LA = {"warehouse": "los_angeles", "client_id": "purestep-footwear"}
ZGZ = {"warehouse": "zaragoza", "client_id": "glowlab-cosmetics"}
DUPLICATED_EVENT_ID = "11111111-1111-4111-8111-111111111111"

# (timestamp, event_type, tags). Edge cases on purpose: Monday 00:00 and Sunday 23:59:59 UTC, the same event stored twice,
# a loss exit (not a dispatch), an unknown warehouse, a discrepancy with no dispatch, and an event that is not a KPI.
EVENTS = [
    ("2026-11-09T00:00:00Z", "inbound_order_created", {**LA, "quantity": 100}),
    ("2026-11-10T10:00:00Z", "inbound_order_created", {**LA, "quantity": 50}),
    ("2026-11-10T11:00:00Z", "outbound_order_created", {**LA, "exit_type": "dispatch", "quantity": 4}),
    ("2026-11-11T11:00:00Z", "outbound_order_created", {**LA, "exit_type": "dispatch", "quantity": 6, "_dup": True}),
    ("2026-11-11T11:00:01Z", "outbound_order_created", {**LA, "exit_type": "dispatch", "quantity": 6, "_dup": True}),
    ("2026-11-12T09:00:00Z", "outbound_order_created", {**LA, "exit_type": "loss", "quantity": 2}),
    ("2026-11-12T12:00:00Z", "stock_threshold_triggered", {**LA, "severity": "below_minimum", "quantity": 5}),
    ("2026-11-13T12:00:00Z", "inventory_discrepancy_detected", {**LA, "quantity": 3}),
    ("2026-11-13T12:30:00Z", "inbound_order_created", {**ZGZ, "quantity": 40}),
    ("2026-11-15T23:59:59Z", "inbound_order_created", {**ZGZ, "quantity": 10}),
    ("2026-11-14T08:00:00Z", "inbound_order_created", {"warehouse": "madrid", "client_id": "x", "quantity": 9}),
    ("2026-11-16T00:00:00Z", "outbound_order_created", {**ZGZ, "exit_type": "dispatch", "quantity": 1}),
    ("2026-11-17T10:00:00Z", "inventory_discrepancy_detected", {**ZGZ, "quantity": 2}),
    ("2026-11-18T10:00:00Z", "inventory_discrepancy_detected", {**LA, "quantity": 1}),
    ("2026-11-18T10:00:00Z", "page_viewed", {"section": "overview"}),
]

# What the fixture must produce: (warehouse, client_id, week) -> (inbound, outbound, stockout, discrepancies, rate).
EXPECTED = {
    ("los_angeles", "purestep-footwear", WEEK_1): (150, 2, 1, 1, 0.5),
    ("zaragoza", "glowlab-cosmetics", WEEK_1): (50, 0, 0, 0, 0.0),
    ("zaragoza", "glowlab-cosmetics", WEEK_2): (0, 1, 0, 1, 1.0),
    ("los_angeles", "purestep-footwear", WEEK_2): (0, 0, 0, 1, 0.0),
}


def utc(text_: str) -> datetime:
    return datetime.fromisoformat(text_.replace("Z", "+00:00"))


def add_event(engine, when: str, event_type: str, tags: dict, event_id: str | None = None, value=None) -> None:
    """Inserts one telemetry row like `telemetry_store.to_row` does: tags plus the `_envelope` ids, `value` = quantity."""
    tags = dict(tags)
    duplicated = tags.pop("_dup", False)
    envelope = {"eventId": DUPLICATED_EVENT_ID if duplicated else (event_id or str(uuid.uuid4())), "schemaVersion": "1.2.0"}
    with engine.begin() as connection:
        connection.execute(
            insert(TelemetryEventRecord.__table__).values(
                id=uuid.uuid4(), timestamp=utc(when), service="backoffice", event_type=event_type, level="info",
                value=tags.get("quantity") if value is None else value, tags={**tags, "_envelope": envelope},
            )
        )


def seed_events(engine, events=EVENTS) -> None:
    for when, event_type, tags in events:
        add_event(engine, when, event_type, tags)


def make_engine(kind: str):
    """A fresh database with the real `telemetry_events` table. `postgres` needs TEST_POSTGRES_URL (skipped without it)."""
    if kind == "sqlite":
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        return engine
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is not set")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS reporting CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS telemetry_events CASCADE"))
    SQLModel.metadata.create_all(engine)
    return engine


def table_rows(engine):
    """The destination table as {(warehouse, client_id, week): (inbound, outbound, stockout, discrepancies, rate, computed_at)}."""
    from data.pipelines.weekly_performance.schema import weekly_performance as table

    ensure_reporting(engine)
    with engine.connect() as connection:
        rows = connection.execute(table.select()).mappings().all()
    return {
        (r["warehouse"], r["client_id"], r["week_start"]): (
            r["inbound_units_count"], r["outbound_orders_count"], r["stockout_events_count"], r["discrepancy_events_count"],
            float(r["discrepancy_rate"]), r["computed_at"],
        )
        for r in rows
    }


def kpis(rows):
    return {key: value[:5] for key, value in rows.items()}


def clear_reporting(engine) -> None:
    from data.pipelines.weekly_performance.schema import pipeline_runs, weekly_performance

    ensure_reporting(engine)
    with engine.begin() as connection:
        connection.execute(delete(weekly_performance))
        connection.execute(delete(pipeline_runs))


__all__ = ["config"]
