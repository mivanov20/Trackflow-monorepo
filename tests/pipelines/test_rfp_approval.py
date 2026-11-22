"""RFP approval (Milestone 9, part 3): human-in-the-loop per department, resume from a checkpoint, parallel branches, the loop limits, the arbitration node and the final document.

Every test drives the real graphs with simulated human decisions (`Workflow.submit`, the same resume entry point the API uses). No model is configured: generators and evaluators use their rules.
The Part 1 and Part 2 results are produced by the real pipeline from the sample PDFs of the CONTEXT, so Part 3 always starts from what Part 2 actually hands over.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from data.pipelines.rfp_intake import Workflow, approval, approval_runner, approval_store, arbitration, generators, run_intake, run_proposal, store, trace
from data.pipelines.rfp_intake.approval_runner import ApprovalError, branch_thread, coordinator_thread
from services import models  # noqa: F401  (registers the tables)

FIXTURES = Path(__file__).parent / "fixtures" / "rfp-requests"
OWNERS = {"warehouse": "Ana Whitfield", "lastmile": "Carlos Vega", "reverse": "Sofía Ramos"}


@pytest.fixture(autouse=True)
def rules_only(monkeypatch):
    for name in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL", "RFP_MAX_APPROVAL_ROUNDS", "RFP_MAX_ARBITRATION_ROUNDS", "RFP_MAX_DRAFT_ITERATIONS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)


def ready_ticket(engine, number: int, ticket_id: str = "t-1") -> str:
    """Parts 1 and 2 for real: the ticket arrives in Part 3 exactly as Part 2 leaves it."""
    with Session(engine) as session:
        store.create_ticket(session, ticket_id=ticket_id, raw_pdf_path=str(FIXTURES / f"trackflow-request-{number}.pdf"), original_filename="rfp.pdf", uploaded_by="u")
    assert run_intake(ticket_id, engine) == "intake_complete"
    assert run_proposal(ticket_id, engine) in ("under_evaluation", "needs_human_review")
    return ticket_id


def decision(department_id: str, kind: str = "approve", **extra) -> dict:
    return {"decision": kind, "approver": OWNERS[department_id], "comment": extra.pop("comment", "" if kind == "approve" else "Please change this."), **extra}


def ticket_of(engine, ticket_id: str):
    with Session(engine) as session:
        ticket = store.get_ticket(session, ticket_id)
        session.expunge(ticket)
        return ticket


def section_of(engine, ticket_id: str, department_id: str) -> dict:
    return next(s for s in approval_store.sections_snapshot(engine, ticket_id) if s["department_id"] == department_id)


def events(engine, ticket_id: str, **filters) -> list[dict]:
    with Session(engine) as session:
        rows = trace.events(session, ticket_id)
    return [row for row in rows if all(row.get(key) == value for key, value in filters.items())]


# ------------------------------------------------------------------------------------------------ the interruption and the checkpoint
def test_the_flow_pauses_at_each_departments_gate_and_the_ticket_waits_for_approval(engine):
    ticket_id = ready_ticket(engine, 1)
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    ticket = ticket_of(engine, ticket_id)
    assert (ticket.status, ticket.approval_state) == ("waiting_for_approval", "waiting") and "Ana Whitfield" in ticket.current_step and "Sofía Ramos" in ticket.current_step
    assert workflow.waiting_departments(ticket_id) == ["warehouse", "reverse"]
    payload = workflow.pending_payload(ticket_id, "warehouse")  # what the paused branch is waiting for: the approval ticket of the department
    assert payload["owner"] == "Ana Whitfield" and payload["section_id"] == f"{ticket_id}:warehouse" and payload["round"] == 1
    assert payload["content"].startswith("## Warehousing proposal") and payload["evaluation_result"]["overall_pass"] is True and payload["needs_attention"] is False
    assert all(section_of(engine, ticket_id, d)["approval_status"] == "pending" for d in ("warehouse", "reverse"))


def test_the_state_is_in_the_checkpoint_and_the_thread_ids_are_namespaced_by_ticket_and_department(engine):
    saver = MemorySaver()
    workflow = Workflow(engine, saver)
    first, second = ready_ticket(engine, 1, "t-one"), ready_ticket(engine, 2, "t-two")
    workflow.start(first)
    workflow.start(second)
    threads = {checkpoint.config["configurable"]["thread_id"] for thread in ("t-one", "t-two") for department in ("warehouse", "lastmile", "reverse") for checkpoint in saver.list({"configurable": {"thread_id": branch_thread(thread, department)}})}
    assert threads == {"rfp-t-one:warehouse", "rfp-t-one:reverse", "rfp-t-two:warehouse", "rfp-t-two:lastmile"}  # one checkpoint thread per ticket and department, nothing shared
    assert branch_thread("t-one", "warehouse") == "rfp-t-one:warehouse" and coordinator_thread("t-one") == "rfp-t-one"
    workflow.submit("t-one", "warehouse", decision("warehouse"))  # deciding on one ticket leaves the other ticket's gates untouched
    assert workflow.waiting_departments("t-two") == ["warehouse", "lastmile"] and workflow.waiting_departments("t-one") == ["reverse"]


def test_approving_one_department_while_another_is_still_interrupted_proves_parallel_branches(engine):
    ticket_id = ready_ticket(engine, 1)
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    warehouse_checkpoint = workflow.branch.get_state({"configurable": {"thread_id": branch_thread(ticket_id, "warehouse")}}).config["configurable"]["checkpoint_id"]

    outcome = workflow.submit(ticket_id, "reverse", decision("reverse"))  # B (reverse) is approved while A (warehouse) has not decided
    assert outcome["waiting"] == ["warehouse"] and outcome["ticket_status"] == "waiting_for_approval"
    assert section_of(engine, ticket_id, "reverse")["approval_status"] == "approved" and section_of(engine, ticket_id, "reverse")["approver"] == "Sofía Ramos"
    assert section_of(engine, ticket_id, "warehouse")["approval_status"] == "pending"
    assert workflow.is_waiting(ticket_id, "warehouse") and not workflow.is_waiting(ticket_id, "reverse")  # B's branch finished, A's is still paused
    assert workflow.branch.get_state({"configurable": {"thread_id": branch_thread(ticket_id, "warehouse")}}).config["configurable"]["checkpoint_id"] == warehouse_checkpoint  # A was not touched at all
    assert approval_store.get_final_document(Session(engine), ticket_id) is None  # and nothing is final until A decides too

    sequence = [(row["node"], row["department_id"]) for row in events(engine, ticket_id) if row["part"] == 3 and row["node"] in ("decision", "approved")]
    assert sequence == [("decision", "reverse"), ("approved", "reverse")]  # the trace shows B's whole branch ran, with no event of A's after its gate


def test_both_owners_deciding_at_the_same_time_do_not_block_each_other(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}", connect_args={"check_same_thread": False, "timeout": 30})
    SQLModel.metadata.create_all(engine)
    ticket_id = ready_ticket(engine, 1)
    workflow = Workflow(engine, SqliteSaver(sqlite3.connect(str(tmp_path / "cp.sqlite"), check_same_thread=False)))
    workflow.start(ticket_id)
    errors, barrier = [], threading.Barrier(2)

    def decide(department_id: str):
        try:
            barrier.wait(timeout=10)
            workflow.submit(ticket_id, department_id, decision(department_id))
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=decide, args=(department_id,)) for department_id in ("warehouse", "reverse")]
    [thread.start() for thread in threads]
    [thread.join(timeout=60) for thread in threads]
    assert errors == [] and ticket_of(engine, ticket_id).status == "done"
    assert len(events(engine, ticket_id, node="finalize")) == 1  # the final document was generated once, not once per decision


def test_resume_continues_from_the_interruption_it_does_not_restart_the_flow(engine):
    ticket_id = ready_ticket(engine, 1)
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    before = {node: len(events(engine, ticket_id, node=node)) for node in ("start", "open_gate", "generate")}
    workflow.submit(ticket_id, "warehouse", decision("warehouse"))
    after = {node: len(events(engine, ticket_id, node=node)) for node in ("start", "open_gate", "generate")}
    assert after == before  # no gate was opened again, nothing was regenerated, the approvals were not started a second time
    assert [row["node"] for row in events(engine, ticket_id, department_id="warehouse") if row["part"] == 3] == ["open_gate", "decision", "approved"]


def test_a_pause_survives_a_restart_because_the_checkpoint_is_on_disk(engine, tmp_path):
    ticket_id = ready_ticket(engine, 1)
    path = str(tmp_path / "checkpoints.sqlite")
    Workflow(engine, SqliteSaver(sqlite3.connect(path, check_same_thread=False))).start(ticket_id)
    restarted = Workflow(engine, SqliteSaver(sqlite3.connect(path, check_same_thread=False)))  # a new process: new graphs, new connection, same file
    assert restarted.waiting_departments(ticket_id) == ["warehouse", "reverse"] and restarted.pending_payload(ticket_id, "reverse")["owner"] == "Sofía Ramos"
    restarted.submit(ticket_id, "warehouse", decision("warehouse"))
    restarted.submit(ticket_id, "reverse", decision("reverse"))
    assert ticket_of(engine, ticket_id).status == "done" and len(events(engine, ticket_id, node="open_gate")) == 2  # resumed, not reopened


def test_if_the_checkpoint_is_lost_the_gate_is_rebuilt_from_the_section(engine):
    ticket_id = ready_ticket(engine, 1)
    Workflow(engine, MemorySaver()).start(ticket_id)
    fresh = Workflow(engine, MemorySaver())  # a different, empty checkpointer: the paused state is gone
    assert fresh.waiting_departments(ticket_id) == ["warehouse", "reverse"] and not fresh.is_waiting(ticket_id, "warehouse")  # the database knows both are pending; there is nothing to resume from
    fresh.submit(ticket_id, "warehouse", decision("warehouse"))
    fresh.submit(ticket_id, "reverse", decision("reverse"))
    assert ticket_of(engine, ticket_id).status == "done"


# ------------------------------------------------------------------------------------------------ the final document
def test_the_final_document_is_generated_only_when_every_department_has_approved(engine):
    ticket_id = ready_ticket(engine, 2)
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    workflow.submit(ticket_id, "lastmile", decision("lastmile"))
    with Session(engine) as session:
        assert approval_store.get_final_document(session, ticket_id) is None and store.get_ticket(session, ticket_id).status == "waiting_for_approval"
    workflow.submit(ticket_id, "warehouse", decision("warehouse"))
    with Session(engine) as session:
        document = approval_store.get_final_document(session, ticket_id)
        ticket = store.get_ticket(session, ticket_id)
        assert (ticket.status, ticket.approval_state, ticket.current_step) == ("done", "complete", "Final document generated")
        assert document.currency == "USD" and [s["department_id"] for s in document.sections] == ["warehouse", "lastmile"]
        assert all(s["approver"] == OWNERS[s["department_id"]] and s["approved_at"] for s in document.sections)  # approved by the named owner of each department
        assert "# Logistics proposal for Luna Cosmetics" in document.content_markdown and "## Approvals" in document.content_markdown and "| Ana Whitfield |" not in document.content_markdown.split("## Approvals")[0]
        assert "EUR" not in document.content_markdown and document.sections[0]["content"] in document.content_markdown


def test_a_ticket_that_arrives_needs_human_review_from_part_two_enters_part_three(engine, monkeypatch):
    real = generators.GENERATORS["reverse"]
    monkeypatch.setitem(generators.GENERATORS, "reverse", lambda brief, facts, concise: type(real(brief, facts, concise))(real(brief, facts, concise).content + "\nWe process every return within 24 hours.", derived_numbers=real(brief, facts, concise).derived_numbers))
    ticket_id = ready_ticket(engine, 1)
    assert ticket_of(engine, ticket_id).status == "needs_human_review"  # Part 2: the iteration limit was exhausted
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    assert ticket_of(engine, ticket_id).status == "waiting_for_approval"
    assert workflow.pending_payload(ticket_id, "reverse")["needs_attention"] is True and workflow.pending_payload(ticket_id, "warehouse")["needs_attention"] is False  # the owner is told which section did not pass the checks


def test_only_a_ticket_with_ready_sections_can_start_and_only_once(engine):
    ticket_id = ready_ticket(engine, 1)
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    with pytest.raises(ApprovalError, match="already started") as again:
        workflow.start(ticket_id)
    assert again.value.kind == "conflict"
    with Session(engine) as session:
        store.create_ticket(session, ticket_id="t-fresh", raw_pdf_path="x.pdf", original_filename="x.pdf", uploaded_by="u")
    with pytest.raises(ApprovalError, match="proposal sections are ready"):
        workflow.start("t-fresh")
    with pytest.raises(ApprovalError) as missing:
        workflow.start("nope")
    assert missing.value.kind == "not_found"


# ------------------------------------------------------------------------------------------------ the human response is validated before it reaches the graph
@pytest.mark.parametrize(
    "raw, message",
    [
        ({"decision": "maybe", "approver": "Ana Whitfield"}, "must be one of"),
        ({"decision": "approve", "approver": "Carlos Vega"}, "Only Ana Whitfield"),  # a different owner cannot sign this department's section
        ({"decision": "approve", "approver": ""}, "Only Ana Whitfield"),
        ({"decision": "reject", "approver": "Ana Whitfield", "comment": ""}, "comment is required"),
        ({"decision": "request_changes", "approver": "Ana Whitfield", "comment": "x"}, "comment is required"),
        ({"decision": "approve", "approver": "Ana Whitfield", "edited_content": "x" * 100}, "only be sent with `request_changes`"),
        ({"decision": "request_changes", "approver": "Ana Whitfield", "comment": "fix it", "edited_content": "too short"}, "edited draft must be"),
        ({"decision": "approve", "approver": "Ana Whitfield", "commitments": {"price": 3}}, "only commitment"),
        ({"decision": "approve", "approver": "Ana Whitfield", "commitments": {"capacity_orders_per_month": -4}}, "whole number"),
        ({"decision": "approve", "approver": "Ana Whitfield", "comment": "x" * 2001}, "too long"),
        ("approve", "must be an object"),
    ],
)
def test_an_invalid_decision_never_reaches_the_graph(engine, raw, message):
    ticket_id = ready_ticket(engine, 1)
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    checkpoint = workflow.branch.get_state({"configurable": {"thread_id": branch_thread(ticket_id, "warehouse")}}).config["configurable"]["checkpoint_id"]
    with pytest.raises(ApprovalError, match=message) as error:
        workflow.submit(ticket_id, "warehouse", raw)
    assert error.value.kind == "invalid"
    assert workflow.branch.get_state({"configurable": {"thread_id": branch_thread(ticket_id, "warehouse")}}).config["configurable"]["checkpoint_id"] == checkpoint  # the paused branch did not move


def test_a_bad_value_that_bypasses_the_api_is_refused_inside_the_graph_too(engine):
    ticket_id = ready_ticket(engine, 1)
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    workflow.branch.invoke(Command(resume={"decision": "approve", "approver": "Somebody Else"}), {"configurable": {"thread_id": branch_thread(ticket_id, "warehouse")}})
    assert workflow.is_waiting(ticket_id, "warehouse")  # still paused: it asked again
    assert "Only Ana Whitfield" in workflow.pending_payload(ticket_id, "warehouse")["error"]
    assert section_of(engine, ticket_id, "warehouse")["approval_status"] == "pending"


def test_decisions_on_the_wrong_state_are_conflicts(engine):
    ticket_id = ready_ticket(engine, 1)
    workflow = Workflow(engine, MemorySaver())
    with pytest.raises(ApprovalError, match="not waiting") as early:
        workflow.submit(ticket_id, "warehouse", decision("warehouse"))
    assert early.value.kind == "conflict"
    workflow.start(ticket_id)
    workflow.submit(ticket_id, "warehouse", decision("warehouse"))
    with pytest.raises(ApprovalError, match="already approved") as twice:
        workflow.submit(ticket_id, "warehouse", decision("warehouse"))
    assert twice.value.kind == "conflict"
    with pytest.raises(ApprovalError) as no_section:
        workflow.submit(ticket_id, "lastmile", decision("lastmile"))  # ModaViva has no last-mile section
    assert no_section.value.kind == "not_found"
    with pytest.raises(ApprovalError) as unknown:
        workflow.submit("nope", "warehouse", decision("warehouse"))
    assert unknown.value.kind == "not_found"


def test_a_section_that_still_breaks_a_guideline_the_arbitration_does_not_cover_cannot_be_approved(engine):
    from data.pipelines.rfp_intake import drafting_graph, knowledge

    with Session(engine) as session:
        store.create_ticket(session, ticket_id="t-1", raw_pdf_path=str(FIXTURES / "trackflow-request-2.pdf"), original_filename="x.pdf", uploaded_by="u")
    run_intake("t-1", engine)
    run_proposal("t-1", engine, facts=drafting_graph.asdict(knowledge.facts("US", search=lambda query: [])))  # no knowledge base: the SLA is missing (GL-2) and every section ends needs_human_review
    workflow = Workflow(engine, MemorySaver())
    workflow.start("t-1")
    with pytest.raises(ApprovalError, match="GL-2.*request changes with an edited draft") as refused:
        workflow.submit("t-1", "lastmile", decision("lastmile"))
    assert refused.value.kind == "invalid" and workflow.is_waiting("t-1", "lastmile")


# ------------------------------------------------------------------------------------------------ request changes, reject, and the iteration limit
def test_requesting_changes_sends_the_section_back_for_a_new_draft_and_a_new_decision(engine):
    ticket_id = ready_ticket(engine, 1)
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    old = section_of(engine, ticket_id, "warehouse")["content"]
    workflow.submit(ticket_id, "warehouse", decision("warehouse", "request_changes", comment="Add our onboarding assumptions", decided_by=None))
    section = section_of(engine, ticket_id, "warehouse")
    assert workflow.is_waiting(ticket_id, "warehouse") and section["approval_status"] == "pending" and section["approval_round"] == 2  # the gate reopened: round 2
    assert section["content"] != old and "Requested by the owner at the approval step: Add our onboarding assumptions" in section["content"]
    assert workflow.pending_payload(ticket_id, "warehouse")["round"] == 2
    with Session(engine) as session:
        history = store.sections_of(session, ticket_id)[0].approval_history
    assert [(h["round"], h["decision"], h["approver"]) for h in history] == [(1, "request_changes", "Ana Whitfield")]
    workflow.submit(ticket_id, "warehouse", decision("warehouse"))
    assert section_of(engine, ticket_id, "warehouse")["approval_status"] == "approved"


def test_the_owner_can_send_an_edited_draft_and_it_is_evaluated_again(engine):
    ticket_id = ready_ticket(engine, 1)
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    edited = section_of(engine, ticket_id, "warehouse")["content"].replace("To be confirmed by Ana Whitfield (the capacity in pallets or SKUs that TrackFlow commits)", "TrackFlow commits capacity for 4,000 pallets")
    workflow.submit(ticket_id, "warehouse", decision("warehouse", "request_changes", comment="Capacity confirmed", edited_content=edited))
    section = section_of(engine, ticket_id, "warehouse")
    assert section["content"] == edited and "4,000 pallets" in section["content"]  # the owner's own text, untouched
    result = section["evaluation_result"]
    assert result["overall_pass"] is True and result["relevance"]["unsupported_figures"] == []  # the owner is the source of her figures
    assert {"readability", "relevance", "compliance"} <= set(result) and workflow.is_waiting(ticket_id, "warehouse")


def test_an_edited_draft_that_breaks_a_guideline_comes_back_with_a_failing_evaluation(engine):
    ticket_id = ready_ticket(engine, 1)
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    edited = section_of(engine, ticket_id, "warehouse")["content"].replace("EUR", "USD")
    workflow.submit(ticket_id, "warehouse", decision("warehouse", "request_changes", comment="Use dollars", edited_content=edited))
    result = section_of(engine, ticket_id, "warehouse")["evaluation_result"]
    assert result["overall_pass"] is False and "GL-1" in result["compliance"]["rule_ids"]
    assert workflow.pending_payload(ticket_id, "warehouse")["needs_attention"] is True


def test_the_approval_rounds_are_limited_and_the_last_refusal_blocks_the_ticket(engine, monkeypatch):
    monkeypatch.setenv("RFP_MAX_APPROVAL_ROUNDS", "2")
    ticket_id = ready_ticket(engine, 1)
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    workflow.submit(ticket_id, "reverse", decision("reverse"))
    workflow.submit(ticket_id, "warehouse", decision("warehouse", "request_changes", comment="Not good enough yet"))
    assert workflow.is_waiting(ticket_id, "warehouse") and section_of(engine, ticket_id, "warehouse")["approval_round"] == 2  # round 2 of 2
    workflow.submit(ticket_id, "warehouse", decision("warehouse", "reject", comment="Still not acceptable"))  # the limit is reached
    ticket = ticket_of(engine, ticket_id)
    assert not workflow.is_waiting(ticket_id, "warehouse") and section_of(engine, ticket_id, "warehouse")["approval_status"] == "rejected"
    assert (ticket.status, ticket.approval_state) == ("needs_human_review", "blocked") and "rejected after 2 approval rounds" in ticket.error_message
    with Session(engine) as session:
        assert approval_store.get_final_document(session, ticket_id) is None  # no final document while a section is rejected
    assert [row["node"] for row in events(engine, ticket_id, department_id="warehouse") if row["node"] == "rejected"] == ["rejected"]
    assert section_of(engine, ticket_id, "reverse")["approval_status"] == "approved"  # the other department's approval is kept


def test_the_limits_are_configurable_and_never_below_one(monkeypatch):
    assert (approval.max_rounds(), approval.max_arbitration_rounds()) == (3, 2)
    monkeypatch.setenv("RFP_MAX_APPROVAL_ROUNDS", "5"), monkeypatch.setenv("RFP_MAX_ARBITRATION_ROUNDS", "0")
    assert (approval.max_rounds(), approval.max_arbitration_rounds()) == (5, 1)


# ------------------------------------------------------------------------------------------------ arbitration: the three triggers of the CONTEXT and their fixed arbiters
def section(department_id: str, content: str = "Prices in EUR. On-time SLA 90%.", **commitments) -> dict:
    return {"department_id": department_id, "content": content, "commitments": commitments}


def test_no_conflict_when_the_sections_agree():
    assert arbitration.detect([section("warehouse"), section("reverse")], "ES", None) == []


def test_volume_vs_capacity_fires_when_the_warehouse_cannot_support_the_volume_last_mile_assumes():
    conflicts = arbitration.detect([section("warehouse", capacity_orders_per_month=3000), section("lastmile", assumed_monthly_volume=5000)], "ES", None)
    assert [c.trigger_id for c in conflicts] == ["volume-vs-capacity"] and conflicts[0].arbiter == "Miguel Torres" and conflicts[0].departments == ["lastmile"] and conflicts[0].cap == 3000
    action = arbitration.resolve(conflicts[0])[0]
    assert action["decision"] == "request_changes" and action["commitments"] == {"assumed_monthly_volume": 3000} and "3,000" in action["notes"][0]
    assert arbitration.detect([section("warehouse", capacity_orders_per_month=8000), section("lastmile", assumed_monthly_volume=5000)], "ES", None) == []  # enough capacity: no conflict
    assert arbitration.detect([section("warehouse", capacity_orders_per_month=3000), section("lastmile")], "ES", 5000)[0].trigger_id == "volume-vs-capacity"  # volume from the RFP when last mile states none
    assert arbitration.detect([section("warehouse"), section("lastmile", assumed_monthly_volume=5000)], "ES", None) == []  # no committed capacity yet: nothing to compare


def test_returns_sla_breach_is_arbitrated_by_sofia_ramos_for_her_section_and_miguel_torres_for_the_others():
    promise = "Prices in EUR. On-time SLA 90%. Returns are processed within 24 hours."
    conflicts = arbitration.detect([section("reverse", promise), section("warehouse", promise), section("lastmile")], "ES", None)
    conflict = next(c for c in conflicts if c.trigger_id == "returns-sla-breach")
    assert conflict.departments == ["reverse", "warehouse"] and conflict.arbiters == {"reverse": "Sofía Ramos", "warehouse": "Miguel Torres"}
    actions = {a["department_id"]: a for a in arbitration.resolve(conflict)}
    assert actions["reverse"]["decision"] == "reject" and actions["reverse"]["arbiter"] == "Sofía Ramos"  # she rejects her own section
    assert actions["warehouse"]["decision"] == "request_changes" and actions["warehouse"]["arbiter"] == "Miguel Torres"  # forced on the other one
    only_other = arbitration.detect([section("reverse"), section("warehouse", promise)], "ES", None)
    assert only_other[0].arbiter == "Miguel Torres"  # reverse is clean: the arbiter is the Commercial Director


def test_currency_mismatch_fires_on_the_wrong_currency_or_on_sections_that_disagree():
    wrong = arbitration.detect([section("warehouse", "Prices in USD."), section("reverse", "Prices in EUR.")], "ES", None)
    assert [c.trigger_id for c in wrong] == ["currency-mismatch"] and wrong[0].departments == ["warehouse"] and wrong[0].arbiter == "Miguel Torres"
    assert arbitration.resolve(wrong[0])[0]["fixes"][0]["id"] == "GL-1"
    assert arbitration.detect([section("warehouse", "Prices in EUR."), section("lastmile", "Prices in USD.")], "US", None)[0].departments == ["warehouse"]
    unknown_country = arbitration.detect([section("warehouse", "Prices in EUR."), section("lastmile", "Prices in USD.")], None, None)
    assert unknown_country[0].trigger_id == "currency-mismatch" and unknown_country[0].departments == ["warehouse", "lastmile"]
    assert set(arbitration.TRIGGER_IDS) == {"volume-vs-capacity", "returns-sla-breach", "currency-mismatch"}


def test_arbitration_resolves_volume_vs_capacity_through_the_graph_and_the_arbiter_is_in_the_trace(engine):
    ticket_id = ready_ticket(engine, 2)  # Luna Cosmetics: warehouse + last mile, 5,000 orders a month
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    workflow.submit(ticket_id, "warehouse", decision("warehouse", commitments={"capacity_orders_per_month": 3000}))
    outcome = workflow.submit(ticket_id, "lastmile", decision("lastmile"))
    lastmile = section_of(engine, ticket_id, "lastmile")
    assert outcome["waiting"] == ["lastmile"] and ticket_of(engine, ticket_id).status == "waiting_for_approval"  # the arbiter sent last mile back; the document is not generated
    assert "capped at 3,000 orders per month" in lastmile["content"] and "Up to 3,000 shipments per month" in lastmile["content"]  # revised downward
    assert "The client expects about 5,000 orders per month" in lastmile["content"] and "The client expects about 3,000" not in lastmile["content"]  # the client's own figure is not rewritten: only what is quoted is capped
    assert "Requested by the owner" not in lastmile["content"]  # the arbiter's instruction is not attributed to the owner
    assert lastmile["commitments"]["assumed_monthly_volume"] == 3000 and lastmile["approval_status"] == "pending" and section_of(engine, ticket_id, "warehouse")["approval_status"] == "approved"
    arbiter = events(engine, ticket_id, agent="arbiter:Miguel Torres")[0]
    assert arbiter["input"]["trigger"] == "volume-vs-capacity" and arbiter["output"]["decision"] == "request_changes" and arbiter["department_id"] == "lastmile"
    workflow.submit(ticket_id, "lastmile", decision("lastmile"))
    with Session(engine) as session:
        document = approval_store.get_final_document(session, ticket_id)
        assert ticket_of(engine, ticket_id).status == "done" and "## Decisions of the arbiter" in document.content_markdown and "volume-vs-capacity (Miguel Torres)" in document.content_markdown


def reverse_with_a_fast_returns_promise(monkeypatch):
    real = generators.GENERATORS["reverse"]

    def promising(brief, facts, concise):
        draft = real(brief, facts, concise)
        return type(draft)(draft.content + "\nWe process every return within 24 hours.", derived_numbers=draft.derived_numbers)

    monkeypatch.setitem(generators.GENERATORS, "reverse", promising)
    return real


def test_arbitration_resolves_returns_sla_breach_by_rejecting_the_section_and_forcing_a_compliant_rewrite(engine, monkeypatch):
    real = reverse_with_a_fast_returns_promise(monkeypatch)
    ticket_id = ready_ticket(engine, 1)  # Part 2 ships the reverse section needs_human_review: it promises returns in 24 hours
    assert "within 24 hours" in section_of(engine, ticket_id, "reverse")["content"]
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    monkeypatch.setitem(generators.GENERATORS, "reverse", real)  # the rewrite that the arbiter's rule forces will be compliant
    workflow.submit(ticket_id, "warehouse", decision("warehouse"))
    outcome = workflow.submit(ticket_id, "reverse", decision("reverse"))  # the owner approves the text, the arbitration does not
    reverse = section_of(engine, ticket_id, "reverse")
    assert outcome["waiting"] == ["reverse"] and "within 24 hours" not in reverse["content"] and reverse["approval_status"] == "pending"
    arbiter = events(engine, ticket_id, agent="arbiter:Sofía Ramos")[0]
    assert arbiter["input"]["trigger"] == "returns-sla-breach" and arbiter["output"]["decision"] == "reject"
    workflow.submit(ticket_id, "reverse", decision("reverse"))
    assert ticket_of(engine, ticket_id).status == "done"


def test_unresolved_conflicts_are_rejected_after_the_arbitration_limit(engine, monkeypatch):
    reverse_with_a_fast_returns_promise(monkeypatch)  # the generator keeps promising 24 hours: the conflict can never be resolved
    ticket_id = ready_ticket(engine, 1)
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    workflow.submit(ticket_id, "warehouse", decision("warehouse"))
    for _ in range(approval.max_arbitration_rounds()):  # each round the arbiter sends the section back and the owner approves again
        workflow.submit(ticket_id, "reverse", decision("reverse"))
        assert workflow.is_waiting(ticket_id, "reverse")
    workflow.submit(ticket_id, "reverse", decision("reverse"))  # the next approval meets the limit
    ticket = ticket_of(engine, ticket_id)
    assert (ticket.status, ticket.approval_state) == ("needs_human_review", "blocked") and "returns-sla-breach unresolved after 2 arbitration rounds" in ticket.error_message
    assert section_of(engine, ticket_id, "reverse")["approval_status"] == "rejected"
    with Session(engine) as session:
        assert approval_store.get_final_document(session, ticket_id) is None
    assert len(events(engine, ticket_id, agent="arbiter:Sofía Ramos", node="arbitrate")) == approval.max_arbitration_rounds() + 1


def test_arbitration_rewrites_a_section_in_the_wrong_currency(engine):
    ticket_id = ready_ticket(engine, 1)  # Spain: EUR
    workflow = Workflow(engine, MemorySaver())
    workflow.start(ticket_id)
    edited = section_of(engine, ticket_id, "warehouse")["content"].replace("EUR", "USD")
    workflow.submit(ticket_id, "warehouse", decision("warehouse", "request_changes", comment="Use dollars", edited_content=edited))
    workflow.submit(ticket_id, "warehouse", decision("warehouse"))  # approves text quoted in USD for a Spanish client (GL-1 is the arbitration's to handle)
    workflow.submit(ticket_id, "reverse", decision("reverse"))
    warehouse = section_of(engine, ticket_id, "warehouse")
    assert "USD" not in warehouse["content"] and "EUR" in warehouse["content"] and warehouse["approval_status"] == "pending"
    assert events(engine, ticket_id, agent="arbiter:Miguel Torres")[0]["input"]["trigger"] == "currency-mismatch"
    workflow.submit(ticket_id, "warehouse", decision("warehouse"))
    assert ticket_of(engine, ticket_id).status == "done"


def test_the_coordinator_runs_on_its_own_namespaced_thread(engine):
    saver = MemorySaver()
    workflow = Workflow(engine, saver)
    ticket_id = ready_ticket(engine, 1)
    workflow.start(ticket_id)
    workflow.submit(ticket_id, "warehouse", decision("warehouse"))
    workflow.submit(ticket_id, "reverse", decision("reverse"))
    assert list(saver.list({"configurable": {"thread_id": coordinator_thread(ticket_id)}})) and approval_runner.coordinator_thread(ticket_id) == f"rfp-{ticket_id}"
