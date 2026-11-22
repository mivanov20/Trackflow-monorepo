"""Runs Part 3: opens the approval gate of every active department, resumes a department when its owner decides, and settles the ticket when every branch is done.

    Workflow.start(ticket_id)                       Part 2 handed the ticket over (`ready_for_part_3`): one branch run per department, each interrupted at its approval gate;
                                                    the ticket becomes `waiting_for_approval`
    Workflow.submit(ticket_id, department, raw)     the explicit resume entry point: validates the decision, then `Command(resume=...)` on THAT department's thread only; the others are not touched
    Workflow.settle(ticket_id)                      when no branch is waiting any more, runs the coordinator (arbitration, then the final document); if the arbiter sent sections back,
                                                    reopens exactly those branches

Checkpoints: SQLite file (`RFP_CHECKPOINT_DB`, default `services/data/rfp-checkpoints.sqlite`; `:memory:` for tests), so a pause survives a restart. Thread ids are namespaced by ticket:
`rfp-{ticket_id}:{department_id}` for a department's branch and `rfp-{ticket_id}` for the coordinator, so concurrent tickets never share a checkpoint. A department's resume takes a lock of its own:
two owners deciding at the same time do not wait for each other; only `settle` is serialized per ticket.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import weakref
from collections import defaultdict
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from sqlmodel import Session

from . import approval, approval_graph, approval_store, drafting_graph, store, trace
from .dblock import LOCK
from .approval import DecisionError
from .config import DEPARTMENTS, ROOT

logger = logging.getLogger("trackflow.rfp_intake")

READY = "ready_for_part_3"
START_STATUSES = ("under_evaluation", "needs_human_review")
# rules the arbitration does not cover (it covers GL-1 and GL-3): an owner cannot approve a section that still breaks them, because the final document could never satisfy them
NOT_ARBITRATED = {"GL-2", "GL-4", "GL-5"}


class ApprovalError(Exception):
    """A request that cannot be honoured. `kind` is `not_found`, `conflict` or `invalid`; the message is written for the person who made the request."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def make_checkpointer(path: str | None = None):
    path = path if path is not None else os.getenv("RFP_CHECKPOINT_DB")
    if path == ":memory:":
        return MemorySaver()
    target = Path(path) if path else ROOT / "services" / "data" / "rfp-checkpoints.sqlite"
    target.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(str(target), check_same_thread=False))


def branch_thread(ticket_id: str, department_id: str) -> str:
    return f"rfp-{ticket_id}:{department_id}"


def coordinator_thread(ticket_id: str) -> str:
    return f"rfp-{ticket_id}"


def can_start(ticket) -> str | None:
    if ticket is None:
        return "The ticket does not exist."
    if ticket.approval_state not in ("not_started", "queued"):
        return "The approvals of this ticket have already started."
    if ticket.proposal_state != READY or ticket.status not in START_STATUSES:
        return "Only a ticket whose proposal sections are ready (under_evaluation or needs_human_review) can start the approvals."
    return None


class Workflow:
    def __init__(self, engine, saver=None):
        self.engine = engine
        self.saver = saver or make_checkpointer()
        self.branch = approval_graph.build_branch_graph(engine).compile(checkpointer=self.saver)
        self.coordinator = approval_graph.build_coordinator_graph(engine, self.brief_for).compile(checkpointer=self.saver)
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._guard = threading.Lock()

    # ------------------------------------------------------------------------------------------------ helpers
    def _lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks[key]

    def _ticket(self, ticket_id: str):
        with LOCK, Session(self.engine) as session:
            ticket = store.get_ticket(session, ticket_id)
            if ticket is not None:
                session.expunge(ticket)
            return ticket

    def brief_for(self, ticket_id: str, department_id: str) -> tuple[dict, dict]:
        """The generator brief of a section (from the Part 1 handoff) with the commitments of Part 3 applied: a capped volume and the notes of the arbiter."""
        ticket = self._ticket(ticket_id)
        brief = next(b for b in drafting_graph.build_briefs(ticket.handoff) if b["department_id"] == department_id)
        commitments = next(s for s in approval_store.sections_snapshot(self.engine, ticket_id) if s["department_id"] == department_id)["commitments"]
        if commitments.get("volume_cap"):
            brief = {**brief, "rfp_monthly_volume": brief["monthly_volume"], "monthly_volume": commitments["volume_cap"]}
        if commitments.get("arbitration_notes"):
            brief = {**brief, "arbitration_notes": commitments["arbitration_notes"]}
        return brief, drafting_graph.facts_state(ticket.handoff["client"].get("country"))

    def _config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def is_waiting(self, ticket_id: str, department_id: str) -> bool:
        return bool(self.branch.get_state(self._config(branch_thread(ticket_id, department_id))).next)

    def waiting_departments(self, ticket_id: str) -> list[str]:
        """The departments whose owner still has to decide. The database is the truth about that (a section is `pending` until it is approved or rejected, also while it is being rewritten);
        the checkpoint is what makes the decision resumable (`is_waiting`)."""
        return [section["department_id"] for section in approval_store.sections_snapshot(self.engine, ticket_id) if section["approval_status"] == "pending"]

    def pending_payload(self, ticket_id: str, department_id: str) -> dict | None:
        """What the interrupted branch is waiting for (the approval ticket of the department), read from its checkpoint."""
        state = self.branch.get_state(self._config(branch_thread(ticket_id, department_id)))
        for task in state.tasks:
            for interruption in task.interrupts:
                return interruption.value
        return None

    def _open_branch(self, ticket_id: str, department_id: str) -> None:
        section = next(s for s in approval_store.sections_snapshot(self.engine, ticket_id) if s["department_id"] == department_id)
        brief, facts = self.brief_for(ticket_id, department_id)
        with self._lock(f"{ticket_id}:{department_id}"):
            self.branch.invoke(
                {
                    "ticket_id": ticket_id,
                    "department_id": department_id,
                    "brief": brief,
                    "facts": facts,
                    "content": section["content"],
                    "evaluation_result": section["evaluation_result"],
                    "round": section["approval_round"] + 1,
                    "max_rounds": approval.max_rounds(),
                    "max_iterations": approval.max_rounds(),
                    "decision": None,
                    "decided_by": None,
                    "error": None,
                    "status": "starting",
                },
                self._config(branch_thread(ticket_id, department_id)),
            )

    def _label(self, ticket_id: str) -> None:
        waiting = self.waiting_departments(ticket_id)
        owners = ", ".join(DEPARTMENTS[d]["owner"] for d in waiting)
        approval_store.set_status(self.engine, ticket_id, approval_store.WAITING, 3, "approval pending", approval_state="waiting", current_step=f"Waiting for approval: {owners}" if owners else "Waiting for the arbitration to settle")

    # ------------------------------------------------------------------------------------------------ entry points
    def start(self, ticket_id: str) -> None:
        ticket = self._ticket(ticket_id)
        reason = can_start(ticket)
        if reason:
            raise ApprovalError("conflict" if ticket else "not_found", reason)
        departments = [section["department_id"] for section in approval_store.sections_snapshot(self.engine, ticket_id)]
        approval_store.set_status(self.engine, ticket_id, approval_store.WAITING, 3, "part 2 handed the sections over", approval_state="waiting", error_message=None)
        trace.record(self.engine, ticket_id, 3, "approval-orchestrator", "start", {"from_status": ticket.status, "part3_handoff_sections": departments, "needs_human_review": ticket.part3_handoff.get("needs_human_review")}, {"gates_opened": departments})
        for department_id in departments:
            self._open_branch(ticket_id, department_id)
        self._label(ticket_id)

    def check_approvable(self, section: dict) -> None:
        violated = set((section["evaluation_result"].get("compliance") or {}).get("rule_ids", [])) & NOT_ARBITRATED
        if violated:
            raise ApprovalError("invalid", f"The section still breaks company guidelines ({', '.join(sorted(violated))}): request changes with an edited draft instead of approving it.")

    def submit(self, ticket_id: str, department_id: str, raw, decided_by: str | None = None) -> dict:
        """The resume entry point. Returns `{department_id, decision, waiting, ticket_status}` after the department's branch (and, if it was the last one, the coordinator) ran."""
        ticket = self._ticket(ticket_id)
        if ticket is None:
            raise ApprovalError("not_found", "The ticket was not found.")
        if department_id not in DEPARTMENTS:
            raise ApprovalError("not_found", "Unknown department.")
        if ticket.approval_state != "waiting":
            raise ApprovalError("conflict", "This ticket is not waiting for approvals.")
        section = next((s for s in approval_store.sections_snapshot(self.engine, ticket_id) if s["department_id"] == department_id), None)
        if section is None:
            raise ApprovalError("not_found", "This department has no section in the ticket.")
        if section["approval_status"] != "pending":
            raise ApprovalError("conflict", f"The {department_id} section is already {section['approval_status']}.")
        try:
            decision = approval.validate_decision(raw, department_id)  # validated BEFORE it can reach the graph
        except DecisionError as error:
            raise ApprovalError("invalid", str(error)) from None
        if decision["decision"] == "approve":
            self.check_approvable(section)
        with self._lock(f"{ticket_id}:{department_id}"):
            if not self.is_waiting(ticket_id, department_id):  # the checkpoint is gone (restored database, new checkpoint file): rebuild the gate from the section and carry on
                logger.warning("rfp approval: no checkpoint for %s, reopening its gate", branch_thread(ticket_id, department_id))
                self._reopen_unlocked(ticket_id, department_id)
            self.branch.invoke(Command(resume={**decision, "decided_by": decided_by}), self._config(branch_thread(ticket_id, department_id)))
        self.settle(ticket_id)
        return {"department_id": department_id, "decision": decision["decision"], "waiting": self.waiting_departments(ticket_id), "ticket_status": self._ticket(ticket_id).status}

    def _reopen_unlocked(self, ticket_id: str, department_id: str) -> None:
        section = next(s for s in approval_store.sections_snapshot(self.engine, ticket_id) if s["department_id"] == department_id)
        brief, facts = self.brief_for(ticket_id, department_id)
        self.branch.invoke(
            {"ticket_id": ticket_id, "department_id": department_id, "brief": brief, "facts": facts, "content": section["content"], "evaluation_result": section["evaluation_result"], "round": max(1, section["approval_round"]),
             "max_rounds": approval.max_rounds(), "max_iterations": approval.max_rounds(), "decision": None, "decided_by": None, "error": None, "status": "starting"},
            self._config(branch_thread(ticket_id, department_id)),
        )

    def settle(self, ticket_id: str) -> None:
        """Runs the coordinator once every branch is done; reopens the branches the arbiter sent back. Serialized per ticket and idempotent."""
        with self._lock(ticket_id):
            for _ in range(approval.max_arbitration_rounds() + 3):
                ticket = self._ticket(ticket_id)
                if ticket.approval_state in ("complete", "blocked"):
                    return
                if self.waiting_departments(ticket_id):
                    self._label(ticket_id)
                    return
                result = self.coordinator.invoke({"ticket_id": ticket_id}, self._config(coordinator_thread(ticket_id)))
                if result.get("outcome") != "reopened":
                    return
                for department_id in result["reopen"]:
                    self._open_branch(ticket_id, department_id)


_workflows: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_registry_lock = threading.Lock()


def workflow_for(engine) -> Workflow:
    """One workflow (graphs + checkpointer + locks) per database engine, shared by every request."""
    with _registry_lock:
        if engine not in _workflows:
            _workflows[engine] = Workflow(engine)
        return _workflows[engine]
