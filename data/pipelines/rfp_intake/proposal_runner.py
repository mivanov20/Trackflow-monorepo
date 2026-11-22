"""Runs the `rfp_drafting` graph for one ticket that finished the intake, and persists the progress as it happens.

`run_proposal(ticket_id, engine)` reads Part 1's routing handoff (`rfp_tickets.handoff`, flagged `ready_for_part_2`): the generators get the ticket id, the client data and each department's
`key_aspects` from it. The PDF and its Markdown are not opened. It never raises: a failure puts the ticket back to `intake_complete` with `proposal_state = failed` and the reason, so the
intake result is intact and the drafting can be started again. One ticket's problem does not touch the others.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from sqlmodel import Session

from . import drafting_graph, proposal_store, store
from .config import HANDOFF_READY, INTAKE_COMPLETE

logger = logging.getLogger("trackflow.rfp_intake")

GENERIC_FAILURE = "The proposal drafting failed because of an internal error. Start it again, or ask the technical team."


def max_iterations() -> int:
    """The bound of the generator-evaluator loop (attempts per section, the first draft included). `RFP_MAX_DRAFT_ITERATIONS`, default 3."""
    try:
        return max(1, int(os.getenv("RFP_MAX_DRAFT_ITERATIONS", drafting_graph.DEFAULT_MAX_ITERATIONS)))
    except ValueError:
        return drafting_graph.DEFAULT_MAX_ITERATIONS


@lru_cache(maxsize=1)
def graph():
    return drafting_graph.compile_graph()


def can_start(ticket) -> str | None:
    """None when the ticket may start drafting, otherwise why not."""
    if ticket is None:
        return "The ticket does not exist."
    if ticket.status != INTAKE_COMPLETE or ticket.handoff_state != HANDOFF_READY or not ticket.handoff:
        return "Only a ticket with a finished intake (intake_complete) can start the proposal."
    return None


def run_proposal(ticket_id: str, engine, *, facts: dict | None = None) -> str:
    """Returns the final status of the ticket (`needs_human_review`, `under_evaluation` when every section passed, or `intake_complete` when it could not run)."""
    try:
        with Session(engine) as session:
            ticket = store.get_ticket(session, ticket_id)
            reason = can_start(ticket)
            if reason:
                logger.warning("rfp proposal: ticket %s not started: %s", ticket_id, reason)
                return "not_started"
            handoff = dict(ticket.handoff)
            proposal_store.begin(session, ticket_id)
        state = graph().invoke(
            {
                "ticket_id": ticket_id,
                "handoff": handoff,
                "facts": facts if facts is not None else drafting_graph.facts_state(handoff["client"].get("country")),
                "max_iterations": max_iterations(),
                "sections": {},
                "steps": [],
            },
            {"configurable": {"reporter": proposal_store.DbReporter(engine, ticket_id)}},
        )
        with Session(engine) as session:
            return proposal_store.finish(session, ticket_id, state["part3_handoff"])
    except Exception:  # noqa: BLE001 - the background task must end in a visible state whatever broke
        logger.exception("rfp proposal: ticket %s failed", ticket_id)
        try:
            with Session(engine) as session:
                proposal_store.fail(session, ticket_id, GENERIC_FAILURE)
        except Exception:  # noqa: BLE001
            logger.exception("rfp proposal: ticket %s could not be marked as failed", ticket_id)
        return "intake_complete"

