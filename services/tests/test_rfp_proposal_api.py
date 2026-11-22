"""RFP proposal drafting through the API (Milestone 9, part 2): the workflow continues by itself after the intake, the ticket shows the drafts and their evaluations, and it can be started by hand.

Same backend, same database: no second service. The pipeline is the real one; no model is configured, so the generators and evaluators use their rules.
"""

from pathlib import Path

import pytest

from data.pipelines.rfp_intake import generators
from data.pipelines.rfp_intake.generators import Draft

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "pipelines" / "fixtures" / "rfp-requests"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path / "raw-rfp"))
    monkeypatch.delenv("RFP_AUTO_DRAFT", raising=False)
    monkeypatch.delenv("RFP_MAX_DRAFT_ITERATIONS", raising=False)


def upload(client, number: int):
    content = (FIXTURES / f"trackflow-request-{number}.pdf").read_bytes()
    return client.post("/rfp/tickets", files={"file": (f"trackflow-request-{number}.pdf", content, "application/pdf")})


def ticket(client, ticket_id: str) -> dict:
    return client.get(f"/rfp/tickets/{ticket_id}").json()


def test_the_workflow_continues_from_the_intake_to_evaluated_drafts(incident_client):
    created = upload(incident_client, 2)
    assert created.status_code == 202 and created.json()["status"] == "analyzing"
    body = ticket(incident_client, created.json()["ticket_id"])
    assert body["status"] == "under_evaluation" and body["proposal_state"] == "ready_for_part_3"
    assert [section["department_id"] for section in body["sections"]] == ["warehouse", "lastmile"]
    for section in body["sections"]:
        result = section["evaluation_result"]
        assert section["draft_status"] == "passed" and section["iterations"] == 1 and section["draft_content"].startswith("## ")
        assert set(result["readability"]) == {"pass", "score", "details"} and result["relevance"]["missing_aspects"] == [] and result["compliance"]["violations"] == []
        assert result["overall_pass"] is True and result["feedback_for_generator"]
        assert section["approval_status"] == "pending"
    assert "USD" in body["sections"][0]["draft_content"] and "EUR" not in body["sections"][0]["draft_content"]  # Luna Cosmetics is a US client


def test_the_spanish_client_is_quoted_in_euros_and_gets_no_last_mile_section(incident_client):
    body = ticket(incident_client, upload(incident_client, 1).json()["ticket_id"])
    assert [section["department_id"] for section in body["sections"]] == ["warehouse", "reverse"]
    assert all("EUR" in section["draft_content"] and "USD" not in section["draft_content"] for section in body["sections"])
    assert "16 EUR per cubic meter per month" in body["sections"][0]["draft_content"]


def test_the_part_three_handoff_carries_every_department_with_content_and_its_evaluation(incident_client):
    body = ticket(incident_client, upload(incident_client, 1).json()["ticket_id"])
    handoff = body["part3_handoff"]
    assert handoff["ticket_id"] == body["ticket_id"] and handoff["rfp_id"] == body["rfp_id"] and handoff["needs_human_review"] == []
    assert [section["department_id"] for section in handoff["sections"]] == ["warehouse", "reverse"]
    for section in handoff["sections"]:
        assert section["content"] and section["section_status"] == "passed" and section["evaluation_result"]["overall_pass"] is True
        assert {"readability", "relevance", "compliance", "feedback_for_generator"} <= set(section["evaluation_result"])
    assert body["handoff_state"] == "ready_for_part_2" and body["handoff"]["ticket_id"] == body["ticket_id"]  # the intake handoff is untouched


def test_a_section_that_exhausts_its_iterations_ships_as_needs_human_review(incident_client, monkeypatch):
    monkeypatch.setenv("RFP_MAX_DRAFT_ITERATIONS", "2")
    real = generators.generate

    def promises_fast_returns(b, facts, feedback=None, previous=None):
        draft = real(b, facts, feedback, previous)
        if b["department_id"] == "reverse":
            return Draft(draft.content + "\nWe process every return within 24 hours.", derived_numbers=draft.derived_numbers)
        return draft

    monkeypatch.setattr(generators, "generate", promises_fast_returns)
    body = ticket(incident_client, upload(incident_client, 1).json()["ticket_id"])
    assert body["status"] == "needs_human_review" and body["proposal_state"] == "ready_for_part_3"
    reverse = next(section for section in body["sections"] if section["department_id"] == "reverse")
    assert reverse["draft_status"] == "needs_human_review" and reverse["iterations"] == 2 and "24 hours" in reverse["draft_content"]
    assert reverse["evaluation_result"]["compliance"]["rule_ids"] == ["GL-3"] and reverse["evaluation_result"]["overall_pass"] is False
    assert next(section for section in body["sections"] if section["department_id"] == "warehouse")["draft_status"] == "passed"
    assert body["part3_handoff"]["needs_human_review"] == ["reverse"] and len(body["part3_handoff"]["sections"]) == 2  # nothing dropped


def test_a_rejected_document_never_reaches_the_drafting(incident_client):
    body = ticket(incident_client, upload(incident_client, 3).json()["ticket_id"])
    assert body["status"] == "discarded" and body["proposal_state"] == "not_started" and body["sections"] == [] and body["part3_handoff"] == {}


def test_without_auto_draft_the_ticket_waits_at_intake_complete_and_can_be_started_by_hand(incident_client, monkeypatch):
    monkeypatch.setenv("RFP_AUTO_DRAFT", "false")
    ticket_id = upload(incident_client, 2).json()["ticket_id"]
    assert ticket(incident_client, ticket_id)["status"] == "intake_complete"
    started = incident_client.post(f"/rfp/tickets/{ticket_id}/proposal")
    assert started.status_code == 202
    body = ticket(incident_client, ticket_id)
    assert body["status"] == "under_evaluation" and body["proposal_state"] == "ready_for_part_3" and len(body["sections"]) == 2


def test_the_proposal_cannot_be_started_for_a_ticket_that_is_not_ready(incident_client, monkeypatch):
    monkeypatch.setenv("RFP_AUTO_DRAFT", "false")
    discarded = upload(incident_client, 3).json()["ticket_id"]
    assert incident_client.post(f"/rfp/tickets/{discarded}/proposal").status_code == 409
    assert incident_client.post("/rfp/tickets/does-not-exist/proposal").status_code == 404
    incident_client.headers.pop("Authorization")
    assert incident_client.post(f"/rfp/tickets/{discarded}/proposal").status_code == 401


def test_the_list_tells_the_page_when_a_ticket_is_still_in_progress(incident_client):
    upload(incident_client, 1)
    entry = incident_client.get("/rfp/tickets").json()[0]
    assert entry["status"] == "under_evaluation" and entry["proposal_state"] == "ready_for_part_3"
