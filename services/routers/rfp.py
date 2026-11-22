"""RFP intake endpoints (Milestone 9, part 1): upload a PDF, follow the ticket, read the result.

This router only receives, stores, triggers and reads. The graph, the agents and the persistence rules live in `data/pipelines/rfp_intake/`:

    POST /rfp/tickets            multipart `file` (a PDF). Answers 202 at once with the ticket (status `analyzing`); the pipeline runs after the response.
    GET  /rfp/tickets            the latest tickets, newest first (what the backoffice list polls)
    GET  /rfp/tickets/{id}       one ticket: status, progress, metadata, readability, per-department key aspects, the Sales summary and the handoff for Part 2

Same API and same database as the rest of TrackFlow: no second service.
"""

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from sqlmodel import Session

from data.pipelines.rfp_intake import DEPARTMENTS, run_intake
from data.pipelines.rfp_intake import store
from data.pipelines.rfp_intake.config import MAX_UPLOAD_BYTES, raw_dir, relative_to_root

from ..accounts import User
from ..auth import get_current_user
from ..database import get_session

logger = logging.getLogger("trackflow.rfp")
router = APIRouter(prefix="/rfp", tags=["rfp"])

PDF_MAGIC = b"%PDF-"


def _iso(value) -> str | None:
    return value.isoformat() if value else None


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
            }
            for section in sections
        ],
        "handoff_state": ticket.handoff_state,
        "handoff": ticket.handoff,
    }


@router.post("/tickets", status_code=status.HTTP_202_ACCEPTED)
async def upload_rfp(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
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
    background_tasks.add_task(run_intake, ticket_id, session.get_bind())
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
