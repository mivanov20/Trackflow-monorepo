"""Validation and storage of telemetry events (Phase 3).

Each raw event of a batch is checked on its own, so one bad event never cancels the rest:

1. the envelope, with the `TelemetryEvent` model of Phase 2, reused without changes;
2. the event type and its properties, against the allowlist generated from the approved plan
   (docs/telemetry/event-schemas.json, through `telemetry_catalogue.py`).

Valid events become rows of `telemetry_events` and are inserted with ONE statement per batch.
Nothing here updates or deletes: events are immutable facts.
"""

import logging
import math
import uuid
from datetime import timezone
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import insert
from sqlmodel import Session

from .database import commit_session
from .models import TelemetryEventRecord
from .telemetry_catalogue import EVENT_CATALOGUE, SCHEMA_MAJOR
from .telemetry_schemas import TelemetryEvent

logger = logging.getLogger("services.telemetry")

SERVICE_BACKOFFICE = "backoffice"
MAX_LOGGED_REJECTIONS = 20


def matches_schema(schema: dict[str, Any], value: Any) -> bool:
    """Checks a value against the JSON Schema subset the plan uses: type, enum, pattern, bounds, arrays, objects."""
    import re

    types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    enum = schema.get("enum")
    if value is None:
        return "null" in types and (enum is None or None in enum)
    if enum is not None and value not in enum:
        return False
    if isinstance(value, bool):
        return "boolean" in types
    if isinstance(value, str):
        if "string" not in types:
            return False
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return False
        return "pattern" not in schema or re.search(schema["pattern"], value) is not None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return False
        is_integer = isinstance(value, int) or value.is_integer()
        if not ("number" in types or ("integer" in types and is_integer)):
            return False
        if "minimum" in schema and value < schema["minimum"]:
            return False
        if "maximum" in schema and value > schema["maximum"]:
            return False
        return "exclusiveMinimum" not in schema or value > schema["exclusiveMinimum"]
    if isinstance(value, list):
        if "array" not in types:
            return False
        if "minItems" in schema and len(value) < schema["minItems"]:
            return False
        if schema.get("uniqueItems") and len({repr(item) for item in value}) != len(value):
            return False
        return "items" not in schema or all(matches_schema(schema["items"], item) for item in value)
    if isinstance(value, dict):
        if "object" not in types:
            return False
        allowed = schema.get("properties", {})
        return all(
            matches_schema(allowed[key], item) if key in allowed else schema.get("additionalProperties") is not False
            for key, item in value.items()
        )
    return False


def properties_problem(entry: dict[str, Any], properties: dict[str, Any]) -> str | None:
    """Why `properties` breaks the event's allowlist, as a short code, or None. The code never contains user values."""
    allowed = entry["properties"]
    if any(key not in allowed for key in properties):
        return "unknown_property"
    for key in entry["required"]:
        if key not in properties:
            return f"missing_property:{key}"
    for key, value in properties.items():
        if not matches_schema(allowed[key], value):
            return f"invalid_value:{key}"
    return None


def check_event(raw: Any) -> tuple[TelemetryEvent | None, str | None]:
    """Validates one raw event. Returns the parsed event, or None with the reason it was rejected."""
    if not isinstance(raw, dict):
        return None, "not_an_object"
    try:
        event = TelemetryEvent.model_validate(raw)
    except ValidationError:
        return None, "invalid_envelope"
    entry = EVENT_CATALOGUE.get(event.event_type)
    if entry is None:
        return None, "unknown_event_type"
    if int(event.schemaVersion.split(".")[0]) != SCHEMA_MAJOR:
        return None, "unsupported_schema_version"
    problem = properties_problem(entry, event.properties)
    if problem:
        return None, problem
    return event, None


def _number(value: Any) -> int | Decimal | None:
    """A property value as a number for the `value` column, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        return Decimal(str(value)) if math.isfinite(value) else None
    return value


def to_row(event: TelemetryEvent, service: str = SERVICE_BACKOFFICE) -> dict[str, Any]:
    """Maps an event to a `telemetry_events` row (docs/telemetry/telemetry-plan.md, section 13)."""
    entry = EVENT_CATALOGUE[event.event_type]
    tags: dict[str, Any] = dict(event.properties)
    # The envelope ids are kept under one reserved key so analytics can correlate and de-duplicate by eventId.
    tags["_envelope"] = {
        "eventId": str(event.eventId),
        "sessionId": str(event.sessionId) if event.sessionId else None,
        "userId": str(event.userId) if event.userId else None,
        "schemaVersion": event.schemaVersion,
        "requestId": str(event.requestId),
    }
    return {
        "id": uuid.uuid4(),
        "timestamp": event.timestamp.astimezone(timezone.utc),
        "service": service,
        "event_type": event.event_type,
        "level": entry["level"],
        "value": _number(event.properties.get(entry["value"])) if entry["value"] else None,
        "message": entry["message"],
        "tags": tags,
    }


def ingest(session: Session, raw_events: list[Any], service: str = SERVICE_BACKOFFICE) -> tuple[int, list[tuple[int, str]]]:
    """Validates a batch event by event and stores the valid ones with a single INSERT.

    Returns how many events were stored and the (position, reason) of each rejected one.
    """
    rows: list[dict[str, Any]] = []
    rejections: list[tuple[int, str]] = []
    for position, raw in enumerate(raw_events):
        event, reason = check_event(raw)
        if event is None:
            rejections.append((position, reason or "invalid"))
        else:
            rows.append(to_row(event, service))
    if rows:
        # One statement, one transaction, whatever the batch size: never one INSERT per event.
        session.exec(insert(TelemetryEventRecord.__table__).values(rows))
        commit_session(session)
    if rejections:
        shown = ", ".join(f"#{position}:{reason}" for position, reason in rejections[:MAX_LOGGED_REJECTIONS])
        logger.warning("Telemetry events rejected (%d): %s", len(rejections), shown)
    return len(rows), rejections
