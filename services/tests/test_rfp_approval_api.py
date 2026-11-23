"""RFP approval through the API (Milestone 9, part 3): the workflow runs from the upload to the paused approval gates by itself, owners decide through the API, and the final document appears.

Same backend and database: no second service. The pipeline and the graphs are the real ones; no model is configured. Decisions are registered by staff accounts (the owners are named people).
"""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "pipelines" / "fixtures" / "rfp-requests"
OWNERS = {"warehouse": "Ana Whitfield", "lastmile": "Carlos Vega", "reverse": "Sofía Ramos"}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL", "RFP_AUTO_DRAFT", "RFP_AUTO_APPROVAL", "RFP_MAX_APPROVAL_ROUNDS", "RFP_MAX_ARBITRATION_ROUNDS", "RFP_MAX_DRAFT_ITERATIONS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path / "raw-rfp"))
    monkeypatch.setenv("RFP_CHECKPOINT_DB", ":memory:")
    monkeypatch.setenv("RFP_APPROVER_ROLES", "user,manager,admin")  # the registered test account stands for the staff that records the owners' decisions


def upload(client, number: int) -> str:
    content = (FIXTURES / f"trackflow-request-{number}.pdf").read_bytes()
    response = client.post("/rfp/tickets", files={"file": (f"trackflow-request-{number}.pdf", content, "application/pdf")})
    assert response.status_code == 202
    return response.json()["ticket_id"]


def ticket(client, ticket_id: str) -> dict:
    return client.get(f"/rfp/tickets/{ticket_id}").json()


def decide(client, ticket_id: str, department_id: str, decision: str = "approve", **extra):
    body = {"decision": decision, "approver": OWNERS[department_id], **extra}
    return client.post(f"/rfp/tickets/{ticket_id}/sections/{department_id}/decision", json=body)


def test_after_the_upload_the_ticket_waits_for_the_owners_with_every_gate_open(incident_client):
    body = ticket(incident_client, upload(incident_client, 2))
    assert body["status"] == "waiting_for_approval" and body["approval_state"] == "waiting" and "Ana Whitfield" in body["current_step"] and "Carlos Vega" in body["current_step"]
    assert [(s["department_id"], s["responsible"], s["awaiting_approval"], s["approval_status"]) for s in body["sections"]] == [("warehouse", "Ana Whitfield", True, "pending"), ("lastmile", "Carlos Vega", True, "pending")]
    assert body["document_ready"] is False and incident_client.get(f"/rfp/tickets/{body['ticket_id']}/document").status_code == 404


def test_the_owners_approve_independently_and_the_final_document_appears_when_the_last_one_does(incident_client):
    ticket_id = upload(incident_client, 1)
    first = decide(incident_client, ticket_id, "reverse")
    assert first.status_code == 200 and first.json()["waiting"] == ["warehouse"] and first.json()["ticket_status"] == "waiting_for_approval"
    midway = ticket(incident_client, ticket_id)
    assert midway["status"] == "waiting_for_approval" and {s["department_id"]: s["approval_status"] for s in midway["sections"]} == {"warehouse": "pending", "reverse": "approved"}
    assert incident_client.get(f"/rfp/tickets/{ticket_id}/document").status_code == 404  # one department decided, the other did not: nothing final yet
    last = decide(incident_client, ticket_id, "warehouse")
    assert last.json()["ticket_status"] == "done" and last.json()["waiting"] == []
    done = ticket(incident_client, ticket_id)
    assert done["status"] == "done" and done["approval_state"] == "complete" and done["document_ready"] is True and done["current_step"] == "Final document generated"
    assert all(s["approver"] == s["responsible"] and s["approved_at"] and s["approval_status"] == "approved" for s in done["sections"])


def test_the_final_document_is_accessible_as_json_and_as_a_markdown_download(incident_client):
    ticket_id = upload(incident_client, 1)
    decide(incident_client, ticket_id, "warehouse")
    decide(incident_client, ticket_id, "reverse")
    document = incident_client.get(f"/rfp/tickets/{ticket_id}/document").json()
    assert document["ticket_id"] == ticket_id and document["currency"] == "EUR" and [s["department_id"] for s in document["sections"]] == ["warehouse", "reverse"]
    assert document["content_markdown"].startswith("# Logistics proposal for ModaViva, S.L.") and document["generated_at"]
    download = incident_client.get(f"/rfp/tickets/{ticket_id}/document", params={"format": "markdown"})
    assert download.status_code == 200 and download.headers["content-type"].startswith("text/markdown") and "attachment" in download.headers["content-disposition"] and download.text == document["content_markdown"]


def test_the_trace_endpoint_tells_which_agent_did_what_in_what_order(incident_client):
    ticket_id = upload(incident_client, 1)
    decide(incident_client, ticket_id, "warehouse")
    decide(incident_client, ticket_id, "reverse")
    events = incident_client.get(f"/rfp/tickets/{ticket_id}/trace").json()["events"]
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1)) and [e["part"] for e in events] == sorted(e["part"] for e in events)
    assert events[0]["agent"] == "intake-api" and events[-1]["agent"] == "ticket" and events[-1]["output"] == {"to": "done"}
    assert all(e["agent"] and e["node"] and e["at"] and "input" in e and "output" in e for e in events)
    assert {e["agent"] for e in events if e["part"] == 3} >= {"approval-orchestrator", "approval-gate", "human:Ana Whitfield", "human:Sofía Ramos", "arbitration", "document-synthesizer"}
    only_part_three = incident_client.get(f"/rfp/tickets/{ticket_id}/trace", params={"part": 3}).json()["events"]
    assert only_part_three and {e["part"] for e in only_part_three} == {3}
    assert incident_client.get("/rfp/tickets/nope/trace").status_code == 404


def test_requesting_changes_returns_the_section_to_its_owner_with_a_new_draft(incident_client):
    ticket_id = upload(incident_client, 1)
    answer = decide(incident_client, ticket_id, "warehouse", "request_changes", comment="State the onboarding assumptions")
    assert answer.status_code == 200 and answer.json()["ticket_status"] == "waiting_for_approval"
    section = next(s for s in ticket(incident_client, ticket_id)["sections"] if s["department_id"] == "warehouse")
    assert section["approval_round"] == 2 and section["awaiting_approval"] is True and "State the onboarding assumptions" in section["draft_content"]
    assert [(h["round"], h["decision"]) for h in section["approval_history"]] == [(1, "request_changes")]


def test_a_conflict_is_arbitrated_and_the_ticket_keeps_waiting_until_the_section_is_approved_again(incident_client):
    ticket_id = upload(incident_client, 2)
    decide(incident_client, ticket_id, "warehouse", commitments={"capacity_orders_per_month": 3000})
    answer = decide(incident_client, ticket_id, "lastmile")
    assert answer.json()["waiting"] == ["lastmile"] and answer.json()["ticket_status"] == "waiting_for_approval"
    body = ticket(incident_client, ticket_id)
    lastmile = next(s for s in body["sections"] if s["department_id"] == "lastmile")
    assert "capped at 3,000 orders per month" in lastmile["draft_content"] and lastmile["commitments"]["assumed_monthly_volume"] == 3000
    assert decide(incident_client, ticket_id, "lastmile").json()["ticket_status"] == "done"
    assert "volume-vs-capacity (Miguel Torres)" in incident_client.get(f"/rfp/tickets/{ticket_id}/document").json()["content_markdown"]


def test_invalid_and_out_of_turn_decisions_are_refused_with_the_right_status(incident_client):
    ticket_id = upload(incident_client, 1)
    assert incident_client.post(f"/rfp/tickets/{ticket_id}/sections/warehouse/decision", json={"decision": "approve", "approver": "Carlos Vega"}).status_code == 422
    assert incident_client.post(f"/rfp/tickets/{ticket_id}/sections/warehouse/decision", json={"decision": "reject", "approver": "Ana Whitfield"}).status_code == 422  # a comment is required
    assert incident_client.post(f"/rfp/tickets/{ticket_id}/sections/warehouse/decision", json={"decision": "approve"}).status_code == 422  # no approver at all
    assert decide(incident_client, ticket_id, "lastmile").status_code == 404  # ModaViva has no last-mile section
    assert decide(incident_client, "nope", "warehouse").status_code == 404
    assert decide(incident_client, ticket_id, "warehouse").status_code == 200
    assert decide(incident_client, ticket_id, "warehouse").status_code == 409  # already approved
    decide(incident_client, ticket_id, "reverse")
    assert decide(incident_client, ticket_id, "reverse").status_code == 409  # the ticket is done


def test_only_a_staff_account_can_register_an_owners_decision(incident_client, monkeypatch):
    ticket_id = upload(incident_client, 1)
    monkeypatch.setenv("RFP_APPROVER_ROLES", "admin,manager")  # the default: a plain account is not staff
    from services import accounts

    accounts.update_user(incident_client.get("/auth/me").json()["id"], role=accounts.Role.user)  # the account falls back to a plain `user`: not staff
    assert decide(incident_client, ticket_id, "warehouse").status_code == 403
    incident_client.headers.pop("Authorization")
    assert decide(incident_client, ticket_id, "warehouse").status_code == 401
    assert incident_client.get(f"/rfp/tickets/{ticket_id}/trace").status_code == 401 and incident_client.get(f"/rfp/tickets/{ticket_id}/document").status_code == 401


def test_the_approvals_can_be_started_by_hand_when_they_are_not_automatic(incident_client, monkeypatch):
    monkeypatch.setenv("RFP_AUTO_APPROVAL", "false")
    ticket_id = upload(incident_client, 1)
    body = ticket(incident_client, ticket_id)
    assert body["status"] == "under_evaluation" and body["proposal_state"] == "ready_for_part_3" and body["approval_state"] == "not_started"
    assert decide(incident_client, ticket_id, "warehouse").status_code == 409  # the gates are not open yet
    started = incident_client.post(f"/rfp/tickets/{ticket_id}/approval")
    assert started.status_code == 202 and started.json()["status"] == "waiting_for_approval"
    assert incident_client.post(f"/rfp/tickets/{ticket_id}/approval").status_code == 409  # only once
    assert incident_client.post("/rfp/tickets/nope/approval").status_code == 404


def test_concurrent_tickets_keep_their_own_gates(incident_client):
    first, second = upload(incident_client, 1), upload(incident_client, 2)
    decide(incident_client, first, "warehouse")
    decide(incident_client, first, "reverse")
    assert ticket(incident_client, first)["status"] == "done" and ticket(incident_client, second)["status"] == "waiting_for_approval"
    assert all(s["approval_status"] == "pending" for s in ticket(incident_client, second)["sections"])


def test_a_rejected_document_has_no_approvals_and_no_document(incident_client):
    ticket_id = upload(incident_client, 3)
    body = ticket(incident_client, ticket_id)
    assert body["status"] == "discarded" and body["approval_state"] == "not_started" and incident_client.get(f"/rfp/tickets/{ticket_id}/document").status_code == 404
    assert incident_client.post(f"/rfp/tickets/{ticket_id}/approval").status_code == 409
