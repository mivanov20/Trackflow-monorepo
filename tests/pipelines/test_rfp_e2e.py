"""End to end, Parts 1 to 3 with simulated human approvals: one ticket travels from the uploaded PDF to the final document and it must read as ONE process.

The sample PDFs of the CONTEXT go through the real intake and the real drafting; the owners' decisions are programmatic (the same resume entry point the API uses). What is asserted is consistency:
the status timeline (no jumps), the same client, country, currency and departments in every part, the order and the content of the trace, and what the user would read at each step.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest
from sqlmodel import Session

from data.pipelines.rfp_intake import approval_store, e2e, store

FIXTURES = Path(__file__).parent / "fixtures" / "rfp-requests"
ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TIMELINE = ["analyzing", "intake_complete", "drafting", "under_evaluation", "waiting_for_approval", "done"]


@pytest.fixture(autouse=True)
def rules_only(monkeypatch):
    for name in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL", "RFP_MAX_APPROVAL_ROUNDS", "RFP_MAX_ARBITRATION_ROUNDS", "RFP_MAX_DRAFT_ITERATIONS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="module")
def formal():
    return e2e.run(FIXTURES / "trackflow-request-1.pdf")


@pytest.fixture(scope="module")
def informal():
    return e2e.run(FIXTURES / "trackflow-request-2.pdf")


def rows(result):
    engine = result["engine"]
    with Session(engine) as session:
        ticket = store.get_ticket(session, result["ticket_id"])
        sections = store.sections_of(session, result["ticket_id"])
        document = approval_store.get_final_document(session, result["ticket_id"])
        session.expunge_all()
    return ticket, sections, document


def test_the_formal_rfp_travels_through_all_three_parts_and_ends_done(formal):
    assert (formal["intake"], formal["proposal"], formal["status"], formal["approval_state"]) == ("intake_complete", "under_evaluation", "done", "complete")
    assert formal["document"].startswith("# Logistics proposal for ModaViva, S.L.")


def test_the_status_never_jumps_it_walks_the_context_lifecycle_in_order(formal, informal):
    assert formal["timeline"] == EXPECTED_TIMELINE and informal["timeline"] == EXPECTED_TIMELINE  # no state is skipped, repeated or visited backwards


def test_the_same_facts_appear_in_every_part(formal):
    ticket, sections, document = rows(formal)
    part1, part3 = ticket.handoff, ticket.part3_handoff
    assert (ticket.client_name, ticket.client_country, ticket.currency) == ("ModaViva, S.L.", "ES", "EUR")
    assert part1["client"] == {"name": "ModaViva, S.L.", "country": "ES", "currency": "EUR", "contact": ticket.client_contact} and part3["client"]["currency"] == "EUR" and document.currency == "EUR"
    assert ticket.departments_needed == part1["departments_needed"] == [s.department_id for s in sections] == [s["department_id"] for s in part3["sections"]] == [s["department_id"] for s in document.sections] == ["warehouse", "reverse"]
    assert all(s.owner == s.approver == {"warehouse": "Ana Whitfield", "reverse": "Sofía Ramos"}[s.department_id] for s in sections)  # the named owner approved each section
    assert [s["content"] for s in document.sections] == [s.draft_content for s in sections]  # the final document is exactly what Part 2 drafted and the owners approved
    assert "lastmile" not in document.content_markdown.lower() and "Last-mile" not in document.content_markdown  # ModaViva keeps its own carrier: no last-mile section anywhere
    assert "USD" not in document.content_markdown and "16 EUR per cubic meter per month" in document.content_markdown


def test_the_us_client_is_in_dollars_from_the_intake_to_the_final_document(informal):
    ticket, sections, document = rows(informal)
    assert (ticket.client_name, ticket.client_country, ticket.currency, ticket.monthly_volume) == ("Luna Cosmetics", "US", "USD", 5000)
    assert [s.department_id for s in sections] == ["warehouse", "lastmile"] and document.currency == "USD"
    assert "EUR" not in document.content_markdown and "UPS, FedEx, DHL" in document.content_markdown and "90% of deliveries per month" in document.content_markdown


def test_no_data_is_lost_between_the_parts(formal):
    ticket, sections, document = rows(formal)
    assert ticket.markdown and ticket.summary and ticket.handoff["workstreams"] and ticket.readability["cost_band"]  # Part 1's results are still there at the end
    assert all(s.key_aspects["asks_of_owner"] and s.evaluation_results["overall_pass"] and s.approval_history[-1]["decision"] == "approve" for s in sections)  # Part 1's key aspects, Part 2's evaluation, Part 3's decision, all on the same row
    assert ticket.part3_handoff["sections"][0]["evaluation_result"]["overall_pass"] is True and ticket.handoff_state == "ready_for_part_2" and ticket.proposal_state == "ready_for_part_3"
    assert document.content_markdown.count("## ") >= 6 and "## Approvals" in document.content_markdown


def test_what_the_user_reads_matches_across_the_parts(formal):
    ticket, sections, _ = rows(formal)
    assert ticket.current_step == "Final document generated" and ticket.error_message is None
    steps = [entry["step"] for entry in ticket.progress]
    assert steps.index("intake_complete") < steps.index("proposal") < steps.index("proposal_ready") < steps.index("done")  # the progress log tells the story in order
    assert ticket.summary.startswith("ModaViva, S.L. (Spain, quoted in EUR)") and "Ana Whitfield (Warehouse Operations)" in ticket.summary  # the owners Part 1 told Sales to ask are the ones who approved


def test_the_trace_orders_the_whole_run_and_every_event_has_agent_input_output_and_time(formal, informal):
    for result in (formal, informal):
        events = result["events"]
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))  # the order is explicit
        times = [datetime.fromisoformat(e["at"]) for e in events]
        assert times == sorted(times)
        assert [e["part"] for e in events] == sorted(e["part"] for e in events) and {e["part"] for e in events} == {1, 2, 3}  # part 1, then 2, then 3
        assert all(e["agent"] and e["node"] and e["at"] and isinstance(e["input"], dict) and isinstance(e["output"], dict) for e in events)
        agents = {e["agent"] for e in events}
        assert {"intake-api", "converter", "classifier", "metadata-extractor", "orchestrator", "synthesizer", "assignment-orchestrator", "evaluator:readability", "evaluator:relevance", "evaluator:compliance", "evaluator:decision",
                "approval-orchestrator", "approval-gate", "coordinator", "arbitration", "document-synthesizer", "ticket"} <= agents
        assert any(a.startswith("worker:") for a in agents) and any(a.startswith("generator:") for a in agents) and any(a.startswith("human:") for a in agents)


def test_the_trace_shows_who_decided_what(formal):
    humans = [e for e in formal["events"] if e["agent"].startswith("human:") and e["node"] == "decision"]
    assert [(e["agent"], e["department_id"], e["output"]["decision"]) for e in humans] == [("human:Ana Whitfield", "warehouse", "approve"), ("human:Sofía Ramos", "reverse", "approve")]
    assert [e["node"] for e in formal["events"] if e["agent"] == "document-synthesizer"] == ["finalize"]


def test_a_request_for_changes_in_the_middle_still_ends_as_one_consistent_process():
    result = e2e.run(FIXTURES / "trackflow-request-1.pdf", "request_changes")
    ticket, sections, document = rows(result)
    assert result["timeline"] == EXPECTED_TIMELINE and result["status"] == "done"
    warehouse = next(s for s in sections if s.department_id == "warehouse")
    assert [(h["round"], h["decision"]) for h in warehouse.approval_history] == [(1, "request_changes"), (2, "approve")]
    assert "Requested by the owner at the approval step: Please add our onboarding assumptions" in document.content_markdown


def test_the_arbitration_of_a_volume_conflict_is_part_of_the_same_timeline():
    result = e2e.run(FIXTURES / "trackflow-request-2.pdf", "volume_conflict")
    ticket, sections, document = rows(result)
    assert result["timeline"] == EXPECTED_TIMELINE and result["status"] == "done"
    assert "capped at 3,000 orders per month" in document.content_markdown and "volume-vs-capacity (Miguel Torres)" in document.content_markdown
    assert any(e["agent"] == "arbiter:Miguel Torres" for e in result["events"])


def test_a_rejected_document_stops_after_part_one_and_never_reaches_the_others():
    result = e2e.run(FIXTURES / "trackflow-request-3.pdf")
    assert (result["intake"], result["status"], result["timeline"]) == ("discarded", "discarded", ["analyzing", "discarded"])
    assert result["document"] is None and {e["part"] for e in result["events"]} == {1} and "proposal" not in result


def test_two_tickets_in_the_same_database_do_not_mix(formal):
    engine = formal["engine"]
    second = e2e.run(FIXTURES / "trackflow-request-2.pdf", engine=engine, ticket_id="e2e-2")
    assert second["status"] == "done" and formal["status"] == "done"
    with Session(engine) as session:
        first_events = {e["agent"] for e in e2e.trace.events(session, "e2e-1")}
        second_sections = [s.department_id for s in store.sections_of(session, "e2e-2")]
    assert second_sections == ["warehouse", "lastmile"] and "worker:lastmile" not in first_events


def test_the_script_runs_the_same_path_from_the_command_line(capsys):
    spec = importlib.util.spec_from_file_location("rfp_e2e_script", ROOT / "scripts" / "rfp_e2e.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main([str(FIXTURES / "trackflow-request-1.pdf"), "--trace"]) == 0
    output = capsys.readouterr().out
    assert "analyzing -> intake_complete -> drafting -> under_evaluation -> waiting_for_approval -> done" in output and "human:Ana Whitfield" in output and "document-synthesizer" in output
