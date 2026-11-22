"""RFP intake through the API (Milestone 9, part 1): upload, asynchronous analysis, polling, validation.

The three sample PDFs of CONTEXT-company.md are uploaded exactly as the backoffice does it (multipart). The pipeline is the real one; only the model is absent, so
the rules decide (the same as in tests/pipelines/test_rfp_intake.py).
"""

from pathlib import Path

import pytest

from data.pipelines.rfp_intake import store
from services.routers import rfp as rfp_router

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "pipelines" / "fixtures" / "rfp-requests"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path / "raw-rfp"))
    monkeypatch.setenv("RFP_AUTO_DRAFT", "false")  # these tests are about the intake; test_rfp_proposal_api.py covers the drafting that follows it


def upload(client, number: int | None = None, *, name: str | None = None, content: bytes | None = None):
    if content is None:
        content = (FIXTURES / f"trackflow-request-{number}.pdf").read_bytes()
    return client.post("/rfp/tickets", files={"file": (name or f"trackflow-request-{number}.pdf", content, "application/pdf")})


def test_the_upload_answers_202_with_an_analyzing_ticket_and_stores_the_pdf(incident_client, tmp_path):
    response = upload(incident_client, 1)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "analyzing" and body["rfp_id"].startswith("TF-RFP-") and body["original_filename"] == "trackflow-request-1.pdf"
    stored = tmp_path / "raw-rfp" / f"{body['ticket_id']}.pdf"
    assert stored.read_bytes() == (FIXTURES / "trackflow-request-1.pdf").read_bytes()  # the original, untouched


def test_the_pipeline_runs_after_the_response_not_inside_the_upload(incident_client, monkeypatch):
    scheduled = []
    monkeypatch.setattr(rfp_router, "run_intake", lambda ticket_id, engine: scheduled.append(ticket_id))  # the task is handed to the background, and does nothing here
    ticket_id = upload(incident_client, 2).json()["ticket_id"]
    assert scheduled == [ticket_id]
    polled = incident_client.get(f"/rfp/tickets/{ticket_id}").json()
    assert polled["status"] == "analyzing" and polled["classification"]["is_rfp"] is None and polled["sections"] == []  # nothing ran synchronously


def test_the_formal_rfp_ends_intake_complete_with_the_departments_and_their_owners(incident_client):
    ticket_id = upload(incident_client, 1).json()["ticket_id"]
    ticket = incident_client.get(f"/rfp/tickets/{ticket_id}").json()
    assert ticket["status"] == "intake_complete" and ticket["classification"]["document_type"] == "formal_rfp"
    assert ticket["metadata"]["client_name"] == "ModaViva, S.L." and ticket["metadata"]["client_country"] == "ES" and ticket["metadata"]["currency"] == "EUR"
    assert [(section["department_id"], section["owner"]) for section in ticket["sections"]] == [("warehouse", "Ana Whitfield"), ("reverse", "Sofía Ramos")]
    assert all(section["key_aspects"]["asks_of_owner"] for section in ticket["sections"])
    assert ticket["readability"]["cost_band"] == "low" and ticket["readability"]["word_count"] > 100
    assert "Ana Whitfield" in ticket["summary"] and ticket["handoff_state"] == "ready_for_part_2"
    assert ticket["handoff"]["ticket_id"] == ticket_id and len(ticket["handoff"]["workstreams"]) == 2
    assert ticket["raw_pdf_path"].endswith(f"{ticket_id}.pdf")


def test_the_informal_rfp_triggers_warehouse_and_last_mile_in_dollars(incident_client):
    ticket = incident_client.get(f"/rfp/tickets/{upload(incident_client, 2).json()['ticket_id']}").json()
    assert ticket["status"] == "intake_complete" and ticket["metadata"]["monthly_volume"] == 5000 and ticket["metadata"]["currency"] == "USD"
    assert [section["department_id"] for section in ticket["sections"]] == ["warehouse", "lastmile"]


def test_the_invalid_document_is_discarded_with_the_reason(incident_client):
    ticket = incident_client.get(f"/rfp/tickets/{upload(incident_client, 3).json()['ticket_id']}").json()
    assert ticket["status"] == "discarded" and ticket["classification"]["is_rfp"] is False
    assert ticket["sections"] == [] and ticket["handoff_state"] == "not_ready" and "offering" in ticket["classification"]["reason"]


def test_a_rejected_ticket_does_not_stop_the_others_and_the_list_shows_all(incident_client):
    ids = [upload(incident_client, number).json()["ticket_id"] for number in (3, 1, 2)]
    tickets = incident_client.get("/rfp/tickets").json()
    assert [ticket["ticket_id"] for ticket in tickets] == ids[::-1]  # newest first
    assert {ticket["ticket_id"]: ticket["status"] for ticket in tickets} == {ids[0]: "discarded", ids[1]: "intake_complete", ids[2]: "intake_complete"}


def test_a_pdf_that_cannot_be_read_fails_that_ticket_with_a_message(incident_client):
    ticket_id = upload(incident_client, name="damaged.pdf", content=b"%PDF-1.4\nthis is not a real pdf").json()["ticket_id"]
    ticket = incident_client.get(f"/rfp/tickets/{ticket_id}").json()
    assert ticket["status"] == "failed" and "PDF" in ticket["error_message"]
    assert upload(incident_client, 2).status_code == 202  # the service keeps working


@pytest.mark.parametrize(
    "name, content, status_code",
    [
        ("notes.txt", b"%PDF-1.4 hello", 415),  # not a .pdf
        ("fake.pdf", b"<html>this is not a pdf</html>", 415),  # a .pdf name, not a PDF
        ("empty.pdf", b"", 400),
    ],
)
def test_files_that_are_not_pdfs_are_refused_without_creating_a_ticket(incident_client, tmp_path, name, content, status_code):
    assert upload(incident_client, name=name, content=content).status_code == status_code
    assert incident_client.get("/rfp/tickets").json() == []
    assert not (tmp_path / "raw-rfp").exists() or not list((tmp_path / "raw-rfp").glob("*"))  # no orphan file


def test_a_pdf_over_the_size_limit_is_refused(incident_client, monkeypatch):
    monkeypatch.setattr(rfp_router, "MAX_UPLOAD_BYTES", 1000)
    response = upload(incident_client, name="big.pdf", content=b"%PDF-1.4" + b"0" * 2000)
    assert response.status_code == 413 and "too large" in response.json()["detail"]


def test_the_client_filename_never_decides_where_the_file_goes(incident_client, tmp_path):
    body = upload(incident_client, 1, name="../../../../evil.pdf").json()
    assert body["original_filename"] == "evil.pdf"
    assert [path.name for path in (tmp_path / "raw-rfp").glob("*")] == [f"{body['ticket_id']}.pdf"]


def test_the_endpoints_need_a_signed_in_user(incident_client):
    incident_client.headers.pop("Authorization")
    assert upload(incident_client, 1).status_code == 401
    assert incident_client.get("/rfp/tickets").status_code == 401
    assert incident_client.get("/rfp/tickets/anything").status_code == 401


def test_an_unknown_ticket_is_a_404(incident_client):
    assert incident_client.get("/rfp/tickets/does-not-exist").status_code == 404


def test_the_ticket_is_in_the_database_not_in_a_file(incident_client, incident_engine):
    from sqlmodel import Session

    ticket_id = upload(incident_client, 1).json()["ticket_id"]
    with Session(incident_engine) as session:
        ticket = store.get_ticket(session, ticket_id)
        assert ticket.status == "intake_complete" and [section.department_id for section in store.sections_of(session, ticket_id)] == ["warehouse", "reverse"]
