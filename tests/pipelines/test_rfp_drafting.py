"""RFP drafting (Milestone 9, part 2): generators, evaluators, the generator-evaluator loop with its bound, and the persistence of drafts and evaluation results.

Deterministic: the generators use their rules (no model is configured) and the knowledge base is the local documents of `docs/company-knowledge-base/`. The loop is exercised with
stand-in generators that fail on purpose, so a rejection, a retry with feedback and the needs_human_review handoff are tested for real, not assumed.
"""

from __future__ import annotations

import copy
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from data.pipelines.rfp_intake import convert, drafting_graph, evaluators, generators, knowledge, proposal_runner, proposal_store, rules, run_intake, run_proposal, store
from data.pipelines.rfp_intake.generators import Draft
from services import models

FIXTURES = Path(__file__).parent / "fixtures" / "rfp-requests"


@pytest.fixture(autouse=True)
def rules_only(monkeypatch):
    for name in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("RFP_MAX_DRAFT_ITERATIONS", raising=False)


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)


def intake(engine, number: int, ticket_id: str = "t-1") -> str:
    with Session(engine) as session:
        store.create_ticket(session, ticket_id=ticket_id, raw_pdf_path=str(FIXTURES / f"trackflow-request-{number}.pdf"), original_filename="rfp.pdf", uploaded_by="u")
    assert run_intake(ticket_id, engine) == "intake_complete"
    return ticket_id


def handoff_of(engine, ticket_id: str) -> dict:
    with Session(engine) as session:
        return copy.deepcopy(store.get_ticket(session, ticket_id).handoff)


def brief(department_id: str, country: str = "ES", volume: int | None = None, **aspects) -> dict:
    currency = {"US": "USD", "ES": "EUR"}[country]
    key_aspects = {"summary": "Acme needs the service.", "requirements": [], "stated_figures": {}, "asks_of_owner": [], "open_questions": ["Monthly order volume is not stated."] if volume is None else [], "constraints": [], **aspects}
    return {
        "department_id": department_id,
        "department": department_id,
        "owner": {"warehouse": "Ana Whitfield", "lastmile": "Carlos Vega", "reverse": "Sofía Ramos"}[department_id],
        "contributes": "x",
        "client": {"name": "Acme", "country": country, "currency": currency},
        "monthly_volume": volume,
        "deadline": None,
        "services_requested": [],
        "departments_needed": ["warehouse", department_id],
        "summary": "Acme asks for a service.",
        "key_aspects": key_aspects,
    }


def facts_for(country: str) -> knowledge.Facts:
    return knowledge.facts(country)


# ------------------------------------------------------------------------------------------------ knowledge base
def test_the_knowledge_base_gives_the_real_figures_of_the_country():
    es, us = facts_for("ES"), facts_for("US")
    assert (es.sla_percent, es.storage_rate, es.storage_currency, es.storage_location) == (90.0, 16.0, "EUR", "Zaragoza")
    assert (us.sla_percent, us.storage_rate, us.storage_currency, us.storage_location) == (90.0, 18.0, "USD", "Los Angeles")
    assert es.carriers == ["MRW", "SEUR", "DHL"] and us.carriers == ["UPS", "FedEx", "DHL"]
    assert es.storage_grace_days == 30 and es.return_window_days == 30 and "sla-delivery" in es.sources


def test_a_missing_knowledge_base_yields_no_figures_not_invented_ones():
    empty = knowledge.facts("ES", search=lambda query: [])
    assert empty.sla_percent is None and empty.storage_rate is None and empty.carriers == []


def test_the_semantic_knowledge_base_is_used_when_up_and_the_local_documents_when_it_is_not(monkeypatch):
    monkeypatch.setenv("LLM_API_URL", "http://m.test"), monkeypatch.setenv("LLM_API_KEY", "k"), monkeypatch.setenv("LLM_MODEL", "m")
    from data.pipelines import rag

    monkeypatch.setattr(rag, "retrieve", lambda query, k=4: [{"source_document": "semantic", "text": "From the vector database."}])
    assert knowledge.lookup("anything")[0]["source_document"] == "semantic"

    def down(query, k=4):
        raise ConnectionError("qdrant is down")

    monkeypatch.setattr(rag, "retrieve", down)
    assert knowledge.lookup("delivery SLA on-time")[0]["source_document"] == "sla-delivery"  # drafting does not depend on the vector database


# ------------------------------------------------------------------------------------------------ generators: one per department
def test_every_department_has_its_own_generator():
    assert set(generators.GENERATORS) == {"warehouse", "lastmile", "reverse"}
    assert len({generators.GENERATORS[key] for key in generators.GENERATORS}) == 3


def test_the_warehouse_generator_drafts_its_section_with_real_policies_and_no_invented_prices():
    draft = generators.generate(brief("warehouse", "ES", stated_figures={"skus": 15000, "preferred_location": "Zaragoza"}), facts_for("ES"))
    text_ = draft.content
    assert draft.engine == "rules" and "Warehousing proposal: Acme" in text_
    assert "16 EUR per cubic meter per month" in text_ and "first 30 days" in text_ and "15,000 SKUs" in text_
    assert "To be confirmed by Ana Whitfield" in text_  # capacity, onboarding and cost per pallet are not in any source
    assert "USD" not in text_ and "Last-mile" not in text_ and "returns processing" not in text_.lower()


def test_the_last_mile_generator_lists_the_carriers_of_the_country_without_rates():
    draft = generators.generate(brief("lastmile", "US", 5000, stated_figures={"destinations": "across the US", "carrier_selection_requested": True}), facts_for("US"))
    assert "UPS, FedEx, DHL" in draft.content and "Cost per shipment" in draft.content and "USD" in draft.content and "EUR" not in draft.content
    assert "Zaragoza" not in draft.content and "5,001 to 10,000 shipments" in draft.content and "10000" in draft.derived_numbers
    assert not rules.check_carrier_rates(draft.content)


def test_the_reverse_generator_never_promises_a_turnaround_under_48_hours():
    draft = generators.generate(brief("reverse", "ES", stated_figures={"process_steps_requested": ["inspection", "refurbishing", "restock"]}), facts_for("ES"))
    assert "never be shorter than 48 hours" in draft.content and "30 days from delivery" in draft.content
    assert not rules.check_returns_48h(draft.content)


def test_without_the_knowledge_base_the_sla_is_not_invented():
    draft = generators.generate(brief("warehouse"), knowledge.facts("ES", search=lambda query: []))
    assert "could not be taken from the knowledge base" in draft.content and "%" not in draft.content
    assert "To be confirmed by Ana Whitfield (the storage rate was not found" in draft.content


def test_a_readability_fix_makes_the_rules_draft_concise():
    normal = generators.generate(brief("lastmile", "US", 5000), facts_for("US"))
    concise = generators.generate(brief("lastmile", "US", 5000), facts_for("US"), feedback={"text": "x", "fixes": [{"kind": "readability"}]})
    assert len(concise.content) < len(normal.content)


# ------------------------------------------------------------------------------------------------ the Part 1 handoff is the input
def test_the_briefs_come_from_the_part_one_handoff_and_carry_only_that_departments_aspects(engine):
    ticket_id = intake(engine, 1)
    briefs = drafting_graph.build_briefs(handoff_of(engine, ticket_id))
    assert [b["department_id"] for b in briefs] == ["warehouse", "reverse"]  # no last mile for ModaViva
    warehouse = briefs[0]
    assert warehouse["client"] == {"name": "ModaViva, S.L.", "country": "ES", "currency": "EUR"} and warehouse["key_aspects"]["stated_figures"]["skus"] == 15000
    assert "markdown" not in warehouse and "process_steps_requested" not in warehouse["key_aspects"]["stated_figures"]  # the reverse department's figures stay with reverse


@pytest.mark.parametrize("handoff", [{}, None, {"ticket_id": "x", "workstreams": []}, {"ticket_id": "x", "schema_version": 99, "workstreams": [{"department_id": "warehouse"}]}])
def test_an_unusable_handoff_is_refused(handoff):
    with pytest.raises(ValueError):
        drafting_graph.build_briefs(handoff)


def test_the_drafting_never_opens_the_pdf_or_its_markdown(engine, monkeypatch):
    ticket_id = intake(engine, 2)
    with Session(engine) as session:  # the PDF is gone and the Markdown is not stored: only the handoff remains
        ticket = store.get_ticket(session, ticket_id)
        ticket.markdown, ticket.raw_pdf_path = None, "data/raw/rfp/does-not-exist.pdf"
        session.add(ticket)
        session.commit()
    monkeypatch.setattr(convert, "pdf_to_markdown", lambda path: (_ for _ in ()).throw(AssertionError("the PDF must not be re-parsed")))
    assert run_proposal(ticket_id, engine) == "under_evaluation"


# ------------------------------------------------------------------------------------------------ evaluators: structured result, success and failure
def good_draft(department_id: str = "warehouse", country: str = "ES", volume: int | None = None, **aspects):
    b = brief(department_id, country, volume, **aspects)
    facts = facts_for(country)
    return b, facts, generators.generate(b, facts)


def evaluate(b, facts, draft):
    readability = evaluators.evaluate_readability(draft.content)
    relevance = evaluators.evaluate_relevance(draft.content, b, facts, draft.derived_numbers)
    compliance = evaluators.evaluate_compliance(draft.content, b, facts)
    return evaluators.consolidate(b["department_id"], f"t:{b['department_id']}", 1, readability, relevance, compliance)


def test_a_good_draft_passes_every_evaluator_with_the_expected_shape():
    b, facts, draft = good_draft(stated_figures={"skus": 15000})
    result = evaluate(b, facts, draft)
    assert result["overall_pass"] is True and result["feedback_for_generator"] == "The section passed all checks." and result["fixes"] == []
    assert set(result) >= {"section_id", "department_id", "readability", "relevance", "compliance", "overall_pass", "feedback_for_generator"}
    assert set(result["readability"]) == {"pass", "score", "details"} and isinstance(result["readability"]["score"], float)
    assert set(result["relevance"]) >= {"pass", "missing_aspects"} and set(result["compliance"]) >= {"pass", "rule_ids", "violations"}
    assert result["compliance"]["checked_rule_ids"] == ["GL-1", "GL-2", "GL-3", "GL-4", "GL-5"]


def test_readability_fails_on_a_text_that_is_hard_to_read():
    hard = ("Notwithstanding the aforementioned contractual considerations, the organisation's comprehensive infrastructure utilisation methodology necessitates "
            "multidimensional operational synchronisation, interdepartmental accountability frameworks and administrative reconciliation procedures. ") * 6
    result = evaluators.evaluate_readability(hard)
    assert result["pass"] is False and result["score"] > evaluators.MAX_GRADE and any("reading level" in p for p in result["details"]["problems"])
    assert evaluators.evaluate_readability("Too short to be a section.")["pass"] is False


def test_relevance_fails_when_the_draft_ignores_what_the_rfp_asks():
    b = brief("warehouse", "ES", stated_figures={"skus": 15000}, asks_of_owner=["Confirm that the client's systems can be integrated (ERP or API)."])
    facts = facts_for("ES")
    result = evaluators.evaluate_relevance("## Warehousing\nWe store goods in our building. Our staff is friendly. Prices follow.", b, facts)
    assert result["pass"] is False
    assert "storage capacity" in result["missing_aspects"] and "onboarding time" in result["missing_aspects"]
    assert any("15,000 SKUs" in aspect for aspect in result["missing_aspects"]) and any("integration" in aspect for aspect in result["missing_aspects"])


def test_relevance_rejects_a_figure_that_is_in_neither_the_rfp_nor_the_knowledge_base():
    b, facts, draft = good_draft(stated_figures={"skus": 15000})
    result = evaluators.evaluate_relevance(draft.content + "\nThe warehouse holds 12,500 pallets today.", b, facts, draft.derived_numbers)
    assert result["pass"] is False and "12,500" in result["unsupported_figures"]


def test_a_failed_evaluation_gives_the_generator_concrete_actionable_feedback():
    b, facts, draft = good_draft(stated_figures={"skus": 15000})
    broken = Draft(draft.content.replace("EUR", "USD").replace("| Volume", "| x"))
    result = evaluate(b, facts, broken)
    assert result["overall_pass"] is False and result["compliance"]["pass"] is False and "GL-1" in result["compliance"]["rule_ids"]
    assert result["feedback_for_generator"].startswith("Fix these before the next draft:") and "GL-1" in result["feedback_for_generator"] and "EUR" in result["feedback_for_generator"]
    assert result["fixes"] and all({"kind", "id", "instruction"} <= set(fix) for fix in result["fixes"])


# ------------------------------------------------------------------------------------------------ compliance: one test per rule of CONTEXT section 5
def violated(content: str, country: str = "ES", sla: float | None = 90.0) -> dict:
    return rules.check_all(content, country, sla)


TABLE = "| Monthly volume (orders) | Discount |\n|---|---|\n| Up to 100 | None |\n| Above 100 | To be set |\n"
BASE = "Prices in EUR. TrackFlow's on-time delivery SLA is 90% per month.\n" + TABLE


def test_the_base_text_used_below_is_itself_compliant():
    assert violated(BASE) == {}


def test_compliance_failure_tied_to_the_context_a_draft_that_promises_returns_in_24_hours():
    """The CONTEXT-anchored case: section 5 forbids promising returns processing in under 48 hours."""
    b, facts, draft = good_draft("reverse", stated_figures={"process_steps_requested": ["inspection"]})
    forbidden = Draft(draft.content + "\nWe guarantee returns processing within 24 hours of receiving the parcel.")
    result = evaluators.evaluate_compliance(forbidden.content, b, facts)
    assert result["pass"] is False and result["rule_ids"] == ["GL-3"] and "24 hours" in result["violations"][0]


@pytest.mark.parametrize(
    "promise",
    [
        "Returns are processed within 24 hours.",
        "Return turnaround is 36 hours.",
        "We process returns in 1 business day.",
        "Same-day processing of every return.",
        "Returns turnaround: 24 to 48 hours.",
        "We process returns in under 48 hours.",
    ],
)
def test_every_way_of_promising_returns_in_under_48_hours_is_caught(promise):
    assert "GL-3" in violated(BASE + promise)


@pytest.mark.parametrize("fine", ["Returns turnaround is never shorter than 48 hours.", "Returns turnaround: 2 business days.", "The standard return window is 30 days from delivery.", "Express shipping: 24 to 48 hours within the same country."])
def test_statements_that_do_not_promise_fast_returns_are_not_flagged(fine):
    assert "GL-3" not in violated(BASE + fine)


def test_the_currency_must_match_the_country():
    assert "GL-1" in violated("Prices in USD. SLA of on-time 90%.\n" + TABLE, country="ES")  # Spain is quoted in EUR
    assert "GL-1" in violated("Storage costs 18 USD and 16 EUR per cubic meter.\n" + TABLE, country="US")  # mixing is wrong too
    assert "GL-1" in violated("The price is to be confirmed. SLA on-time 90%.\n" + TABLE, country="ES")  # no currency at all
    assert "GL-1" in violated(BASE, country=None)  # without a country the currency cannot be decided
    assert violated(BASE.replace("EUR", "USD"), country="US") == {}


def test_the_sla_must_be_stated_and_must_be_the_contractual_one():
    assert "GL-2" in violated("Prices in EUR.\n" + TABLE)
    assert "GL-2" in violated(BASE.replace("90%", "99%"))  # a promise TrackFlow does not make
    assert "GL-2" not in violated(BASE.replace("90%", "99%"), sla=None)  # nothing to compare with: only presence is checked


def test_a_volume_based_discount_table_is_required():
    assert "GL-4" in violated("Prices in EUR. On-time SLA 90%. Discounts are available.")
    assert "GL-4" in violated("Prices in EUR. On-time SLA 90%.\n| Item | Price |\n|---|---|\n| Storage | 16 EUR |\n| Pallet | tbc |\n")  # a table, but not a discount table
    assert "GL-4" in violated("Prices in EUR. On-time SLA 90%.\n| Monthly volume | Discount |\n|---|---|\n| Up to 100 | None |\n")  # a single tier is not a tier table


@pytest.mark.parametrize(
    "disclosure",
    ["Our negotiated rate with UPS is 3.10 EUR per parcel.", "SEUR charges us 4 EUR per package.", "The contract rate with DHL gives you a discount.", "| UPS | 2.50 EUR |"],
)
def test_negotiated_carrier_rates_are_never_disclosed(disclosure):
    assert "GL-5" in violated(BASE + disclosure)


def test_naming_the_carriers_and_what_they_cover_is_allowed():
    assert "GL-5" not in violated(BASE + "TrackFlow can use UPS, FedEx and DHL. UPS has full national coverage.\nThe final cost per shipment is 5 EUR.")


# ------------------------------------------------------------------------------------------------ the loop (stand-in generators, real graph)
def run_sections(briefs: list[dict], country: str, max_iterations: int, reporter=None) -> dict:
    graph = drafting_graph.compile_graph()
    handoff = {"ticket_id": "t", "rfp_id": "TF-RFP-2026-0001", "schema_version": 1, "client": {"name": "Acme", "country": country, "currency": {"US": "USD", "ES": "EUR"}[country]}, "summary": "s",
               "workstreams": [{"department_id": b["department_id"], "key_aspects": b["key_aspects"]} for b in briefs], "departments_needed": [b["department_id"] for b in briefs], "services_requested": [], "monthly_volume": None}
    config = {"configurable": {"reporter": reporter}} if reporter else {}
    return graph.invoke({"ticket_id": "t", "handoff": handoff, "facts": drafting_graph.facts_state(country), "max_iterations": max_iterations, "sections": {}, "steps": []}, config)


class Recorder(drafting_graph.NullReporter):
    def __init__(self):
        self.events, self.lock = [], threading.Lock()

    def note(self, *event):
        with self.lock:
            self.events.append((time.monotonic(), *event))

    def generating(self, department_id, iteration):
        self.note("generating", department_id, iteration)

    def drafted(self, department_id, iteration, content, engine):
        self.note("drafted", department_id, iteration)

    def evaluated(self, department_id, iteration, result, outcome):
        self.note("evaluated", department_id, iteration, outcome)


def test_a_failed_section_goes_back_to_its_generator_with_the_feedback_and_then_passes(monkeypatch):
    seen_feedback = []
    real = generators.generate

    def flawed_then_fixed(b, facts, feedback=None, previous=None):
        seen_feedback.append(feedback)
        draft = real(b, facts, feedback, previous)
        if feedback is None:  # first attempt: the draft quotes the wrong currency
            return Draft(draft.content.replace("EUR", "USD"), derived_numbers=draft.derived_numbers)
        return draft

    monkeypatch.setattr(generators, "generate", flawed_then_fixed)
    reporter = Recorder()
    state = run_sections([brief("warehouse", "ES")], "ES", 3, reporter)
    section = state["sections"]["warehouse"]
    assert section["status"] == "passed" and section["iterations"] == 2
    assert seen_feedback[0] is None and "GL-1" in seen_feedback[1]["text"] and seen_feedback[1]["fixes"]  # the generator received feedback_for_generator
    assert section["history"][0]["overall_pass"] is False and "GL-1" in section["history"][0]["feedback_for_generator"]
    assert [e[1:] for e in reporter.events if e[1] == "evaluated"] == [("evaluated", "warehouse", 1, None), ("evaluated", "warehouse", 2, "passed")]


def test_the_loop_is_bounded_and_the_last_draft_with_its_evaluation_is_kept(monkeypatch):
    attempts = []
    real = generators.generate

    def never_compliant(b, facts, feedback=None, previous=None):
        attempts.append(feedback)
        draft = real(b, facts, feedback, previous)
        return Draft(draft.content.replace("EUR", "USD") + f"\nAttempt marker {len(attempts)}.", derived_numbers=draft.derived_numbers + [str(len(attempts))])

    monkeypatch.setattr(generators, "generate", never_compliant)
    state = run_sections([brief("warehouse", "ES")], "ES", 3)
    section = state["sections"]["warehouse"]
    assert len(attempts) == 3  # the limit: three attempts and no fourth
    assert section["status"] == "needs_human_review" and section["iterations"] == 3
    assert "Attempt marker 3." in section["content"]  # the LAST draft, not the first
    assert section["evaluation_result"]["overall_pass"] is False and section["evaluation_result"]["iteration"] == 3 and "GL-1" in section["evaluation_result"]["compliance"]["rule_ids"]
    assert len(section["history"]) == 2
    part3 = state["part3_handoff"]
    assert part3["status"] == "needs_human_review" and part3["needs_human_review"] == ["warehouse"] and part3["sections"][0]["section_status"] == "needs_human_review"


def test_one_section_exhausting_its_iterations_does_not_drop_or_block_the_others(monkeypatch):
    real = generators.generate

    def only_warehouse_fails(b, facts, feedback=None, previous=None):
        draft = real(b, facts, feedback, previous)
        return Draft(draft.content.replace("EUR", "USD"), derived_numbers=draft.derived_numbers) if b["department_id"] == "warehouse" else draft

    monkeypatch.setattr(generators, "generate", only_warehouse_fails)
    state = run_sections([brief("warehouse", "ES"), brief("reverse", "ES", stated_figures={"process_steps_requested": ["inspection"]})], "ES", 2)
    assert {key: value["status"] for key, value in state["sections"].items()} == {"warehouse": "needs_human_review", "reverse": "passed"}
    handed = {section["department_id"]: section for section in state["part3_handoff"]["sections"]}
    assert set(handed) == {"warehouse", "reverse"}  # both reach Part 3, with content and a structured evaluation
    assert all(section["content"] and set(section["evaluation_result"]) >= {"readability", "relevance", "compliance", "overall_pass", "feedback_for_generator"} for section in handed.values())
    assert handed["reverse"]["iterations"] == 1 and handed["warehouse"]["iterations"] == 2


def test_the_three_evaluators_run_in_parallel(monkeypatch):
    spans = {}
    lock = threading.Lock()

    def slow(name, real):
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            time.sleep(0.4)
            result = real(*args, **kwargs)
            with lock:
                spans[name] = (start, time.monotonic())
            return result

        return wrapper

    for name in ("evaluate_readability", "evaluate_relevance", "evaluate_compliance"):
        monkeypatch.setattr(evaluators, name, slow(name, getattr(evaluators, name)))
    started = time.monotonic()
    run_sections([brief("warehouse", "ES")], "ES", 1)
    elapsed = time.monotonic() - started
    latest_start = max(start for start, _ in spans.values())
    earliest_end = min(end for _, end in spans.values())
    assert latest_start < earliest_end  # each one started before any of the others finished: they overlap
    assert elapsed < 1.1  # three 0.4 s evaluators would take 1.2 s one after another


def test_a_slow_department_does_not_block_the_others(monkeypatch):
    real = generators.generate

    def slow_warehouse(b, facts, feedback=None, previous=None):
        if b["department_id"] == "warehouse":
            time.sleep(0.8)
        return real(b, facts, feedback, previous)

    monkeypatch.setattr(generators, "generate", slow_warehouse)
    reporter = Recorder()
    run_sections([brief("warehouse", "ES"), brief("reverse", "ES", stated_figures={"process_steps_requested": ["inspection"]})], "ES", 1, reporter)
    finished = {event[2]: event[0] for event in reporter.events if event[1] == "evaluated"}
    assert finished["reverse"] < finished["warehouse"]  # reverse was evaluated and done while warehouse was still being drafted


# ------------------------------------------------------------------------------------------------ persistence and the ticket's status in real time
def test_a_clean_run_persists_drafts_and_evaluations_and_leaves_the_ticket_ready_for_part_three(engine):
    ticket_id = intake(engine, 1)
    assert run_proposal(ticket_id, engine) == "under_evaluation"  # all passed: no Part 3 status is invented
    with Session(engine) as session:
        ticket = store.get_ticket(session, ticket_id)
        assert (ticket.status, ticket.proposal_state) == ("under_evaluation", "ready_for_part_3") and ticket.handoff_state == "ready_for_part_2"
        sections = store.sections_of(session, ticket_id)
        assert [s.department_id for s in sections] == ["warehouse", "reverse"]
        for section in sections:
            result = section.evaluation_results
            assert section.draft_status == "passed" and section.iterations == 1 and section.draft_content.startswith("## ")
            assert result["overall_pass"] is True and result["section_id"] == f"{ticket_id}:{section.department_id}" and result["history"] == []
            assert section.approval_status == "pending"  # approval belongs to Part 3
        handoff = ticket.part3_handoff
        assert handoff["ticket_id"] == ticket_id and handoff["status"] == "under_evaluation" and handoff["needs_human_review"] == []
        assert [s["department_id"] for s in handoff["sections"]] == ["warehouse", "reverse"] and all(s["content"] and s["evaluation_result"]["overall_pass"] for s in handoff["sections"])
        assert handoff["client"]["currency"] == "EUR" and ticket.handoff["ticket_id"] == ticket_id  # the Part 1 handoff is untouched


def test_the_ticket_status_follows_what_is_really_happening(tmp_path, monkeypatch):
    # a file database with real connections: this test reads the ticket from OTHER threads while the departments write, which one shared in-memory connection cannot do
    engine = create_engine(f"sqlite:///{tmp_path / 'live.db'}", connect_args={"check_same_thread": False, "timeout": 30})
    SQLModel.metadata.create_all(engine)
    ticket_id = intake(engine, 2)
    observed = []

    def status() -> tuple[str, dict]:
        with Session(engine) as session:
            sections = {s.department_id: s.draft_status for s in store.sections_of(session, ticket_id)}
            return store.get_ticket(session, ticket_id).status, sections

    real_generate, real_compliance = generators.generate, evaluators.evaluate_compliance

    def spying_generate(b, facts, feedback=None, previous=None):
        observed.append(("while generating", b["department_id"], *status()))
        return real_generate(b, facts, feedback, previous)

    def spying_compliance(content, b, facts):
        observed.append(("while evaluating", b["department_id"], *status()))
        return real_compliance(content, b, facts)

    monkeypatch.setattr(generators, "generate", spying_generate)
    monkeypatch.setattr(evaluators, "evaluate_compliance", spying_compliance)
    run_proposal(ticket_id, engine)
    generating = [entry for entry in observed if entry[0] == "while generating"]
    assert generating and all(entry[2] in ("drafting", "under_evaluation") for entry in generating)  # leaving intake_complete: it is never reported as finished
    assert any(entry[2] == "drafting" for entry in generating)
    evaluating = [entry for entry in observed if entry[0] == "while evaluating"]
    assert evaluating and all(entry[3][entry[1]] == "under_evaluation" for entry in evaluating)  # the section being evaluated says so
    assert all(entry[2] in ("drafting", "under_evaluation") for entry in evaluating)
    with Session(engine) as session:
        steps = [p["step"] for p in store.get_ticket(session, ticket_id).progress]
    assert steps.index("intake_complete") < steps.index("proposal") < steps.index("draft:warehouse") < steps.index("evaluate:warehouse") < steps.index("result:warehouse") < steps.index("proposal_ready")


def test_an_exhausted_section_sets_the_ticket_to_needs_human_review_and_ships_anyway(engine, monkeypatch):
    ticket_id = intake(engine, 1)
    monkeypatch.setenv("RFP_MAX_DRAFT_ITERATIONS", "2")
    real = generators.generate

    def bad_warehouse(b, facts, feedback=None, previous=None):
        draft = real(b, facts, feedback, previous)
        return Draft(draft.content + "\nWe process returns within 24 hours.", derived_numbers=draft.derived_numbers) if b["department_id"] == "warehouse" else draft

    monkeypatch.setattr(generators, "generate", bad_warehouse)
    assert run_proposal(ticket_id, engine) == "needs_human_review"
    with Session(engine) as session:
        ticket = store.get_ticket(session, ticket_id)
        assert ticket.status == "needs_human_review" and ticket.proposal_state == "ready_for_part_3"  # not discarded, not stuck: handed to Part 3
        by_department = {s.department_id: s for s in store.sections_of(session, ticket_id)}
        warehouse = by_department["warehouse"]
        assert (warehouse.draft_status, warehouse.iterations) == ("needs_human_review", 2) and "within 24 hours" in warehouse.draft_content
        assert warehouse.evaluation_results["compliance"]["rule_ids"] == ["GL-3"] and len(warehouse.evaluation_results["history"]) == 1
        assert by_department["reverse"].draft_status == "passed"
        handoff = ticket.part3_handoff
        assert handoff["needs_human_review"] == ["warehouse"] and {s["department_id"] for s in handoff["sections"]} == {"warehouse", "reverse"}
        assert next(s for s in handoff["sections"] if s["department_id"] == "warehouse")["evaluation_result"]["overall_pass"] is False


def test_the_loop_bound_is_configurable_and_never_below_one(monkeypatch):
    assert proposal_runner.max_iterations() == 3
    monkeypatch.setenv("RFP_MAX_DRAFT_ITERATIONS", "5")
    assert proposal_runner.max_iterations() == 5
    monkeypatch.setenv("RFP_MAX_DRAFT_ITERATIONS", "0")
    assert proposal_runner.max_iterations() == 1
    monkeypatch.setenv("RFP_MAX_DRAFT_ITERATIONS", "many")
    assert proposal_runner.max_iterations() == 3


def test_a_missing_sla_in_the_knowledge_base_ends_in_human_review_instead_of_an_invented_percentage(engine):
    ticket_id = intake(engine, 2)
    assert run_proposal(ticket_id, engine, facts=drafting_graph.asdict(knowledge.facts("US", search=lambda query: []))) == "needs_human_review"
    with Session(engine) as session:
        for section in store.sections_of(session, ticket_id):
            assert section.draft_status == "needs_human_review" and "GL-2" in section.evaluation_results["compliance"]["rule_ids"] and "%" not in section.draft_content


def test_only_a_finished_intake_can_start_the_drafting(engine):
    discarded = "t-bad"
    with Session(engine) as session:
        store.create_ticket(session, ticket_id=discarded, raw_pdf_path=str(FIXTURES / "trackflow-request-3.pdf"), original_filename="x.pdf", uploaded_by="u")
    run_intake(discarded, engine)
    assert run_proposal(discarded, engine) == "not_started"
    assert run_proposal("does-not-exist", engine) == "not_started"
    with Session(engine) as session:
        assert store.get_ticket(session, discarded).status == "discarded"  # still discarded, untouched


def test_a_crash_in_the_drafting_returns_the_ticket_to_intake_complete_with_the_reason(engine, monkeypatch):
    ticket_id = intake(engine, 1)
    monkeypatch.setattr(generators, "generate", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret: postgres://u:p@h/db")))
    assert run_proposal(ticket_id, engine) == "intake_complete"
    with Session(engine) as session:
        ticket = store.get_ticket(session, ticket_id)
        assert (ticket.status, ticket.proposal_state) == ("intake_complete", "failed") and "postgres" not in ticket.error_message and ticket.handoff_state == "ready_for_part_2"


def test_drafting_interrupted_by_a_restart_goes_back_to_intake_complete(engine):
    ticket_id = intake(engine, 1)
    with Session(engine) as session:
        proposal_store.mark_queued(session, ticket_id)
        assert proposal_store.interrupt_stale(session) == 1
        ticket = store.get_ticket(session, ticket_id)
        assert (ticket.status, ticket.proposal_state) == ("intake_complete", "interrupted")
    assert run_proposal(ticket_id, engine) == "under_evaluation"  # and it can simply be started again


def test_the_part_two_columns_are_added_to_an_existing_part_one_database(engine):
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE rfp_tickets DROP COLUMN proposal_state"))
        connection.execute(text("ALTER TABLE rfp_tickets DROP COLUMN part3_handoff"))
        connection.execute(text("ALTER TABLE rfp_department_sections DROP COLUMN draft_status"))
        connection.execute(text("ALTER TABLE rfp_department_sections DROP COLUMN iterations"))
    proposal_store.ensure_columns(engine)
    proposal_store.ensure_columns(engine)  # idempotent
    inspector = inspect(engine)
    assert {"proposal_state", "part3_handoff"} <= {c["name"] for c in inspector.get_columns("rfp_tickets")}
    assert {"draft_status", "iterations", "draft_engine"} <= {c["name"] for c in inspector.get_columns("rfp_department_sections")}
    ticket_id = intake(engine, 1)  # and the pipeline works on the migrated tables
    assert run_proposal(ticket_id, engine) == "under_evaluation"


def test_the_drafting_graph_is_dedicated_and_has_the_expected_nodes():
    outer = set(drafting_graph.compile_graph().get_graph().nodes) - {"__start__", "__end__"}
    inner = set(drafting_graph.section_graph().get_graph().nodes) - {"__start__", "__end__"}
    assert outer == {"assign", "warehouse_section", "lastmile_section", "reverse_section", "finalize"}
    assert inner == {"generate", "evaluate_readability", "evaluate_relevance", "evaluate_compliance", "decide"}
