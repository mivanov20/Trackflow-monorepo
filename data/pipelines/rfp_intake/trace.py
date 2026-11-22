"""Traceability of the whole workflow: for any ticket, which agent did what, in what order (Parts 1, 2 and 3 in one timeline).

Every node execution writes one event to `rfp_trace_events`: the agent, the node, a SUMMARY of its input and of its output (never the whole document: long text is cut) and the time.
`seq` is the order within the ticket. An event with a `dedupe_key` is written once (a graph node that is re-run after an interrupt must not log its opening twice).
The trace is separate from the checkpoint: the checkpoint lets a run resume, the trace tells people what happened.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from .dblock import LOCK as _lock


def now() -> datetime:
    return datetime.now(timezone.utc)


MAX_TEXT = 400


def summarize(value: Any, depth: int = 0) -> Any:
    """A JSON-safe, short version of a value: strings are cut, lists show their length and first items, deep structures are flattened to a count."""
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT else value[: MAX_TEXT - 1] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        if depth >= 3:
            return f"{{{len(value)} keys}}"
        return {str(key): summarize(item, depth + 1) for key, item in list(value.items())[:20]}
    if isinstance(value, (list, tuple, set)):
        if depth >= 3:
            return f"[{len(value)} items]"
        items = [summarize(item, depth + 1) for item in list(value)[:8]]
        return items if len(value) <= 8 else [*items, f"… {len(value) - 8} more"]
    return str(value)[:MAX_TEXT]


def record(engine, ticket_id: str, part: int, agent: str, node: str, input: Any = None, output: Any = None, department_id: str | None = None, dedupe_key: str | None = None) -> bool:
    """Writes one event. Returns False when an event with the same `dedupe_key` already exists for the ticket."""
    from services import models

    with _lock, Session(engine) as session:
        if dedupe_key:
            exists = session.exec(select(models.RfpTraceEvent.id).where(models.RfpTraceEvent.ticket_id == ticket_id, models.RfpTraceEvent.dedupe_key == dedupe_key)).first()
            if exists:
                return False
        last = session.exec(select(func.max(models.RfpTraceEvent.seq)).where(models.RfpTraceEvent.ticket_id == ticket_id)).one()
        session.add(
            models.RfpTraceEvent(
                ticket_id=ticket_id,
                seq=(last or 0) + 1,
                part=part,
                agent=agent,
                node=node,
                department_id=department_id,
                input=summarize(input or {}),
                output=summarize(output or {}),
                dedupe_key=dedupe_key,
                at=now(),
            )
        )
        session.commit()
    return True


def status_change(engine, ticket_id: str, part: int, old: str, new: str, reason: str) -> None:
    """One event per real change of the ticket's status: the status timeline of a ticket is part of its trace."""
    if old != new:
        record(engine, ticket_id, part, "ticket", "status", {"from": old, "reason": reason}, {"to": new})


def events(session: Session, ticket_id: str, part: int | None = None) -> list[dict]:
    from services import models

    query = select(models.RfpTraceEvent).where(models.RfpTraceEvent.ticket_id == ticket_id)
    if part is not None:
        query = query.where(models.RfpTraceEvent.part == part)
    rows = session.exec(query.order_by(models.RfpTraceEvent.seq))
    return [
        {"seq": row.seq, "part": row.part, "agent": row.agent, "node": row.node, "department_id": row.department_id, "input": row.input, "output": row.output, "at": row.at.isoformat() if row.at else None}
        for row in rows
    ]
