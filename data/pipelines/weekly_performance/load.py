"""LOAD: replace one week of `reporting.weekly_warehouse_client_performance` with the freshly computed rows (design 2.6, 3.1).

One transaction per week. Inside it: read what is stored, upsert the rows that are new or changed (keyed by the unique
constraint `(warehouse, client_id, week_start)`), delete the keys the recompute no longer produces, commit. If anything
raises, the week rolls back untouched. Running it twice on the same rollup changes nothing the second time.
"""

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import Engine, and_, delete, or_, select
from sqlalchemy.dialects import postgresql, sqlite

from .schema import ensure_reporting, weekly_performance

KEY = ["warehouse", "client_id"]
KPI_COLUMNS = ["inbound_units_count", "outbound_orders_count", "stockout_events_count", "discrepancy_events_count", "discrepancy_rate"]


class EmptyExtractGuard(RuntimeError):
    """The recompute produced no rows for a week that already has rows. Refuse to wipe good data on a suspicious empty result."""


def _insert(engine: Engine):
    return (postgresql.insert if engine.dialect.name == "postgresql" else sqlite.insert)(weekly_performance)


def load_week(engine: Engine, week: date, rollup: pd.DataFrame, now: datetime | None = None) -> dict[str, int]:
    """Stores the rollup of one week and returns `{rows_inserted, rows_updated, rows_unchanged, rows_deleted}`."""
    ensure_reporting(engine)
    computed_at = now or datetime.now(timezone.utc)
    table = weekly_performance
    with engine.begin() as connection:  # commits on success, rolls back on any exception
        stored = pd.DataFrame(
            connection.execute(select(*[table.c[name] for name in KEY + KPI_COLUMNS]).where(table.c.week_start == week)).fetchall(),
            columns=KEY + KPI_COLUMNS,
        )
        if rollup.empty and not stored.empty:
            raise EmptyExtractGuard(f"The recompute of week {week.isoformat()} is empty but {len(stored)} rows are stored; refusing to delete them.")

        if rollup.empty:  # no activity that week and nothing stored: there is nothing to write, compare or delete
            return {"rows_inserted": 0, "rows_updated": 0, "rows_unchanged": 0, "rows_deleted": 0}

        new = rollup[KEY + KPI_COLUMNS].copy()
        new["discrepancy_rate"] = new["discrepancy_rate"].astype(float).round(6)
        stored["discrepancy_rate"] = stored["discrepancy_rate"].astype(float).round(6)
        merged = new.merge(stored, on=KEY, how="left", suffixes=("", "_stored"), indicator=True)
        is_new = merged["_merge"].eq("left_only")
        # A row whose numbers are identical to the stored ones is left alone, `computed_at` included.
        differs = pd.concat([merged[column].ne(merged[f"{column}_stored"]) for column in KPI_COLUMNS], axis=1).any(axis=1)
        to_write = merged[is_new | differs]

        rows: list[dict[str, Any]] = [
            {
                **{name: record[name] for name in KEY},
                "week_start": week,
                "computed_at": computed_at,
                **{column: (float(record[column]) if column == "discrepancy_rate" else int(record[column])) for column in KPI_COLUMNS},
            }
            for record in to_write.to_dict("records")
        ]
        if rows:
            statement = _insert(engine).values(rows)
            # The unique key makes this an upsert even if two writers ever raced; the guard skips an identical row.
            statement = statement.on_conflict_do_update(
                index_elements=KEY + ["week_start"],
                set_={name: statement.excluded[name] for name in KPI_COLUMNS + ["computed_at"]},
                where=or_(*[table.c[name].is_distinct_from(statement.excluded[name]) for name in KPI_COLUMNS]),
            )
            connection.execute(statement)

        stale = stored.merge(new[KEY], on=KEY, how="left", indicator=True).query("_merge == 'left_only'")
        if not stale.empty:
            connection.execute(
                delete(table).where(
                    table.c.week_start == week,
                    or_(*[and_(table.c.warehouse == r.warehouse, table.c.client_id == r.client_id) for r in stale.itertuples()]),
                )
            )

    return {
        "rows_inserted": int(is_new.sum()),
        "rows_updated": int((~is_new & differs).sum()),
        "rows_unchanged": int((~is_new & ~differs).sum()),
        "rows_deleted": len(stale),
    }
