"""TRANSFORM: raw telemetry events to one rollup per ISO week (design 2.5). Pure Pandas, no database, no loops.

One function prepares the events, one function per KPI of the company CONTEXT computes it, and one assembles the rows:

    prepare_events -> inbound_volume, outbound_throughput, stockout_frequency, discrepancy_events
                   -> discrepancy_rate -> assemble_weekly_rollup

Every function takes and returns plain DataFrames, so each can be tested alone with in-memory events. The Prefect tasks
of `data/pipelines/pipeline.py` are thin wrappers around them.
"""

import json
from datetime import date
from typing import Any

import pandas as pd

from .config import COUNT_LOSS_EXITS, EVENT_TYPES, VALUE_MISMATCH_TOLERANCE, WAREHOUSES

# Bump when the transformation rules change: it is part of the cache key of the prepare task.
TRANSFORM_VERSION = "2"

KEY = ["warehouse", "client_id", "week_start"]
ROLLUP_COLUMNS = KEY + [
    "inbound_units_count", "outbound_orders_count", "stockout_events_count", "discrepancy_events_count", "discrepancy_rate",
]
COUNT_COLUMNS = ["inbound_units_count", "outbound_orders_count", "stockout_events_count", "discrepancy_events_count"]
REQUIRED_COLUMNS = ["timestamp", "event_type", "tags"]
TAG_FIELDS = ["warehouse", "client_id", "quantity", "exit_type", "_envelope.eventId"]


class DataQualityError(ValueError):
    """The extracted data breaks a rule that makes the rollup untrustworthy; the run fails before loading anything."""


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value) if isinstance(value, (str, bytes)) else None
    except ValueError:
        parsed = None
    return parsed if isinstance(parsed, dict) else {}


def _empty(column: str) -> pd.DataFrame:
    return pd.DataFrame({name: pd.Series(dtype="object") for name in KEY} | {column: pd.Series(dtype="float64")})


def _count(events: pd.DataFrame, mask: pd.Series, column: str) -> pd.DataFrame:
    selected = events[mask]
    if selected.empty:
        return _empty(column)
    return selected.groupby(KEY).size().rename(column).reset_index()


# --- preparation -------------------------------------------------------------------------------------------------------


def prepare_events(raw: pd.DataFrame, weeks: list[date], tolerance: float = VALUE_MISMATCH_TOLERANCE) -> dict[str, Any]:
    """Refines, de-duplicates and types the raw events. Returns `{"events": DataFrame, "counters": {...}}`.

    `events` has one row per usable event with `warehouse`, `client_id`, `week_start`, `quantity`, `exit_type`,
    `event_type`. Anything that cannot belong to a destination row is discarded and counted, never silently lost.
    """
    missing = [name for name in REQUIRED_COLUMNS if name not in raw.columns]
    if missing:
        raise DataQualityError(f"The extracted events lack the column(s): {', '.join(missing)}.")
    df = raw.copy()
    if "value" not in df.columns:
        df["value"] = pd.NA

    # REFINE: the tags the KPIs need, as columns.
    tags = pd.json_normalize(df["tags"].map(_as_dict).tolist()).reindex(columns=TAG_FIELDS).set_axis(df.index)
    df = df.drop(columns="tags").join(tags).rename(columns={"_envelope.eventId": "event_id"})
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")

    # DISCARD what cannot be counted: an unknown warehouse, no client, no valid time, an event outside the four KPIs, and
    # a received quantity that is missing or negative (it would silently understate Inbound Volume).
    inbound = df["event_type"].eq("inbound_order_created")
    usable = (
        df["warehouse"].isin(WAREHOUSES)
        & df["client_id"].notna()
        & df["timestamp"].notna()
        & df["event_type"].isin(EVENT_TYPES)
        & ~(inbound & (df["quantity"].isna() | (df["quantity"] < 0)))
    )
    records_discarded = int((~usable).sum())
    df = df[usable]

    # DE-DUPLICATE by eventId: a browser retry can store the same event twice. Events without an id are kept.
    duplicated = df["event_id"].duplicated(keep="first") & df["event_id"].notna()
    duplicates_dropped = int(duplicated.sum())
    df = df[~duplicated].copy()

    # CONVERT TYPES before grouping: the Monday of the event's UTC week.
    day = df["timestamp"].dt.normalize()
    df["week_start"] = (day - pd.to_timedelta(day.dt.day_of_week, unit="D")).dt.date

    # CHECK: the `value` column must agree with `tags.quantity` where both exist (the stored row is intact).
    movements = df["event_type"].isin(["inbound_order_created", "outbound_order_created"]) & df["value"].notna() & df["quantity"].notna()
    mismatches = int((movements & (pd.to_numeric(df["value"], errors="coerce") != df["quantity"])).sum())
    if movements.sum() and mismatches / movements.sum() > tolerance:
        raise DataQualityError(f"{mismatches} of {int(movements.sum())} movement events have a value that differs from their quantity.")

    df = df[df["week_start"].isin(weeks)]
    counters = {
        "records_extracted": len(raw),
        "duplicates_dropped": duplicates_dropped,
        "records_discarded": records_discarded,
        "value_mismatches": mismatches,
    }
    return {"events": df[["event_type", "warehouse", "client_id", "week_start", "quantity", "exit_type"]].reset_index(drop=True), "counters": counters}


# --- one function per KPI of the company CONTEXT ------------------------------------------------------------------------


def inbound_volume(events: pd.DataFrame) -> pd.DataFrame:
    """Inbound Volume: units received, the sum of `quantity` of `inbound_order_created`, per warehouse, client and week."""
    received = events[events["event_type"].eq("inbound_order_created")]
    if received.empty:
        return _empty("inbound_units_count")
    return received.groupby(KEY)["quantity"].sum().rename("inbound_units_count").reset_index()


def outbound_throughput(events: pd.DataFrame, count_loss_exits: bool = COUNT_LOSS_EXITS) -> pd.DataFrame:
    """Outbound Throughput: orders picked and dispatched. A `loss` exit is a write-off, not a dispatch, unless asked for."""
    outbound = events["event_type"].eq("outbound_order_created")
    mask = outbound if count_loss_exits else outbound & events["exit_type"].eq("dispatch")
    return _count(events, mask, "outbound_orders_count")


def stockout_frequency(events: pd.DataFrame) -> pd.DataFrame:
    """Stockout Frequency: how many times a SKU fell below the minimum, the count of `stock_threshold_triggered`."""
    return _count(events, events["event_type"].eq("stock_threshold_triggered"), "stockout_events_count")


def discrepancy_events(events: pd.DataFrame) -> pd.DataFrame:
    """The numerator of the Discrepancy Rate: the count of `inventory_discrepancy_detected`."""
    return _count(events, events["event_type"].eq("inventory_discrepancy_detected"), "discrepancy_events_count")


def discrepancy_rate(outbound: pd.DataFrame, discrepancies: pd.DataFrame) -> pd.DataFrame:
    """Discrepancy Rate: discrepancy events divided by outbound orders, 0 when the week has no outbound orders."""
    both = pd.concat([outbound, discrepancies], ignore_index=True).reindex(columns=KEY + ["outbound_orders_count", "discrepancy_events_count"])
    both = both.groupby(KEY)[["outbound_orders_count", "discrepancy_events_count"]].sum().reset_index() if not both.empty else both
    if both.empty:
        return _empty("discrepancy_events_count").assign(discrepancy_rate=pd.Series(dtype="float64"))
    orders = both["outbound_orders_count"].fillna(0)
    both["discrepancy_events_count"] = both["discrepancy_events_count"].fillna(0)
    both["discrepancy_rate"] = (both["discrepancy_events_count"] / orders.where(orders > 0)).fillna(0).round(6)
    return both[KEY + ["discrepancy_events_count", "discrepancy_rate"]]


# --- assembly ------------------------------------------------------------------------------------------------------------


def assemble_weekly_rollup(
    inbound: pd.DataFrame, outbound: pd.DataFrame, stockouts: pd.DataFrame, discrepancy: pd.DataFrame, weeks: list[date]
) -> dict[date, pd.DataFrame]:
    """One frame per requested week with the five computed columns of the destination table. A row never mixes clients.

    Every requested week has an entry; a week with no usable event has an empty frame (the load stage needs that to tell
    "no activity" from "the extract failed silently").
    """
    combined = pd.concat([inbound, outbound, stockouts, discrepancy], ignore_index=True).reindex(columns=ROLLUP_COLUMNS)
    if combined.empty:
        grouped = pd.DataFrame({name: pd.Series(dtype="object") for name in ROLLUP_COLUMNS})
    else:
        metrics = COUNT_COLUMNS + ["discrepancy_rate"]
        grouped = combined.groupby(KEY)[metrics].sum().reset_index()
        grouped[COUNT_COLUMNS] = grouped[COUNT_COLUMNS].fillna(0).astype(int)
        grouped["discrepancy_rate"] = grouped["discrepancy_rate"].fillna(0).round(6)
    return {week: grouped[grouped["week_start"].eq(week)].reset_index(drop=True) for week in weeks}


def weekly_rollup(raw: pd.DataFrame, weeks: list[date], count_loss_exits: bool = COUNT_LOSS_EXITS, tolerance: float = VALUE_MISMATCH_TOLERANCE) -> dict[str, Any]:
    """The whole transformation in one call: `{"rollups": {week_start: DataFrame}, "counters": {...}}`."""
    prepared = prepare_events(raw, weeks, tolerance)
    events = prepared["events"]
    outbound = outbound_throughput(events, count_loss_exits)
    rollups = assemble_weekly_rollup(
        inbound_volume(events), outbound, stockout_frequency(events), discrepancy_rate(outbound, discrepancy_events(events)), weeks
    )
    return {"rollups": rollups, "counters": prepared["counters"]}
