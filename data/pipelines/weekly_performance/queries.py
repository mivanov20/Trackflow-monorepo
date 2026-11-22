"""Reads of the KPI table for the business side: what `GET /reporting/weekly-warehouse-client-performance` serves."""

from datetime import date
from typing import Any

from sqlalchemy import Engine, func, select

from .schema import ensure_reporting, weekly_performance

ENTRY_FIELDS = [
    "warehouse", "client_id", "inbound_units_count", "outbound_orders_count", "stockout_events_count", "discrepancy_events_count", "discrepancy_rate",
]


def get_weekly_performance(engine: Engine, week_start: date | None = None) -> dict[str, Any] | None:
    """`{"week_start": ..., "entries": [...]}` for the given Monday, or for the most recent computed week.

    Returns None only when no week has been computed at all. An explicit week with no rows is a valid, empty answer.
    """
    ensure_reporting(engine)
    table = weekly_performance
    with engine.connect() as connection:
        if week_start is None:
            week_start = connection.execute(select(func.max(table.c.week_start))).scalar()
            if week_start is None:
                return None
        rows = connection.execute(
            select(*[table.c[name] for name in ENTRY_FIELDS]).where(table.c.week_start == week_start).order_by(table.c.warehouse, table.c.client_id)
        ).mappings().all()
    return {
        "week_start": week_start.isoformat() if hasattr(week_start, "isoformat") else str(week_start),
        "entries": [{**{name: row[name] for name in ENTRY_FIELDS[:-1]}, "discrepancy_rate": float(row["discrepancy_rate"])} for row in rows],
    }
