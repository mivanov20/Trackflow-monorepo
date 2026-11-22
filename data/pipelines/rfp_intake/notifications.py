"""The notification the intake raises when a new RFP ticket enters the system (Real Time Systems, part 1): a name, a payload, and a publisher that somebody else provides.

CONTEXT (real-time notification): it fires the exact moment a new ticket enters the system with `status = analyzing`, meaning the document was classified as a valid RFP and the flow starts
processing it. In the intake that is right after the classifier accepts the document and the metadata is read: a rejected document (a carrier pitch) never notifies anybody. The payload carries
ticket fields only, with the names the RFP system already uses:

    event: rfp_ticket_created
    data: {"ticket_id", "rfp_id", "client_name", "client_country", "services_requested", "departments_needed", "status": "analyzing", "created_at"}

The pipeline does not know how it is delivered. `set_publisher` registers a function (the API registers the SSE event bus at startup); without one, `publish` does nothing. A notification that
cannot be delivered must never break the intake, so errors are logged and swallowed. No model or agent is involved here: this is a communication layer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger("trackflow.rfp_intake")

EVENT_TICKET_CREATED = "rfp_ticket_created"
TICKET_CREATED_FIELDS = ("ticket_id", "rfp_id", "client_name", "client_country", "services_requested", "departments_needed", "status", "created_at")

_publisher: Callable[[str, dict], Any] | None = None


def set_publisher(publisher: Callable[[str, dict], Any] | None) -> None:
    global _publisher
    _publisher = publisher


def iso_utc(moment: datetime | None) -> str | None:
    """`2026-07-24T14:32:00Z`: the time as UTC with a Z, whether the database kept the time zone or not (SQLite does not)."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ticket_created_payload(ticket) -> dict:
    """The `data:` of `rfp_ticket_created`, from the ticket row."""
    return {
        "ticket_id": ticket.ticket_id,
        "rfp_id": ticket.rfp_id,
        "client_name": ticket.client_name,
        "client_country": ticket.client_country,
        "services_requested": list(ticket.services_requested or []),
        "departments_needed": list(ticket.departments_needed or []),
        "status": ticket.status,
        "created_at": iso_utc(ticket.created_at),
    }


def publish(event: str, payload: dict) -> None:
    if _publisher is None:
        return
    try:
        _publisher(event, payload)
    except Exception:  # noqa: BLE001 - the intake goes on whatever happens to the notification
        logger.exception("rfp notification %s could not be published", event)


def ticket_created(ticket) -> None:
    publish(EVENT_TICKET_CREATED, ticket_created_payload(ticket))
