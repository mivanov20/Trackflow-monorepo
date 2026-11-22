"""EXTRACT: the mandatory telemetry events of the run's window, read-only, filtered in SQL (design 2.3)."""

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import Engine, select

from .config import EVENT_TYPES
from .schema import telemetry_source


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def extract_events(engine: Engine, window_start: datetime, window_end: datetime) -> pd.DataFrame:
    """Rows of `telemetry_events` with `window_start <= timestamp < window_end` for the four KPI event types.

    Only SELECT is ever issued against the source. The window and the event types are filtered in SQL, using the
    `timestamp` and `event_type` indexes; the `tags` dimensions are extracted later, in Pandas.
    """
    statement = select(telemetry_source.c.timestamp, telemetry_source.c.event_type, telemetry_source.c.value, telemetry_source.c.tags).where(
        telemetry_source.c.timestamp >= _utc(window_start),
        telemetry_source.c.timestamp < _utc(window_end),
        telemetry_source.c.event_type.in_(EVENT_TYPES),
    )
    with engine.connect() as connection:
        return pd.read_sql_query(statement, connection)


def newest_timestamp(raw: pd.DataFrame) -> datetime | None:
    """The newest event the run read (its freshness), as an aware UTC datetime, or None for an empty extract."""
    if raw.empty:
        return None
    return pd.to_datetime(raw["timestamp"], utc=True).max().to_pydatetime()
