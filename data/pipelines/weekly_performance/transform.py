"""TRANSFORM: raw telemetry events to one rollup per ISO week (design 2.5). Pure Pandas, no database, no loops.

Order of the rules: refine, de-duplicate, convert types, select the weeks, flag, group and aggregate, check.
"""

import json
from datetime import date
from typing import Any

import pandas as pd

from .config import COUNT_LOSS_EXITS, VALUE_MISMATCH_TOLERANCE, WAREHOUSES

# Bump when the transformation rules change: it is part of the cache key of the transform task.
TRANSFORM_VERSION = "1"

ROLLUP_COLUMNS = [
    "warehouse", "client_id", "week_start", "inbound_units_count", "outbound_orders_count",
    "stockout_events_count", "discrepancy_events_count", "discrepancy_rate",
]
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


def weekly_rollup(raw: pd.DataFrame, weeks: list[date], count_loss_exits: bool = COUNT_LOSS_EXITS, tolerance: float = VALUE_MISMATCH_TOLERANCE) -> dict[str, Any]:
    """Returns `{"rollups": {week_start: DataFrame}, "counters": {...}}` for the requested weeks.

    Every requested week has an entry; a week with no usable event has an empty frame (the load stage needs that to
    tell "no activity" from "the extract failed silently").
    """
    df = raw.copy()
    # 1. REFINE: the tags the KPIs need, as columns. Rows that cannot belong to a destination row are discarded and counted.
    tags = pd.json_normalize(df["tags"].map(_as_dict).tolist()).reindex(columns=TAG_FIELDS).set_axis(df.index)
    df = df.drop(columns="tags").join(tags).rename(columns={"_envelope.eventId": "event_id"})
    valid = df["warehouse"].isin(WAREHOUSES) & df["client_id"].notna()
    records_discarded = int((~valid).sum())
    df = df[valid]

    # 2. DE-DUPLICATE by eventId: a browser retry can store the same event twice. Events without an id are kept.
    has_id = df["event_id"].notna()
    duplicated = df["event_id"].duplicated(keep="first") & has_id
    duplicates_dropped = int(duplicated.sum())
    df = df[~duplicated].copy()

    # 3. CONVERT TYPES before grouping: timestamps to UTC datetimes, then the Monday of the event's UTC week.
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    day = df["timestamp"].dt.normalize()
    df["week_start"] = (day - pd.to_timedelta(day.dt.day_of_week, unit="D")).dt.date
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")

    # 4. CHECK (before anything is written): the `value` column must agree with `tags.quantity` where both exist.
    movements = df["event_type"].isin(["inbound_order_created", "outbound_order_created"]) & df["value"].notna() & df["quantity"].notna()
    mismatches = int((movements & (pd.to_numeric(df["value"], errors="coerce") != df["quantity"])).sum())
    if movements.sum() and mismatches / movements.sum() > tolerance:
        raise DataQualityError(f"{mismatches} of {int(movements.sum())} movement events have a value that differs from their quantity.")

    # 5. SELECT the weeks of the run (the SQL window can include a partial week at its edges), then FLAG.
    df = df[df["week_start"].isin(weeks)].copy()
    is_outbound = df["event_type"].eq("outbound_order_created")
    df["inbound_units"] = df["quantity"].where(df["event_type"].eq("inbound_order_created"), 0).fillna(0)
    df["is_outbound"] = is_outbound if count_loss_exits else is_outbound & df["exit_type"].eq("dispatch")
    df["is_stockout"] = df["event_type"].eq("stock_threshold_triggered")
    df["is_discrepancy"] = df["event_type"].eq("inventory_discrepancy_detected")

    # 6. GROUP AND AGGREGATE: one row per warehouse, client and week. A row never mixes clients.
    grouped = df.groupby(["warehouse", "client_id", "week_start"]).agg(
        inbound_units_count=("inbound_units", "sum"),
        outbound_orders_count=("is_outbound", "sum"),
        stockout_events_count=("is_stockout", "sum"),
        discrepancy_events_count=("is_discrepancy", "sum"),
    ).reset_index()
    counts = ["inbound_units_count", "outbound_orders_count", "stockout_events_count", "discrepancy_events_count"]
    grouped[counts] = grouped[counts].astype(int)
    orders = grouped["outbound_orders_count"]
    grouped["discrepancy_rate"] = (grouped["discrepancy_events_count"] / orders.where(orders > 0)).fillna(0).round(6)
    grouped = grouped[ROLLUP_COLUMNS]

    rollups = {week: grouped[grouped["week_start"].eq(week)].reset_index(drop=True) for week in weeks}
    counters = {
        "records_extracted": len(raw),
        "duplicates_dropped": duplicates_dropped,
        "records_discarded": records_discarded,
        "value_mismatches": mismatches,
    }
    return {"rollups": rollups, "counters": counters}
