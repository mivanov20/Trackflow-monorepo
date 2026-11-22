"""Unit tests of the transformation tasks of the Weekly Warehouse & Client Performance pipeline.

They run in isolation: in-memory events shaped like the `telemetry_events` rows of the company CONTEXT, the tasks called
through `.fn` (the plain function behind each Prefect task), no database, no Prefect backend, no network.

    python -m pytest tests/pipelines/test_pipeline.py

Names follow the KPIs of the CONTEXT: Inbound Volume, Outbound Throughput, Stockout Frequency, Discrepancy Rate.
"""

import ast
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from data.pipelines import pipeline
from data.pipelines.weekly_performance import transform
from data.pipelines.weekly_performance.transform import DataQualityError

ROOT = Path(__file__).resolve().parents[2]
WEEK = date(2026, 7, 13)  # the Monday of the example in the CONTEXT
NEXT_WEEK = date(2026, 7, 20)
LA = {"warehouse": "los_angeles", "client_id": "fashion-co"}
ZGZ = {"warehouse": "zaragoza", "client_id": "electro-brand"}
KEY = ["warehouse", "client_id", "week_start"]


def event(when: str, event_type: str, event_id: str, tags: dict, value=None) -> dict:
    """One `telemetry_events` row as the extract stage returns it: timestamp, event_type, value and the tags JSON."""
    quantity = tags.get("quantity")
    return {
        "timestamp": datetime.fromisoformat(when.replace("Z", "+00:00")),
        "event_type": event_type,
        "value": quantity if value is None else value,
        "tags": {**tags, "_envelope": {"eventId": event_id, "schemaVersion": "1.2.0"}},
    }


def frame(*events: dict) -> pd.DataFrame:
    return pd.DataFrame(list(events), columns=["timestamp", "event_type", "value", "tags"])


def prepared(*events: dict, weeks=(WEEK,)) -> pd.DataFrame:
    return pipeline.prepare_telemetry_events.fn(frame(*events), list(weeks))["events"]


def cell(result: pd.DataFrame, warehouse: str, client_id: str, column: str, week: date = WEEK):
    row = result[(result["warehouse"] == warehouse) & (result["client_id"] == client_id) & (result["week_start"] == week)]
    assert len(row) == 1, f"expected one row for {warehouse}/{client_id}/{week}, found {len(row)}"
    return row.iloc[0][column]


# --- Inbound Volume --------------------------------------------------------------------------------------------------


def test_compute_inbound_volume_sums_the_units_received_per_warehouse_client_and_week():
    events = prepared(
        event("2026-07-13T08:00:00Z", "inbound_order_created", "a1", {**LA, "quantity": 4000}),
        event("2026-07-15T08:00:00Z", "inbound_order_created", "a2", {**LA, "quantity": 200}),
        event("2026-07-16T08:00:00Z", "inbound_order_created", "a3", {**ZGZ, "quantity": 35}),
        event("2026-07-14T08:00:00Z", "outbound_order_created", "a4", {**LA, "exit_type": "dispatch", "quantity": 999}),  # not a receipt
    )
    result = pipeline.compute_inbound_volume.fn(events)
    assert cell(result, "los_angeles", "fashion-co", "inbound_units_count") == 4200  # the value of the CONTEXT example
    assert cell(result, "zaragoza", "electro-brand", "inbound_units_count") == 35
    assert len(result) == 2


def test_compute_inbound_volume_never_adds_two_clients_of_the_same_warehouse_together():
    events = prepared(
        event("2026-07-13T08:00:00Z", "inbound_order_created", "b1", {"warehouse": "los_angeles", "client_id": "fashion-co", "quantity": 10}),
        event("2026-07-13T09:00:00Z", "inbound_order_created", "b2", {"warehouse": "los_angeles", "client_id": "beauty-lab", "quantity": 5}),
    )
    result = pipeline.compute_inbound_volume.fn(events)
    assert cell(result, "los_angeles", "fashion-co", "inbound_units_count") == 10
    assert cell(result, "los_angeles", "beauty-lab", "inbound_units_count") == 5


# --- Outbound Throughput ---------------------------------------------------------------------------------------------


def test_compute_outbound_throughput_counts_orders_picked_and_dispatched_not_write_offs():
    events = prepared(
        event("2026-07-13T08:00:00Z", "outbound_order_created", "c1", {**LA, "exit_type": "dispatch", "quantity": 3}),
        event("2026-07-14T08:00:00Z", "outbound_order_created", "c2", {**LA, "exit_type": "dispatch", "quantity": 12}),
        event("2026-07-14T09:00:00Z", "outbound_order_created", "c3", {**LA, "exit_type": "loss", "quantity": 1}),  # a write-off
    )
    result = pipeline.compute_outbound_throughput.fn(events)
    assert cell(result, "los_angeles", "fashion-co", "outbound_orders_count") == 2  # orders, not units: 3 + 12 would be 15
    literal = pipeline.compute_outbound_throughput.fn(events, count_loss_exits=True)
    assert cell(literal, "los_angeles", "fashion-co", "outbound_orders_count") == 3  # the CONTEXT's literal wording, one switch away


# --- Stockout Frequency ----------------------------------------------------------------------------------------------


def test_compute_stockout_frequency_counts_each_time_a_sku_fell_below_its_minimum():
    events = prepared(
        event("2026-07-13T08:00:00Z", "stock_threshold_triggered", "d1", {**LA, "severity": "below_minimum", "quantity": 5}),
        event("2026-07-15T08:00:00Z", "stock_threshold_triggered", "d2", {**LA, "severity": "out_of_stock", "quantity": 0}),
        event("2026-07-15T09:00:00Z", "stock_threshold_triggered", "d3", {**ZGZ, "severity": "below_minimum", "quantity": 4}),
        event("2026-07-15T10:00:00Z", "inbound_order_created", "d4", {**LA, "quantity": 50}),
    )
    result = pipeline.compute_stockout_frequency.fn(events)
    assert cell(result, "los_angeles", "fashion-co", "stockout_events_count") == 2
    assert cell(result, "zaragoza", "electro-brand", "stockout_events_count") == 1


# --- Discrepancy Rate: the definition of the CONTEXT, with hand-calculated numbers ----------------------------------------


def test_compute_discrepancy_rate_is_discrepancy_events_over_outbound_orders_for_a_known_week():
    """Los Angeles / fashion-co: 8 dispatches and 2 discrepancies, so 2 / 8 = 0.25. Zaragoza / electro-brand: 4 dispatches
    and 0 discrepancies, so 0. Zaragoza / beauty-lab: 1 discrepancy and no dispatch at all, so 0 ("0 if no orders that week")."""
    dispatches = [event(f"2026-07-1{day}T08:00:00Z", "outbound_order_created", f"e{day}{n}", {**LA, "exit_type": "dispatch", "quantity": 1}) for day in (3, 4, 5, 6) for n in (1, 2)]
    more = [event(f"2026-07-1{day}T10:00:00Z", "outbound_order_created", f"f{day}", {**ZGZ, "exit_type": "dispatch", "quantity": 2}) for day in (3, 4, 5, 6)]
    discrepancies = [
        event("2026-07-14T12:00:00Z", "inventory_discrepancy_detected", "g1", {**LA, "quantity": 3}),
        event("2026-07-16T12:00:00Z", "inventory_discrepancy_detected", "g2", {**LA, "quantity": 1}),
        event("2026-07-15T12:00:00Z", "inventory_discrepancy_detected", "g3", {"warehouse": "zaragoza", "client_id": "beauty-lab", "quantity": 6}),
    ]
    events = prepared(*dispatches, *more, *discrepancies)
    outbound = pipeline.compute_outbound_throughput.fn(events)
    rate = pipeline.compute_discrepancy_rate.fn(outbound, pipeline.count_discrepancy_events.fn(events))

    assert cell(outbound, "los_angeles", "fashion-co", "outbound_orders_count") == 8
    assert cell(rate, "los_angeles", "fashion-co", "discrepancy_events_count") == 2
    assert cell(rate, "los_angeles", "fashion-co", "discrepancy_rate") == pytest.approx(0.25)
    assert cell(rate, "zaragoza", "electro-brand", "discrepancy_rate") == 0
    assert cell(rate, "zaragoza", "beauty-lab", "discrepancy_events_count") == 1
    assert cell(rate, "zaragoza", "beauty-lab", "discrepancy_rate") == 0  # no division by zero, no NaN, no infinity


def test_compute_discrepancy_rate_of_a_week_with_no_activity_is_an_empty_result():
    empty = prepared()
    outbound = pipeline.compute_outbound_throughput.fn(empty)
    assert pipeline.compute_discrepancy_rate.fn(outbound, pipeline.count_discrepancy_events.fn(empty)).empty


# --- defensive behaviour against invalid input -------------------------------------------------------------------------


def test_prepare_telemetry_events_discards_malformed_events_and_counts_them():
    good = event("2026-07-13T08:00:00Z", "inbound_order_created", "h1", {**LA, "quantity": 10})
    malformed = [
        event("2026-07-13T08:01:00Z", "inbound_order_created", "h2", {"warehouse": None, "client_id": "fashion-co", "quantity": 5}),  # no warehouse
        event("2026-07-13T08:02:00Z", "inbound_order_created", "h3", {"warehouse": "madrid", "client_id": "fashion-co", "quantity": 5}),  # unknown warehouse
        event("2026-07-13T08:03:00Z", "inbound_order_created", "h4", {"warehouse": "zaragoza", "client_id": None, "quantity": 5}),  # no client
        event("2026-07-13T08:04:00Z", "inbound_order_created", "h5", {**LA, "quantity": "plenty"}, value=None),  # quantity is not a number
        event("2026-07-13T08:05:00Z", "inbound_order_created", "h6", {**LA, "quantity": -3}, value=None),  # negative units received
        event("2026-07-13T08:06:00Z", "page_viewed", "h7", {**LA, "quantity": 1}),  # not one of the four KPI events
        {"timestamp": None, "event_type": "inbound_order_created", "value": 1, "tags": {**LA, "quantity": 1}},  # no time
        {"timestamp": datetime(2026, 7, 13, 9), "event_type": "inbound_order_created", "value": 1, "tags": "{not json"},  # tags is not JSON
        {"timestamp": datetime(2026, 7, 13, 9), "event_type": "inbound_order_created", "value": 1, "tags": None},  # tags is missing
    ]
    result = pipeline.prepare_telemetry_events.fn(frame(good, *malformed), [WEEK])
    assert result["counters"]["records_discarded"] == len(malformed)
    assert result["counters"]["records_extracted"] == 1 + len(malformed)
    assert len(result["events"]) == 1
    # Nothing malformed reached a KPI: only the one good receipt of 10 units counts.
    inbound = pipeline.compute_inbound_volume.fn(result["events"])
    assert inbound["inbound_units_count"].tolist() == [10]


def test_prepare_telemetry_events_refuses_a_frame_that_lacks_the_columns_it_needs():
    with pytest.raises(DataQualityError, match="tags"):
        pipeline.prepare_telemetry_events.fn(pd.DataFrame({"timestamp": [datetime(2026, 7, 13)], "event_type": ["inbound_order_created"]}), [WEEK])


def test_prepare_telemetry_events_fails_when_the_stored_value_contradicts_the_quantity():
    events = [event(f"2026-07-1{day}T08:00:00Z", "inbound_order_created", f"i{day}", {**LA, "quantity": 5}, value=999) for day in (3, 4, 5, 6)]
    with pytest.raises(DataQualityError):
        pipeline.prepare_telemetry_events.fn(frame(*events), [WEEK])


def test_prepare_telemetry_events_counts_an_event_stored_twice_only_once():
    twice = [event("2026-07-13T08:00:00Z", "inbound_order_created", "same-id", {**LA, "quantity": 10})] * 2
    result = pipeline.prepare_telemetry_events.fn(frame(*twice), [WEEK])
    assert result["counters"]["duplicates_dropped"] == 1
    assert pipeline.compute_inbound_volume.fn(result["events"])["inbound_units_count"].tolist() == [10]


def test_the_kpi_tasks_accept_an_empty_events_frame_and_return_empty_results():
    empty = prepared()
    for task in (pipeline.compute_inbound_volume, pipeline.compute_outbound_throughput, pipeline.compute_stockout_frequency, pipeline.count_discrepancy_events):
        assert task.fn(empty).empty


# --- the week an event belongs to ----------------------------------------------------------------------------------------


def test_events_belong_to_the_utc_week_that_starts_on_monday_at_midnight():
    events = prepared(
        event("2026-07-13T00:00:00Z", "inbound_order_created", "j1", {**LA, "quantity": 1}),  # Monday 00:00:00 UTC: this week
        event("2026-07-19T23:59:59Z", "inbound_order_created", "j2", {**LA, "quantity": 2}),  # Sunday 23:59:59 UTC: still this week
        event("2026-07-20T00:00:00Z", "inbound_order_created", "j3", {**LA, "quantity": 4}),  # the next Monday: the next week
        event("2026-07-12T23:59:59Z", "inbound_order_created", "j4", {**LA, "quantity": 8}),  # the Sunday before: the previous week
        weeks=(WEEK, NEXT_WEEK),
    )
    result = pipeline.compute_inbound_volume.fn(events)
    assert cell(result, "los_angeles", "fashion-co", "inbound_units_count", WEEK) == 3
    assert cell(result, "los_angeles", "fashion-co", "inbound_units_count", NEXT_WEEK) == 4
    assert len(result) == 2  # the Sunday before is outside the requested weeks


# --- the rows of the destination table -----------------------------------------------------------------------------------


def test_assemble_weekly_warehouse_client_rollup_produces_the_columns_and_values_of_the_context_table():
    events = prepared(
        event("2026-07-13T08:00:00Z", "inbound_order_created", "k1", {**LA, "quantity": 4200}),
        *[event(f"2026-07-14T0{n}:00:00Z", "outbound_order_created", f"k2{n}", {**LA, "exit_type": "dispatch", "quantity": 1}) for n in range(5)],
        event("2026-07-15T08:00:00Z", "stock_threshold_triggered", "k3", {**LA, "severity": "below_minimum", "quantity": 4}),
        event("2026-07-16T08:00:00Z", "inventory_discrepancy_detected", "k4", {**LA, "quantity": 2}),
    )
    inbound = pipeline.compute_inbound_volume.fn(events)
    outbound = pipeline.compute_outbound_throughput.fn(events)
    stockouts = pipeline.compute_stockout_frequency.fn(events)
    discrepancy = pipeline.compute_discrepancy_rate.fn(outbound, pipeline.count_discrepancy_events.fn(events))
    rollups = pipeline.assemble_weekly_warehouse_client_rollup.fn(inbound, outbound, stockouts, discrepancy, [WEEK, NEXT_WEEK])

    assert list(rollups[WEEK].columns) == [
        "warehouse", "client_id", "week_start", "inbound_units_count", "outbound_orders_count",
        "stockout_events_count", "discrepancy_events_count", "discrepancy_rate",
    ]
    assert rollups[WEEK].to_dict("records") == [{
        "warehouse": "los_angeles", "client_id": "fashion-co", "week_start": WEEK, "inbound_units_count": 4200,
        "outbound_orders_count": 5, "stockout_events_count": 1, "discrepancy_events_count": 1, "discrepancy_rate": 0.2,
    }]
    assert rollups[NEXT_WEEK].empty  # a week with no activity has an empty frame, not a missing key


# --- isolation ---------------------------------------------------------------------------------------------------------


def test_the_transformation_code_has_no_database_network_or_loops_over_rows():
    source = (ROOT / "data" / "pipelines" / "weekly_performance" / "transform.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {(node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level == 0}
    assert imported <= {"json", "datetime", "typing", "pandas"}, f"unexpected imports: {imported}"
    assert not [node for node in ast.walk(tree) if isinstance(node, (ast.For, ast.While))]
    for forbidden in ("iterrows", "itertuples", ".apply(", "sqlalchemy", "requests", "http"):
        assert forbidden not in source
    assert 'utc=True' in source
