"""Which ISO weeks a run covers. A week starts on Monday 00:00 UTC; the window is [first Monday, last Monday + 7 days)."""

from datetime import date, datetime, timedelta, timezone

from .config import DEFAULT_WEEKS_BACK, MAX_WEEKS_BACK


def monday_of(day: date) -> date:
    """The Monday of the week that contains `day`."""
    return day - timedelta(days=day.weekday())


def weeks_for_run(week_start: date | None = None, weeks_back: int = DEFAULT_WEEKS_BACK, now: datetime | None = None) -> list[date]:
    """The `week_start` of every week a run recomputes, oldest first.

    With `week_start` it is that single week (it must be a Monday). Without it, the last `weeks_back` COMPLETE weeks:
    the current week is excluded, so a half week is never shown as if it were a whole one.
    """
    if week_start is not None:
        if week_start.weekday() != 0:
            raise ValueError(f"week_start must be a Monday, got {week_start.isoformat()}.")
        return [week_start]
    if not 1 <= weeks_back <= MAX_WEEKS_BACK:
        raise ValueError(f"weeks_back must be between 1 and {MAX_WEEKS_BACK}.")
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    last_complete = monday_of(today) - timedelta(days=7)
    return [last_complete - timedelta(days=7 * offset) for offset in range(weeks_back - 1, -1, -1)]


def window_bounds(weeks: list[date]) -> tuple[datetime, datetime]:
    """UTC bounds for the SQL filter: inclusive start, exclusive end."""
    start = datetime(weeks[0].year, weeks[0].month, weeks[0].day, tzinfo=timezone.utc)
    end_day = weeks[-1] + timedelta(days=7)
    return start, datetime(end_day.year, end_day.month, end_day.day, tzinfo=timezone.utc)
