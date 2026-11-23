"""RFP intake endpoints (Milestone 9, part 1): upload a PDF, follow the ticket, read the result.

This router only receives, stores, triggers and reads. The graph, the agents and the persistence rules live in `data/pipelines/rfp_intake/`:

    POST /rfp/tickets            multipart `file` (a PDF). Answers 202 at once with the ticket (status `analyzing`); the pipeline runs after the response.
    GET  /rfp/tickets            the latest tickets, newest first (what the backoffice list polls)
    GET  /rfp/tickets/{id}       one ticket: status, progress, metadata, readability, per-department key aspects, the Sales summary and the handoff for Part 2

    POST /rfp/tickets/{id}/proposal   (re)starts the proposal drafting of a ticket whose intake is complete (Part 2); it also starts by itself after the intake unless RFP_AUTO_DRAFT=false
    POST /rfp/tickets/{id}/approval   opens the approval gate of every department (Part 3); it also happens by itself after the drafting unless RFP_AUTO_APPROVAL=false
    POST /rfp/tickets/{id}/sections/{department}/decision   the owner's approve / request_changes / reject: resumes THAT department's paused branch
    GET  /rfp/tickets/{id}/document   the final document, once every department approved (`?format=markdown` downloads it)
    GET  /rfp/tickets/{id}/trace      which agent did what, in what order, across the three parts

Same API and same database as the rest of TrackFlow: no second service.
"""

import logging
import os
from datetime import timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from data.pipelines.rfp_intake import DEPARTMENTS, run_intake
from data.pipelines.rfp_intake import approval_runner, approval_store, proposal_runner, proposal_store, store, trace
from data.pipelines.rfp_intake.config import MAX_UPLOAD_BYTES, raw_dir, relative_to_root

from ..accounts import Role, User
from ..auth import get_current_user, require_staff
from ..ratelimit import limit_model_calls
from ..database import get_session

logger = logging.getLogger("trackflow.rfp")
router = APIRouter(prefix="/rfp", tags=["rfp"], dependencies=[Depends(require_staff)])

PDF_MAGIC = b"%PDF-"


def auto_draft() -> bool:
    """After the intake, the proposal drafting starts by itself (the workflow of Milestone 9). `RFP_AUTO_DRAFT=false` leaves the ticket at `intake_complete`."""
    return os.getenv("RFP_AUTO_DRAFT", "true").lower() not in ("0", "false", "no")


def auto_approval() -> bool:
    """After the drafting, the approval gates of the departments open by themselves. `RFP_AUTO_APPROVAL=false` leaves the ticket ready for approval until someone starts it."""
    return os.getenv("RFP_AUTO_APPROVAL", "true").lower() not in ("0", "false", "no")


def approver_roles() -> set[str]:
    """Accounts that may register a department owner's decision (the owners are named people, the backoffice accounts are staff): `RFP_APPROVER_ROLES`, default admin and manager."""
    return {role.strip() for role in os.getenv("RFP_APPROVER_ROLES", "admin,manager").split(",") if role.strip()}


def start_approvals(ticket_id: str, engine) -> None:
    from sqlmodel import Session

    with Session(engine) as session:
        ticket = store.get_ticket(session, ticket_id)
        if approval_runner.can_start(ticket) is not None:
            return
    approval_store.mark_queued(engine, ticket_id)
    try:
        approval_runner.workflow_for(engine).start(ticket_id)
    except Exception:  # noqa: BLE001 - the drafting result is intact; the approvals can be started by hand
        logger.exception("rfp approval: ticket %s could not open its approval gates", ticket_id)
        approval_store.mark_queued(engine, ticket_id, "not_started")


def process_ticket(ticket_id: str, engine) -> None:
    """The background task of an upload: Part 1 (intake), Part 2 (proposal drafting) when the intake completed, and the opening of Part 3's approval gates when the drafting is ready."""
    from sqlmodel import Session

    run_intake(ticket_id, engine)
    if not auto_draft():
        return
    with Session(engine) as session:
        ticket = store.get_ticket(session, ticket_id)
        if proposal_runner.can_start(ticket) is not None:
            return
        proposal_store.mark_queued(session, ticket_id)
    proposal_runner.run_proposal(ticket_id, engine)
    if auto_approval():
        start_approvals(ticket_id, engine)


def _iso(value) -> str | None:
    """ISO 8601 with its offset: a time the database returns without a time zone (SQLite) is UTC, and says so, so a browser does not read it as local time."""
    if not value:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def ticket_summary(ticket) -> dict[str, Any]:
    """What the list shows: enough to see the state of each ticket without opening it."""
    return {
        "ticket_id": ticket.ticket_id,
        "rfp_id": ticket.rfp_id,
        "status": ticket.status,
        "current_step": ticket.current_step,
        "original_filename": ticket.original_filename,
        "client_name": ticket.client_name,
        "client_country": ticket.client_country,
        "departments_needed": ticket.departments_needed,
        "document_type": ticket.document_type,
        "proposal_state": ticket.proposal_state,
        "approval_state": ticket.approval_state,
        "created_at": _iso(ticket.created_at),
        "updated_at": _iso(ticket.updated_at),
    }


def ticket_detail(ticket, sections) -> dict[str, Any]:
    """Everything Sales needs, without opening the original document."""
    return {
        **ticket_summary(ticket),
        "raw_pdf_path": ticket.raw_pdf_path,
        "progress": ticket.progress,
        "error_message": ticket.error_message,
        "engine": ticket.engine,
        "classification": {
            "is_rfp": ticket.is_rfp,
            "document_type": ticket.document_type,
            "reason": ticket.classification_reason,
            "confidence": ticket.classification_confidence,
        },
        "metadata": {
            "client_name": ticket.client_name,
            "client_country": ticket.client_country,
            "currency": ticket.currency,
            "client_contact": ticket.client_contact,
            "services_requested": ticket.services_requested,
            "monthly_volume": ticket.monthly_volume,
            "deadline": ticket.deadline,
            "budget_range": ticket.budget_range,
            "departments_needed": ticket.departments_needed,
            "extra": ticket.metadata_extra,
        },
        "open_questions": ticket.open_questions,
        "readability": ticket.readability,
        "summary": ticket.summary,
        "sections": [
            {
                "department_id": section.department_id,
                "department": DEPARTMENTS[section.department_id]["name"],
                "owner": section.owner,
                "key_aspects": section.key_aspects,
                "approval_status": section.approval_status,
                "engine": section.engine,
                "draft_status": section.draft_status,
                "iterations": section.iterations,
                "draft_engine": section.draft_engine,
                "draft_content": section.draft_content,
                "evaluation_result": section.evaluation_results or None,
                "approval_round": section.approval_round,
                "approver": section.approver,
                "approved_at": _iso(section.approved_at),
                "approval_comment": section.approval_comment,
                "approval_history": section.approval_history or [],
                "commitments": section.commitments or {},
                "awaiting_approval": ticket.approval_state == "waiting" and section.approval_status == "pending",
                "responsible": DEPARTMENTS[section.department_id]["owner"],
            }
            for section in sections
        ],
        "handoff_state": ticket.handoff_state,
        "handoff": ticket.handoff,
        "part3_handoff": ticket.part3_handoff,
        "document_ready": ticket.status == "done",
    }


@router.post("/tickets", status_code=status.HTTP_202_ACCEPTED)
async def upload_rfp(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(limit_model_calls),  # the analysis runs several model calls: counted against the per-user limit (services/ratelimit.py)
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Stores the PDF under `data/raw/rfp/`, creates the ticket as `analyzing`, and returns. The analysis runs in the background; poll `GET /rfp/tickets/{id}`."""
    name = file.filename or ""
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Only PDF files are accepted.")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"The PDF is too large (the limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
    if not content.startswith(PDF_MAGIC):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="The file is not a valid PDF.")

    ticket_id = store.new_ticket_id()
    directory = raw_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{ticket_id}.pdf"  # the name is ours (never the client's): no path can be smuggled in through it
    target.write_bytes(content)
    try:
        ticket = store.create_ticket(session, ticket_id=ticket_id, raw_pdf_path=relative_to_root(target), original_filename=store.safe_filename(name), uploaded_by=user.id)
    except Exception:
        target.unlink(missing_ok=True)  # no ticket, no orphan file
        raise
    background_tasks.add_task(process_ticket, ticket_id, session.get_bind())
    logger.info("rfp ticket %s created (%d bytes); analysis scheduled", ticket_id, len(content))
    return ticket_summary(ticket)


@router.get("/tickets")
def list_rfp_tickets(limit: int = Query(default=50, ge=1, le=200), user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [ticket_summary(ticket) for ticket in store.list_tickets(session, limit)]


@router.get("/tickets/{ticket_id}")
def get_rfp_ticket(ticket_id: str, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict[str, Any]:
    ticket = store.get_ticket(session, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The ticket was not found.")
    return ticket_detail(ticket, store.sections_of(session, ticket_id))


@router.post("/tickets/{ticket_id}/proposal", status_code=status.HTTP_202_ACCEPTED)
def start_proposal(ticket_id: str, background_tasks: BackgroundTasks, user: User = Depends(limit_model_calls), session: Session = Depends(get_session)) -> dict[str, Any]:
    """Starts (or restarts, after a failure) the proposal drafting of a ticket whose intake is complete. Answers at once; the drafting runs in the background."""
    ticket = store.get_ticket(session, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The ticket was not found.")
    if proposal_runner.can_start(ticket) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The proposal can only be drafted for a ticket whose intake is complete.")
    proposal_store.mark_queued(session, ticket_id)
    background_tasks.add_task(proposal_runner.run_proposal, ticket_id, session.get_bind())
    session.refresh(ticket)
    return ticket_summary(ticket)


class DecisionBody(BaseModel):
    """The owner's decision on a section. What it may contain is checked by `approval.validate_decision` before the paused branch is resumed."""

    decision: str = Field(max_length=32)
    approver: str = Field(max_length=120)
    comment: str = Field(default="", max_length=4000)
    edited_content: str | None = Field(default=None, max_length=30000)
    commitments: dict | None = None


@router.post("/tickets/{ticket_id}/approval", status_code=status.HTTP_202_ACCEPTED)
def open_approvals(ticket_id: str, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict[str, Any]:
    """Opens the approval gate of every department of a ticket whose proposal sections are ready."""
    ticket = store.get_ticket(session, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The ticket was not found.")
    reason = approval_runner.can_start(ticket)
    if reason:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=reason)
    approval_runner.workflow_for(session.get_bind()).start(ticket_id)
    session.refresh(ticket)
    return ticket_summary(ticket)


@router.post("/tickets/{ticket_id}/sections/{department_id}/decision")
def decide_section(ticket_id: str, department_id: str, body: DecisionBody, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict[str, Any]:
    """Registers a department owner's decision and resumes that department's branch. Only staff accounts may register decisions; the named owner of the department is the approver."""
    if user.role.value not in approver_roles():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only a staff account can register a department owner's decision.")
    raw = body.model_dump(exclude_none=True)
    try:
        outcome = approval_runner.workflow_for(session.get_bind()).submit(ticket_id, department_id, raw, decided_by=user.id)
    except approval_runner.ApprovalError as error:
        code = {"not_found": status.HTTP_404_NOT_FOUND, "conflict": status.HTTP_409_CONFLICT}.get(error.kind, status.HTTP_422_UNPROCESSABLE_ENTITY)
        raise HTTPException(status_code=code, detail=str(error)) from None
    session.expire_all()
    ticket = store.get_ticket(session, ticket_id)
    return {**ticket_summary(ticket), **outcome}


@router.get("/tickets/{ticket_id}/document")
def get_final_document(ticket_id: str, format: str = Query(default="json", pattern="^(json|markdown)$"), user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    """The final document, available once the ticket is `done`."""
    document = approval_store.get_final_document(session, ticket_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The final document is not ready: every department has to approve its section first.")
    if format == "markdown":
        return PlainTextResponse(document.content_markdown, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="{document.rfp_id}-proposal.md"'})
    return {"ticket_id": document.ticket_id, "rfp_id": document.rfp_id, "currency": document.currency, "generated_at": _iso(document.generated_at), "sections": document.sections, "content_markdown": document.content_markdown}


@router.get("/tickets/{ticket_id}/trace")
def get_trace(ticket_id: str, part: int | None = Query(default=None, ge=1, le=3), user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict[str, Any]:
    """Which agent did what, in what order: one timeline across the three parts."""
    if store.get_ticket(session, ticket_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The ticket was not found.")
    return {"ticket_id": ticket_id, "events": trace.events(session, ticket_id, part)}
