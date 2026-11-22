"""A reproducible run of the whole workflow, Parts 1, 2 and 3, with SIMULATED human decisions: what the end-to-end test and `scripts/rfp_e2e.py` drive.

    run(pdf_path, scenario="approve")

Part 1 (intake of the PDF) and Part 2 (generation and evaluation) run for real; Part 3 opens the approval gates and the "owners" decide programmatically through the same resume entry point the API
uses (`Workflow.submit`), so a reviewer does not depend on clicking through the UI. Everything is in memory unless a database engine and a checkpointer are given.

Scenarios:
    approve            every owner approves at once
    request_changes    the warehouse owner asks for a change first (the section is redrafted and comes back to her), then every owner approves
    volume_conflict    the warehouse owner commits capacity below the volume last mile assumes (RFPs with both departments): the arbiter caps the volume and sends last mile back
"""

from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from . import approval_store, store, trace
from .approval_runner import Workflow
from .config import DEPARTMENTS
from .proposal_runner import run_proposal
from .runner import run_intake

SCENARIOS = ("approve", "request_changes", "volume_conflict")


def new_engine():
    from services import models  # noqa: F401  (registers the tables)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


def status_timeline(engine, ticket_id: str) -> list[str]:
    """The statuses the ticket went through, in order, from the trace (the upload creates it as `analyzing`)."""
    with Session(engine) as session:
        return ["analyzing", *[event["output"]["to"] for event in trace.events(session, ticket_id) if event["node"] == "status"]]


def _decision(department_id: str, kind: str = "approve", **extra) -> dict:
    return {"decision": kind, "approver": DEPARTMENTS[department_id]["owner"], "comment": extra.pop("comment", ""), **extra}


def run(pdf_path: str | Path, scenario: str = "approve", engine=None, saver=None, ticket_id: str = "e2e-1") -> dict:
    if scenario not in SCENARIOS:
        raise ValueError(f"scenario must be one of {SCENARIOS}")
    engine = engine or new_engine()
    with Session(engine) as session:
        store.create_ticket(session, ticket_id=ticket_id, raw_pdf_path=str(Path(pdf_path).resolve()), original_filename=Path(pdf_path).name, uploaded_by="e2e")
    intake = run_intake(ticket_id, engine)
    result: dict = {"ticket_id": ticket_id, "intake": intake}
    if intake == "intake_complete":
        result["proposal"] = run_proposal(ticket_id, engine)
        workflow = Workflow(engine, saver or MemorySaver())  # in memory unless a checkpointer is given: a simulated run must not inherit the paused state of an earlier one
        workflow.start(ticket_id)
        departments = [section["department_id"] for section in approval_store.sections_snapshot(engine, ticket_id)]
        if scenario == "request_changes":
            workflow.submit(ticket_id, departments[0], _decision(departments[0], "request_changes", comment="Please add our onboarding assumptions to the section."))
        if scenario == "volume_conflict" and "warehouse" in departments and "lastmile" in departments:
            workflow.submit(ticket_id, "warehouse", _decision("warehouse", commitments={"capacity_orders_per_month": 3000}))
        for department_id in departments:
            for _ in range(10):  # a bound, like every loop of the workflow: an owner approves until nothing of hers is pending
                if not any(s["department_id"] == department_id and s["approval_status"] == "pending" for s in approval_store.sections_snapshot(engine, ticket_id)):
                    break
                workflow.submit(ticket_id, department_id, _decision(department_id))
    with Session(engine) as session:
        ticket = store.get_ticket(session, ticket_id)
        document = approval_store.get_final_document(session, ticket_id)
        events = trace.events(session, ticket_id)
        result |= {
            "status": ticket.status,
            "approval_state": ticket.approval_state,
            "timeline": status_timeline(engine, ticket_id),
            "events": events,
            "document": document.content_markdown if document else None,
            "departments": list(ticket.departments_needed),
        }
    result["engine"] = engine
    return result
