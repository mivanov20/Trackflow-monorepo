"""Receiver of telemetry batches: validates each event and stores the valid ones (Phase 3).

It replaces the Phase 2 stub without changing the URL or the answer status, so the browser needs no change:
the TelemetryService only looks at the HTTP status code.

The body is parsed loosely (`events` is a list of raw items) on purpose. Typing it as `list[TelemetryEvent]` would
make FastAPI answer 422 for the whole request when a single event is wrong, and nothing would be stored.
Each item is validated inside the handler instead, so valid events are persisted regardless.
"""

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session

from ..database import get_session
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
