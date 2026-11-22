"""Stub receiver for telemetry batches (Phase 2).

It validates the envelope, logs what arrived and answers 200. It persists nothing: storage, the
per-event property allowlist and authentication belong to Phase 3, which replaces this handler
without changing the URL or the `TelemetryEvent` model the browser already depends on.
"""

import logging
import os

from fastapi import APIRouter

from ..telemetry_schemas import TelemetryBatch, TelemetryReceipt

logger = logging.getLogger("services.telemetry")
# Uvicorn leaves the root logger at WARNING, which would hide the batch log that this stub exists to show.
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     [telemetry] %(message)s"))
    logger.addHandler(_handler)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

DEFAULT_TELEMETRY_ENDPOINT = "/telemetry/events"


def telemetry_endpoint() -> str:
    """Address where producers send batches (`TELEMETRY_ENDPOINT`): a path of this API or a full http(s) URL."""
    return (os.getenv("TELEMETRY_ENDPOINT") or DEFAULT_TELEMETRY_ENDPOINT).strip()


def validate_telemetry_config() -> None:
    """Fails at startup on a malformed endpoint and logs the configured value, so the pattern is in place for Phase 3."""
    endpoint = telemetry_endpoint()
    if not (endpoint.startswith("/") or endpoint.startswith(("http://", "https://"))):
        raise RuntimeError("TELEMETRY_ENDPOINT must be a path starting with / or an http(s) URL.")
    logger.info("Telemetry endpoint configured as %s", endpoint)


@router.post("/events", response_model=TelemetryReceipt)
def receive_events(batch: TelemetryBatch) -> TelemetryReceipt:
    """Accepts a batch of events, logs how many arrived and their types, and returns the count."""
    types = [event.event_type for event in batch.events]
    # event_type is pattern-validated (lowercase letters, digits, underscores), so it is safe to log.
    logger.info("Telemetry batch received: %d event(s) [%s]", len(types), ", ".join(types))
    return TelemetryReceipt(received=len(types))
