"""The `rfp_approval` graphs of Part 3: a human-in-the-loop branch per department, and a coordinator with the arbitration node.

DEPARTMENT BRANCH (one graph run per department, thread `rfp-{ticket_id}:{department_id}`, checkpointed)

    START -> open_gate -> human_approval --INTERRUPT: the run stops here and its state is saved--> (resume with the owner's decision)
                 ^                |  invalid decision: asks again
                 |                v
                 |         record_decision --approve--------------------------> finish_approved -> END
                 |                |--request changes / reject, rounds left----> revise ----+
                 +----------------+--the limit of rounds is reached-----------> finish_rejected -> END

COORDINATOR (one graph run each time every branch has settled, thread `rfp-{ticket_id}`, checkpointed)

    START -> collect -> arbitrate --no conflict, every section approved--> finalize -> END   (final document stored, ticket `done`)
                           |--conflicts, rounds left: the arbiter's rule rewrites sections and sends them back to their branch -> END (outcome `reopened`)
                           |--a rejected section, or conflicts after the limit -> END (outcome `blocked`)

 * The pause is per department: each branch is its own run with its own checkpoint, so approving B while A is interrupted neither waits for A nor blocks it (tested).
 * `human_approval` is the only node that interrupts, and everything before the `interrupt()` call in it is free of side effects: LangGraph re-runs a node from its start on resume.
 * Resume is an explicit entry point (`approval_runner.Workflow.submit`): `Command(resume=decision)` on the department's thread, never a restart of the flow.
 * Both loops are bounded in code: `approval.max_rounds()` per section and `approval.max_arbitration_rounds()` for the arbitration.
 * Every node writes a trace event (agent, input, output, time).
"""

from __future__ import annotations

from typing import Annotated, TypedDict
import operator

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import approval, approval_store, arbitration, final_document, revision, rules, store, trace
from .approval import DecisionError
from .config import DEPARTMENTS, GUIDELINES
from .dblock import LOCK


# ------------------------------------------------------------------------------------------------ department branch
class BranchState(TypedDict, total=False):
    ticket_id: str
    department_id: str
    brief: dict
    facts: dict
    content: str
    evaluation_result: dict
    round: int
    max_rounds: int
    max_iterations: int
    decision: dict | None
    decided_by: str | None
    error: str | None
    status: str  # waiting | approved | revised | rejected_final


def build_branch_graph(engine) -> StateGraph:
    def owner_of(state: BranchState) -> str:
        return DEPARTMENTS[state["department_id"]]["owner"]

    def open_gate(state: BranchState) -> dict:
        department_id, round_number = state["department_id"], state["round"]
        if round_number > state["max_rounds"]:  # a section reopened too many times: no more gates
            return {"status": "rejected_final"}
        approval_store.open_gate(engine, state["ticket_id"], department_id, round_number)
        trace.record(
            engine, state["ticket_id"], 3, "approval-gate", "open_gate",
            {"round": round_number, "max_rounds": state["max_rounds"], "section_overall_pass": state["evaluation_result"].get("overall_pass")},
            {"waiting_for": owner_of(state)}, department_id, dedupe_key=f"gate:{department_id}:{round_number}",
        )
        return {"decision": None, "error": None, "decided_by": None, "status": "waiting"}

    def after_open_gate(state: BranchState) -> str:
        return "finish_rejected" if state.get("status") == "rejected_final" else "human_approval"

    def human_approval(state: BranchState) -> dict:
        payload = {
            "department_id": state["department_id"],
            "owner": owner_of(state),
            "section_id": f"{state['ticket_id']}:{state['department_id']}",
            "round": state["round"],
            "max_rounds": state["max_rounds"],
            "content": state["content"],
            "evaluation_result": state["evaluation_result"],
            "needs_attention": not state["evaluation_result"].get("overall_pass", False),
            "error": state.get("error"),
        }
        value = interrupt(payload)  # <- the flow stops here until the owner decides; the state is in the checkpoint
        try:
            decision = approval.validate_decision(value, state["department_id"])
        except DecisionError as error:
            return {"decision": None, "error": str(error), "decided_by": None}
        return {"decision": decision, "error": None, "decided_by": value.get("decided_by") if isinstance(value, dict) else None}

    def after_human(state: BranchState) -> str:
        return "human_approval" if state.get("error") else "record_decision"

    def record_decision(state: BranchState) -> dict:
        decision = state["decision"]
        approval_store.record_decision(engine, state["ticket_id"], state["department_id"], state["round"], decision, state.get("decided_by"))
        trace.record(
            engine, state["ticket_id"], 3, f"human:{decision['approver']}", "decision",
            {"round": state["round"], "department": state["department_id"]},
            {"decision": decision["decision"], "comment": decision["comment"], "edited": bool(decision["edited_content"]), "commitments": decision["commitments"]},
            state["department_id"],
        )
        return {}

    def route(state: BranchState) -> str:
        if state["decision"]["decision"] == "approve":
            return "finish_approved"
        return "finish_rejected" if state["round"] >= state["max_rounds"] else "revise"

    def finish_approved(state: BranchState) -> dict:
        trace.record(engine, state["ticket_id"], 3, "approval-gate", "approved", {"round": state["round"]}, {"approver": state["decision"]["approver"]}, state["department_id"])
        return {"status": "approved"}

    def finish_rejected(state: BranchState) -> dict:
        comment = (state.get("decision") or {}).get("comment") or "The section ran out of approval rounds."
        approval_store.reject_section(engine, state["ticket_id"], state["department_id"], comment)
        trace.record(engine, state["ticket_id"], 3, "approval-gate", "rejected", {"round": state["round"], "max_rounds": state["max_rounds"]}, {"reason": "the limit of approval rounds was reached"}, state["department_id"])
        return {"status": "rejected_final"}

    def revise(state: BranchState) -> dict:
        decision = state["decision"]
        result = revision.revise(
            engine, state["ticket_id"], state["brief"], state["facts"], state["content"], decision["comment"], [], decision["edited_content"], state["max_iterations"], state["round"],
        )
        approval_store.apply_revision(engine, state["ticket_id"], state["department_id"], result["content"], result["evaluation_result"], result["engine"])
        trace.record(
            engine, state["ticket_id"], 3, f"generator:{state['department_id']}" if result["engine"] != "human" else f"human:{decision['approver']}", "revise",
            {"round": state["round"], "request": decision["comment"], "edited": bool(decision["edited_content"])},
            {"engine": result["engine"], "overall_pass": result["evaluation_result"]["overall_pass"]}, state["department_id"],
        )
        return {"content": result["content"], "evaluation_result": result["evaluation_result"], "round": state["round"] + 1, "status": "revised"}

    graph = StateGraph(BranchState)
    for name, node in (("open_gate", open_gate), ("human_approval", human_approval), ("record_decision", record_decision), ("finish_approved", finish_approved), ("finish_rejected", finish_rejected), ("revise", revise)):
        graph.add_node(name, node)
    graph.add_edge(START, "open_gate")
    graph.add_conditional_edges("open_gate", after_open_gate, {"human_approval": "human_approval", "finish_rejected": "finish_rejected"})
    graph.add_conditional_edges("human_approval", after_human, {"human_approval": "human_approval", "record_decision": "record_decision"})
    graph.add_conditional_edges("record_decision", route, {"finish_approved": "finish_approved", "finish_rejected": "finish_rejected", "revise": "revise"})
    graph.add_edge("revise", "open_gate")
    graph.add_edge("finish_approved", END)
    graph.add_edge("finish_rejected", END)
    return graph


# ------------------------------------------------------------------------------------------------ coordinator with the arbitration node
class CoordinatorState(TypedDict, total=False):
    ticket_id: str
    sections: list
    arbitration_round: int
    conflicts: list
    arbitration_log: Annotated[list, operator.add]
    reopen: list
    outcome: str  # clear | reopened | blocked | done
    reason: str


def build_coordinator_graph(engine, brief_for) -> StateGraph:
    """`brief_for(ticket_id, department_id)` -> (brief, facts) with the section's commitments applied (a capped volume, arbitration notes)."""

    def ticket_facts(ticket_id: str) -> dict:
        from sqlmodel import Session

        with LOCK, Session(engine) as session:
            ticket = store.get_ticket(session, ticket_id)
            return {"country": ticket.client_country, "volume": ticket.monthly_volume, "handoff": dict(ticket.handoff), "rfp_id": ticket.rfp_id}

    def collect(state: CoordinatorState) -> dict:
        sections = approval_store.sections_snapshot(engine, state["ticket_id"])
        trace.record(engine, state["ticket_id"], 3, "coordinator", "collect", {"departments": [s["department_id"] for s in sections]}, {"approved": [s["department_id"] for s in sections if s["approval_status"] == "approved"], "rejected": [s["department_id"] for s in sections if s["approval_status"] == "rejected"]})
        return {"sections": sections, "outcome": "", "reopen": []}

    def block(ticket_id: str, reason: str) -> dict:
        approval_store.set_status(engine, ticket_id, approval_store.NEEDS_REVIEW, 3, reason, approval_state="blocked", current_step="Blocked: a person has to decide", error_message=reason)
        return {"outcome": "blocked", "reason": reason}

    def arbitrate(state: CoordinatorState) -> dict:
        ticket_id, sections = state["ticket_id"], state["sections"]
        info = ticket_facts(ticket_id)
        rejected = [s["department_id"] for s in sections if s["approval_status"] == "rejected"]
        if rejected:
            trace.record(engine, ticket_id, 3, "arbitration", "arbitrate", {"rejected_sections": rejected}, {"outcome": "blocked"})
            return block(ticket_id, f"The {', '.join(rejected)} section was rejected after {approval.max_rounds()} approval rounds: no final document until a person resolves it.")
        conflicts = arbitration.detect(sections, info["country"], info["volume"])
        if not conflicts:
            trace.record(engine, ticket_id, 3, "arbitration", "arbitrate", {"triggers_checked": list(arbitration.TRIGGER_IDS)}, {"conflicts": []})
            return {"outcome": "clear", "conflicts": []}
        round_number = state.get("arbitration_round", 0) + 1
        log = [{**conflict.as_dict(), "round": round_number} for conflict in conflicts]
        if round_number > approval.max_arbitration_rounds():
            for conflict in conflicts:
                for department_id in conflict.departments:
                    approval_store.reject_section(engine, ticket_id, department_id, f"{conflict.trigger_id} unresolved after {approval.max_arbitration_rounds()} arbitration rounds.")
                trace.record(engine, ticket_id, 3, f"arbiter:{conflict.arbiter}", "arbitrate", {"trigger": conflict.trigger_id, "round": round_number, "limit": approval.max_arbitration_rounds()}, {"resolution": "rejected: unresolved after the iteration limit", "sections": conflict.departments})
            result = block(ticket_id, "Conflict(s) " + ", ".join(c.trigger_id for c in conflicts) + f" unresolved after {approval.max_arbitration_rounds()} arbitration rounds.")
            return {**result, "conflicts": [c.as_dict() for c in conflicts], "arbitration_log": log, "arbitration_round": round_number}
        reopen: list[str] = []
        for conflict in conflicts:
            for action in arbitration.resolve(conflict):
                department_id = action["department_id"]
                if action["commitments"] or action["notes"]:
                    current = next(s for s in sections if s["department_id"] == department_id)["commitments"]
                    notes = [*current.get("arbitration_notes", []), *action["notes"]]
                    approval_store.update_commitments(engine, ticket_id, department_id, {**action["commitments"], "arbitration_notes": notes, **({"volume_cap": action["volume"]} if action.get("volume") else {})})
                brief, facts = brief_for(ticket_id, department_id)
                previous = next(s for s in sections if s["department_id"] == department_id)["content"]
                result = revision.revise(engine, ticket_id, brief, facts, previous, action["feedback"], action["fixes"], None, approval.max_rounds(), round_number)
                approval_store.apply_revision(
                    engine, ticket_id, department_id, result["content"], result["evaluation_result"], result["engine"],
                    {"round": round_number, "decision": action["decision"], "comment": action["feedback"], "approver": action["arbiter"], "decided_by": "arbitration", "trigger_id": action["trigger_id"], "at": store.now().isoformat()},
                )
                trace.record(
                    engine, ticket_id, 3, f"arbiter:{action['arbiter']}", "arbitrate",
                    {"trigger": conflict.trigger_id, "round": round_number, "evidence": conflict.evidence, "rule": conflict.rule},
                    {"decision": action["decision"], "section": department_id, "rewritten": True, "overall_pass": result["evaluation_result"]["overall_pass"]}, department_id,
                )
                if department_id not in reopen:
                    reopen.append(department_id)
        return {"outcome": "reopened", "reopen": reopen, "conflicts": [c.as_dict() for c in conflicts], "arbitration_log": log, "arbitration_round": round_number}

    def after_arbitrate(state: CoordinatorState) -> str:
        return "finalize" if state["outcome"] == "clear" else END

    def finalize(state: CoordinatorState) -> dict:
        ticket_id = state["ticket_id"]
        info = ticket_facts(ticket_id)
        sections = approval_store.sections_snapshot(engine, ticket_id)
        if not sections or any(s["approval_status"] != "approved" for s in sections):
            return block(ticket_id, "The final document needs every department's approval.")
        country = info["country"]
        violations = {s["department_id"]: rules.check_all(s["content"], country) for s in sections}
        violations = {key: value for key, value in violations.items() if value}  # the last guard: the approved text must satisfy every guideline of CONTEXT section 5
        if violations:
            trace.record(engine, ticket_id, 3, "final-guard", "check_guidelines", {"guidelines": list(GUIDELINES)[:2]}, {"violations": {key: list(value) for key, value in violations.items()}})
            return block(ticket_id, "The approved text still breaks company guidelines: " + "; ".join(f"{key}: {', '.join(value)}" for key, value in violations.items()))
        log = state.get("arbitration_log", [])
        document = final_document.build(ticket_id, info["rfp_id"], info["handoff"], sections, [{"trigger_id": e["trigger_id"], "arbiter": e["arbiter"], "evidence": " ".join(e["evidence"])} for e in log])
        trace.record(engine, ticket_id, 3, "document-synthesizer", "finalize", {"approved_sections": [s["department_id"] for s in sections]}, {"currency": document["currency"], "characters": len(document["content_markdown"]), "arbitration_decisions": len(log)})
        approval_store.store_final_document(engine, ticket_id, document)  # the last event of the run is the ticket becoming `done`
        return {"outcome": "done"}

    graph = StateGraph(CoordinatorState)
    graph.add_node("collect", collect)
    graph.add_node("arbitrate", arbitrate)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "collect")
    graph.add_edge("collect", "arbitrate")
    graph.add_conditional_edges("arbitrate", after_arbitrate, {"finalize": "finalize", END: END})
    graph.add_edge("finalize", END)
    return graph

