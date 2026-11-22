"""Persistence of the intake: tickets and department sections in the application database (PostgreSQL / Supabase in production), through SQLModel.

The pipeline writes through these functions and the routers read through them; nothing here is a source of truth outside the database (no JSON files, no TinyDB).
`services.models` is imported inside the functions so the agents and the graph can be used (and tested) without the web application.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import Session, select

from .config import ANALYZING, FAILED, HANDOFF_NOT_READY, HANDOFF_READY


def _models():
    from services import models

    return models


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_ticket_id() -> str:
    return str(uuid.uuid4())


def next_rfp_id(session: Session) -> str:
    """`TF-RFP-<year>-<n>`: the reference Sales quotes. The sequence is per year and counts every ticket, discarded ones included."""
    models = _models()
    year = now().year
    prefix = f"TF-RFP-{year}-"
    count = session.exec(select(func.count()).select_from(models.RfpTicket).where(models.RfpTicket.rfp_id.like(f"{prefix}%"))).one()
    number = int(count) + 1
    existing = {row for row in session.exec(select(models.RfpTicket.rfp_id).where(models.RfpTicket.rfp_id.like(f"{prefix}%")))}
    while f"{prefix}{number:04d}" in existing:
        number += 1
    return f"{prefix}{number:04d}"


def create_ticket(session: Session, *, ticket_id: str, raw_pdf_path: str, original_filename: str, uploaded_by: str | None) -> "object":
    """The row created at upload time: status `analyzing`, before any agent has run."""
    models = _models()
    ticket = models.RfpTicket(
        ticket_id=ticket_id,
        rfp_id=next_rfp_id(session),
        status=ANALYZING,
        raw_pdf_path=raw_pdf_path,
        original_filename=original_filename,
        uploaded_by=uploaded_by,
        current_step="Queued",
        progress=[{"step": "upload", "detail": "PDF received and stored", "at": now().isoformat()}],
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def get_ticket(session: Session, ticket_id: str):
    return session.get(_models().RfpTicket, ticket_id)


def list_tickets(session: Session, limit: int = 50) -> list:
    models = _models()
    return list(session.exec(select(models.RfpTicket).order_by(models.RfpTicket.created_at.desc()).limit(limit)))


def sections_of(session: Session, ticket_id: str) -> list:
    models = _models()
    rows = list(session.exec(select(models.DepartmentSection).where(models.DepartmentSection.ticket_id == ticket_id)))
    order = {"warehouse": 0, "lastmile": 1, "reverse": 2}
    return sorted(rows, key=lambda row: order.get(row.department_id, 9))


def record_step(session: Session, ticket_id: str, label: str, entries: list[dict]) -> None:
    """While the pipeline runs: what finished and what is next, so the UI that polls can show it."""
    ticket = get_ticket(session, ticket_id)
    if ticket is None:
        return
    ticket.progress = [*ticket.progress, *[{"step": entry["step"], "detail": entry["detail"], "engine": entry.get("engine"), "at": now().isoformat()} for entry in entries]]
    ticket.current_step = label
    ticket.updated_at = now()
    session.add(ticket)
    session.commit()


def _apply_metadata(ticket, meta: dict, cost: dict) -> None:
    ticket.client_name = meta.get("client_name")
    ticket.client_country = meta.get("client_country")
    ticket.currency = meta.get("currency")
    ticket.client_contact = meta.get("client_contact") or {}
    ticket.services_requested = meta.get("services_requested", [])
    ticket.monthly_volume = meta.get("monthly_volume")
    ticket.deadline = meta.get("deadline")
    ticket.budget_range = meta.get("budget_range")
    ticket.departments_needed = meta.get("departments_needed", [])
    ticket.open_questions = meta.get("open_questions", [])
    ticket.metadata_extra = {"extras": meta.get("extras", {}), "services_excluded": meta.get("services_excluded", []), "deadline_kind": meta.get("deadline_kind"), "metadata_engine": meta.get("engine")}
    ticket.readability = cost or {}


def _engine_of(state: dict) -> str:
    engines = {step.get("engine") for step in state.get("steps", []) if step.get("engine")}
    if engines == {"llm"}:
        return "llm"
    return "rules" if engines <= {"rules"} else "mixed"


def finish_ticket(session: Session, ticket_id: str, state: dict) -> None:
    """The end of a run that completed: the verdict, and for a valid RFP its metadata, sections, summary and handoff, in ONE transaction (a ticket is never
    `intake_complete` without its sections)."""
    models = _models()
    ticket = get_ticket(session, ticket_id)
    classification = state["classification"]
    ticket.is_rfp = classification["is_rfp"]
    ticket.document_type = classification["document_type"]
    ticket.classification_reason = classification["reason"]
    ticket.classification_confidence = classification["confidence"]
    ticket.markdown = state.get("markdown")
    ticket.engine = _engine_of(state)
    if state["outcome"] == "intake_complete":
        _apply_metadata(ticket, state["metadata"], state["readability"])
        synthesis = state["synthesis"]
        ticket.summary = synthesis["summary"]
        ticket.open_questions = synthesis["open_questions"]
        ticket.handoff = state["handoff"]
        ticket.handoff_state = HANDOFF_READY
        for existing in sections_of(session, ticket_id):
            session.delete(existing)
        session.flush()
        for section in synthesis["sections"]:
            session.add(models.DepartmentSection(ticket_id=ticket_id, department_id=section["department_id"], owner=section["owner"], key_aspects=section["key_aspects"], engine=section["engine"]))
    else:
        ticket.handoff_state = HANDOFF_NOT_READY
    ticket.status = state["outcome"]
    ticket.current_step = "Done" if state["outcome"] == "intake_complete" else "Discarded"
    ticket.error_message = None
    ticket.progress = [*ticket.progress, {"step": state["outcome"], "detail": classification["reason"] if state["outcome"] == "discarded" else "Sales summary and handoff ready", "at": now().isoformat()}]
    ticket.updated_at = now()
    session.add(ticket)
    session.commit()


def fail_ticket(session: Session, ticket_id: str, message: str) -> None:
    """The pipeline itself broke: the ticket says so, with a message safe to show, instead of staying `analyzing` forever."""
    ticket = get_ticket(session, ticket_id)
    if ticket is None:
        return
    ticket.status = FAILED
    ticket.current_step = "Failed"
    ticket.error_message = message
    ticket.progress = [*ticket.progress, {"step": "failed", "detail": message, "at": now().isoformat()}]
    ticket.updated_at = now()
    session.add(ticket)
    session.commit()


def reset_for_reprocess(session: Session, ticket_id: str) -> None:
    """Back to `analyzing` (the manual reprocess script): results of the earlier run are cleared, the PDF stays."""
    ticket = get_ticket(session, ticket_id)
    for section in sections_of(session, ticket_id):
        session.delete(section)
    ticket.status = ANALYZING
    ticket.current_step = "Queued"
    ticket.error_message = None
    ticket.handoff, ticket.handoff_state, ticket.summary = {}, HANDOFF_NOT_READY, None
    ticket.proposal_state, ticket.part3_handoff = "not_started", {}
    ticket.progress = [*ticket.progress, {"step": "reprocess", "detail": "Reprocessing requested", "at": now().isoformat()}]
    ticket.updated_at = now()
    session.add(ticket)
    session.commit()


def interrupt_stale(session: Session) -> int:
    """At startup: a ticket still `analyzing` belongs to a run that died with the previous process (background tasks do not survive a restart)."""
    models = _models()
    stale = list(session.exec(select(models.RfpTicket).where(models.RfpTicket.status == ANALYZING)))
    for ticket in stale:
        ticket.status = FAILED
        ticket.current_step = "Failed"
        ticket.error_message = "The analysis was interrupted when the service restarted. Reprocess it with `python scripts/rfp_intake.py reprocess <ticket_id>`."
        ticket.updated_at = now()
        session.add(ticket)
    session.commit()
    return len(stale)


def safe_filename(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "rfp.pdf").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).strip("-.") or "rfp.pdf"
    return name[:80]

