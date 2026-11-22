"""Receiver of telemetry batches: validates each event and stores the valid ones (Phase 3).

It replaces the Phase 2 stub without changing the URL or the answer status, so the browser needs no change:
the TelemetryService only looks at the HTTP status code.

The body is parsed loosely (`events` is a list of raw items) on purpose. Typing it as `list[TelemetryEvent]` would
make FastAPI answer 422 for the whole request when a single event is wrong, and nothing would be stored.
Each item is validated inside the handler instead, so valid events are persisted regardless.
"""

import logging
import os
from datetime import datetime
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from ..auth import get_current_user
from ..database import SERVICE_UNAVAILABLE_MESSAGE, get_session
from ..accounts import User
from ..telemetry.report import InvalidPeriod, get_report
from ..telemetry_schemas import MAX_EVENTS_PER_BATCH
from ..telemetry_store import ingest

logger = logging.getLogger("services.telemetry")
# Uvicorn leaves the root logger at WARNING, which would hide the batch log that shows the pipeline working.
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     [telemetry] %(message)s"))
    logger.addHandler(_handler)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

DEFAULT_TELEMETRY_ENDPOINT = "/telemetry/events"


class TelemetryIngestBody(BaseModel):
    """The envelope of a batch. Items stay raw: they are validated one by one by `ingest`."""

    events: list[Any] = Field(max_length=MAX_EVENTS_PER_BATCH)


class TelemetryIngestResult(BaseModel):
    """What happened to the batch: how many events arrived, were stored and were rejected."""

    received: int
    stored: int
    rejected: int


def telemetry_endpoint() -> str:
    """Address where producers send batches (`TELEMETRY_ENDPOINT`): a path of this API or a full http(s) URL."""
    return (os.getenv("TELEMETRY_ENDPOINT") or DEFAULT_TELEMETRY_ENDPOINT).strip()


def validate_telemetry_config() -> None:
    """Fails at startup on a malformed endpoint and logs the configured value."""
    endpoint = telemetry_endpoint()
    if not (endpoint.startswith("/") or endpoint.startswith(("http://", "https://"))):
        raise RuntimeError("TELEMETRY_ENDPOINT must be a path starting with / or an http(s) URL.")
    logger.info("Telemetry endpoint configured as %s", endpoint)


@router.post("/events", response_model=TelemetryIngestResult)
def receive_events(body: TelemetryIngestBody, session: Session = Depends(get_session)) -> TelemetryIngestResult:
    """Stores the valid events of a batch with one bulk insert and reports received, stored and rejected counts."""
    stored, rejections = ingest(session, body.events)
    result = TelemetryIngestResult(received=len(body.events), stored=stored, rejected=len(rejections))
    logger.info("Telemetry batch: received=%d stored=%d rejected=%d", result.received, result.stored, result.rejected)
    return result


@router.get("/report")
def telemetry_report(
    response: Response,
    start_date: datetime | None = Query(default=None, description="ISO 8601, inclusive. Default: end_date minus 7 days."),
    end_date: datetime | None = Query(default=None, description="ISO 8601, exclusive. Default: now."),
    session: Session = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Operational report of the system (volume, errors, latency, availability, sign-in health) for a period.

    The metrics come from the pipeline in `services/telemetry/analysis.py` and are cached for 60 seconds per period,
    so calling this endpoint again does not recalculate. `X-Cache` says whether the answer came from the cache.
    """
    try:
        report, cache_hit = get_report(session, start_date, end_date)
    except InvalidPeriod as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    except pd.errors.DatabaseError as error:
        logger.exception("Telemetry report could not read the database")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=SERVICE_UNAVAILABLE_MESSAGE) from error
    response.headers["X-Cache"] = "HIT" if cache_hit else "MISS"
    return report
