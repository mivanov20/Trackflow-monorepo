"""Persistence of Part 3: approval decisions, revised drafts, the final document and the ticket's status, in the application database.

`status` follows the approval: `waiting_for_approval` while any section is pending a human decision, `needs_human_review` when the flow is blocked (a section ran out of rounds or a conflict stayed unresolved
after the limit), `done` when the final document is stored. `approval_state` (`not_started`, `waiting`, `blocked`, `complete`) says where Part 3 is without inventing statuses. Every status change is also an event
of the trace, so the status timeline of a ticket can be read (and tested) as one sequence across the three parts.
"""

from __future__ import annotations

from sqlmodel import Session

from . import store, trace
from .dblock import LOCK as _lock
from .store import now

WAITING, NEEDS_REVIEW, DONE = "waiting_for_approval", "needs_human_review", "done"
# `_lock` (dblock): one access at a time from the parallel department threads


def set_status(engine, ticket_id: str, status: str, part: int, reason: str, **fields) -> None:
    """Changes the ticket's status (and other fields) and records the transition in the trace when the status actually changes."""
    with _lock, Session(engine) as session:
        ticket = store.get_ticket(session, ticket_id)
        old = ticket.status
        ticket.status = status
        for key, value in fields.items():
            setattr(ticket, key, value)
        ticket.updated_at = now()
        session.add(ticket)
        session.commit()
    if old != status:
        trace.record(engine, ticket_id, part, "ticket", "status", {"from": old, "reason": reason}, {"to": status})


def mark_queued(engine, ticket_id: str, state: str = "queued") -> None:
    with _lock, Session(engine) as session:
        ticket = store.get_ticket(session, ticket_id)
        ticket.approval_state = state
        ticket.updated_at = now()
        session.add(ticket)
        session.commit()


def _section(session: Session, ticket_id: str, department_id: str):
    return next(section for section in store.sections_of(session, ticket_id) if section.department_id == department_id)


def sections_snapshot(engine, ticket_id: str) -> list[dict]:
    with _lock, Session(engine) as session:
        return [
            {
                "department_id": s.department_id,
                "owner": s.owner,
                "content": s.draft_content or "",
                "evaluation_result": s.evaluation_results or {},
                "approval_status": s.approval_status,
                "approver": s.approver,
                "approved_at": s.approved_at.isoformat() if s.approved_at else None,
                "approval_round": s.approval_round,
                "commitments": dict(s.commitments or {}),
                "draft_status": s.draft_status,
            }
            for s in store.sections_of(session, ticket_id)
        ]


def open_gate(engine, ticket_id: str, department_id: str, round_number: int) -> None:
    with _lock, Session(engine) as session:
        section = _section(session, ticket_id, department_id)
        section.approval_status, section.approval_round = "pending", round_number
        session.add(section)
        session.commit()


def record_decision(engine, ticket_id: str, department_id: str, round_number: int, decision: dict, decided_by: str | None) -> None:
    """The owner's decision: history, comment, who registered it, and (on approval) the approver and the time. A request for changes leaves the section `pending` until its new draft is ready."""
    with _lock, Session(engine) as session:
        section = _section(session, ticket_id, department_id)
        entry = {"round": round_number, "decision": decision["decision"], "comment": decision["comment"], "approver": decision["approver"], "decided_by": decided_by, "at": now().isoformat()}
        section.approval_history = [*(section.approval_history or []), entry]
        section.approval_comment = decision["comment"] or None
        section.decided_by = decided_by
        if decision.get("commitments"):
            section.commitments = {**(section.commitments or {}), **decision["commitments"]}
        if decision["decision"] == "approve":
            section.approval_status, section.approver, section.approved_at = "approved", decision["approver"], now()
        else:
            section.approval_status, section.approver, section.approved_at = "pending", None, None
        session.add(section)
        session.commit()


def apply_revision(engine, ticket_id: str, department_id: str, content: str, evaluation_result: dict, draft_engine: str, extra_history: dict | None = None) -> None:
    """A new draft after changes were requested (by the owner or by the arbiter) replaces the old one, with its own evaluation; the earlier evaluations stay in its history."""
    with _lock, Session(engine) as session:
        section = _section(session, ticket_id, department_id)
        earlier = (section.evaluation_results or {}).get("history", [])
        if (section.evaluation_results or {}).get("section_id"):
            earlier = [*earlier, {key: section.evaluation_results[key] for key in ("iteration", "overall_pass", "feedback_for_generator") if key in section.evaluation_results}]
        section.draft_content, section.draft_engine = content, draft_engine
        section.iterations = (section.iterations or 0) + 1
        section.evaluation_results = {**evaluation_result, "history": earlier}
        section.draft_status = "passed" if evaluation_result["overall_pass"] else "needs_human_review"
        section.approval_status, section.approver, section.approved_at = "pending", None, None
        if extra_history:
            section.approval_history = [*(section.approval_history or []), extra_history]
        session.add(section)
        session.commit()


def update_commitments(engine, ticket_id: str, department_id: str, values: dict) -> None:
    with _lock, Session(engine) as session:
        section = _section(session, ticket_id, department_id)
        section.commitments = {**(section.commitments or {}), **values}
        session.add(section)
        session.commit()


def reject_section(engine, ticket_id: str, department_id: str, comment: str) -> None:
    with _lock, Session(engine) as session:
        section = _section(session, ticket_id, department_id)
        section.approval_status, section.approver, section.approved_at, section.approval_comment = "rejected", None, None, comment
        session.add(section)
        session.commit()


def store_final_document(engine, ticket_id: str, document: dict) -> None:
    """The final document and `done`, in one transaction: a ticket is never `done` without a document."""
    from services import models

    with _lock, Session(engine) as session:
        existing = session.get(models.RfpFinalDocument, ticket_id)
        if existing:
            session.delete(existing)
            session.flush()
        session.add(models.RfpFinalDocument(ticket_id=ticket_id, rfp_id=document["rfp_id"], currency=document["currency"], sections=document["sections"], content_markdown=document["content_markdown"], generated_at=now()))
        ticket = store.get_ticket(session, ticket_id)
        old = ticket.status
        ticket.status, ticket.approval_state, ticket.current_step, ticket.error_message = DONE, "complete", "Final document generated", None
        ticket.progress = [*ticket.progress, {"step": "done", "detail": "Every department approved; the final document was generated", "at": now().isoformat()}]
        ticket.updated_at = now()
        session.add(ticket)
        session.commit()
    if old != DONE:
        trace.record(engine, ticket_id, 3, "ticket", "status", {"from": old, "reason": "final document stored"}, {"to": DONE})


def get_final_document(session: Session, ticket_id: str):
    from services import models

    return session.get(models.RfpFinalDocument, ticket_id)
