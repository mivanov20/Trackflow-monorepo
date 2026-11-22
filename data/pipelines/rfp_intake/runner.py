"""Runs the `rfp_intake` graph for one ticket and persists what happens, step by step.

`run_intake(ticket_id, engine)` is what the API schedules in the background after answering the upload, and what `scripts/rfp_intake.py` calls to reprocess a ticket.
It never raises: whatever goes wrong ends as a ticket in `failed` with a message, so a bad PDF cannot take the service down or leave a ticket `analyzing` forever.
Each ticket runs on its own; a rejection or a failure in one has no effect on the others.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from sqlmodel import Session

from . import store
from .config import ROOT
from .convert import ConversionError
from .graph import WORKER_NODES, compile_graph

logger = logging.getLogger("trackflow.rfp_intake")

GENERIC_FAILURE = "The analysis failed because of an internal error. Try uploading the document again, or ask the technical team to reprocess it."

# what the user sees while the NEXT node runs, keyed by the node that just finished
NEXT_LABEL = {
    "convert": "Classifying the document",
    "classify": "Reading the RFP's metadata and readability",
    "analyze": "Splitting the work by department",
    "orchestrate": "Departments are analysing their part",
    "discard": "Discarding",
    "synthesize": "Writing the summary",
}


@lru_cache(maxsize=1)
def graph():
    return compile_graph()


def resolve_pdf(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def run_intake(ticket_id: str, engine) -> str:
    """Returns the final status of the ticket (`intake_complete`, `discarded` or `failed`)."""
    try:
        with Session(engine) as session:
            ticket = store.get_ticket(session, ticket_id)
            if ticket is None:
                logger.error("rfp intake: ticket %s does not exist", ticket_id)
                return "missing"
            initial = {"ticket_id": ticket.ticket_id, "rfp_id": ticket.rfp_id, "pdf_path": str(resolve_pdf(ticket.raw_pdf_path)), "sections": [], "steps": []}
        state: dict = {"steps": [], "sections": []}
        pending_workers = 0
        for update in graph().stream(initial, stream_mode="updates"):
            for node, output in update.items():
                output = output or {}
                for key, value in output.items():
                    if key in ("steps", "sections"):
                        state[key] = [*state.get(key, []), *value]
                    else:
                        state[key] = value
                label = NEXT_LABEL.get(node)
                if node in WORKER_NODES.values():
                    pending_workers = max(0, pending_workers - 1)
                    label = "Departments are analysing their part" if pending_workers else "Writing the summary"
                elif node == "orchestrate":
                    pending_workers = len(output.get("subtasks", []))
                    label = "Departments are analysing their part" if pending_workers else "Writing the summary"
                with Session(engine) as session:
                    store.record_step(session, ticket_id, label or "Working", output.get("steps", []))
        with Session(engine) as session:
            store.finish_ticket(session, ticket_id, state)
            return store.get_ticket(session, ticket_id).status
    except ConversionError as error:
        logger.warning("rfp intake: ticket %s could not be converted (%s)", ticket_id, type(error.__cause__).__name__ if error.__cause__ else "no text")
        return _fail(engine, ticket_id, str(error))
    except Exception:  # noqa: BLE001 - the background task must end in a visible state whatever broke
        logger.exception("rfp intake: ticket %s failed", ticket_id)
        return _fail(engine, ticket_id, GENERIC_FAILURE)


def _fail(engine, ticket_id: str, message: str) -> str:
    try:
        with Session(engine) as session:
            store.fail_ticket(session, ticket_id, message)
    except Exception:  # noqa: BLE001 - nothing else can be done; the log has it
        logger.exception("rfp intake: ticket %s could not be marked as failed", ticket_id)
    return "failed"
