"""The operational report: resolves the period once, calls every metric with it, and caches the result."""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session

from .analysis import METRICS
from .cache import TTLCache

DEFAULT_PERIOD_DAYS = 7
MAX_PERIOD_DAYS = 90

REPORT_CACHE = TTLCache()


class InvalidPeriod(ValueError):
    """The requested period cannot be served (reversed, empty or too long)."""


def _as_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def resolve_period(start_date: datetime | None, end_date: datetime | None, now: datetime | None = None) -> tuple[datetime, datetime]:
    """The period of the report, resolved ONCE: inclusive start, exclusive end, UTC.

    With no parameters it is the last 7 days (now - 7d to now). Only an end gives the 7 days before it;
    only a start runs until now.
    """
    now = _as_utc(now) if now else datetime.now(timezone.utc)
    end = _as_utc(end_date) if end_date else now
    start = _as_utc(start_date) if start_date else end - timedelta(days=DEFAULT_PERIOD_DAYS)
    if start >= end:
        raise InvalidPeriod("start_date must be earlier than end_date.")
    if end - start > timedelta(days=MAX_PERIOD_DAYS):
        raise InvalidPeriod(f"The period cannot be longer than {MAX_PERIOD_DAYS} days.")
    return start, end


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_report(session: Session, start: datetime, end: datetime) -> dict[str, Any]:
    """Runs every metric of the pipeline for the same, already resolved period."""
    return {
        "period": {"from": _iso(start), "to": _iso(end)},
        "metrics": {name: metric(session, start, end) for name, metric in METRICS.items()},
    }


def cache_key(start_date: datetime | None, end_date: datetime | None) -> tuple[str | None, str | None]:
    """Requests without parameters share one entry, so "the last 7 days" can be served from the cache.

    A default period is anchored to the moment it is computed; keying it by that moment would never hit.
    """
    return (_iso(_as_utc(start_date)) if start_date else None, _iso(_as_utc(end_date)) if end_date else None)


def get_report(session: Session, start_date: datetime | None, end_date: datetime | None, cache: TTLCache | None = None) -> tuple[dict[str, Any], bool]:
    """The report for the requested period and whether it came from the cache. Validates the period before touching the cache."""
    resolve_period(start_date, end_date)  # fail fast on a bad period, before the cache or the database
    store = cache if cache is not None else REPORT_CACHE

    def compute() -> dict[str, Any]:
        start, end = resolve_period(start_date, end_date)
        return build_report(session, start, end)

    return store.get_or_compute(cache_key(start_date, end_date), compute)
