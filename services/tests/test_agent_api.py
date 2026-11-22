"""POST /agent/query and GET /agent/runs/{id}/trace: the endpoint only invokes the graph and never exposes internals."""

import inspect
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from data.pipelines import rag
from data.process import model_client
from services import database
from services.accounts import Role, User
from services.auth import get_current_user
from services.main import app
from services.routers import agent as agent_router

CHUNK = {"source_document": "sla-delivery", "section": "Delivery SLA > High-demand dates (no SLA guarantee)", "chunk_index": 2, "text": "TrackFlow does not guarantee delivery SLA during declared high-demand dates.", "score": 0.91}


@pytest.fixture
def pipeline(monkeypatch, incident_engine):
    """The RAG steps are stubbed; the graph, the trace table and the checkpoints are the real ones."""
    monkeypatch.setattr(database, "engine", incident_engine)  # the agent writes its trace to this database
    state = {"context": [CHUNK], "answer": "No SLA is guaranteed on Black Friday.\nSource: sla-delivery", "fail": None}

    def retrieve(question, **kwargs):
        return state["context"]

    def generate_answer(question, context):
        if state["fail"]:
            raise state["fail"]
        return state["answer"]

    monkeypatch.setattr(rag, "retrieve", retrieve)
    monkeypatch.setattr(rag, "generate_answer", generate_answer)
    return state


def test_the_endpoint_needs_a_signed_in_user():
    with TestClient(app) as client:
        assert client.post("/agent/query", json={"question": "hi"}).status_code == 401


def test_a_question_returns_the_generated_answer_and_a_run_id(incident_client, pipeline):
    response = incident_client.post("/agent/query", json={"question": "Can we guarantee delivery times on Black Friday?"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "run_id"} and body["answer"].startswith("No SLA is guaranteed")
    uuid.UUID(body["run_id"])
    assert "0.91" not in response.text and CHUNK["text"] not in response.text  # no chunks, no scores to the client


def test_an_empty_question_is_a_clear_422_decided_by_the_graph_and_never_retrieves(incident_client, pipeline, monkeypatch):
    monkeypatch.setattr(rag, "retrieve", lambda *a, **k: pytest.fail("an empty question must not reach retrieval"))
    response = incident_client.post("/agent/query", json={"question": "   "})
    assert response.status_code == 422 and "type a question" in response.json()["detail"].lower()


def test_nothing_above_the_threshold_answers_honestly_with_a_200(incident_client, pipeline):
    pipeline["context"] = []
    response = incident_client.post("/agent/query", json={"question": "What is the weather in Zaragoza?"})
    assert response.status_code == 200 and "don't have information" in response.json()["answer"]


def test_a_failing_node_gives_a_clear_503_with_a_reference_and_no_internals(incident_client, pipeline):
    pipeline["fail"] = model_client.ModelServiceError("The model service answered 401 for /chat/completions. key=super-secret http://models.internal")
    response = incident_client.post("/agent/query", json={"question": "What's the standard return window?"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail.startswith("The support agent could not answer right now") and re.search(r"reference: [0-9a-f-]{36}", detail)
    for leak in ("super-secret", "models.internal", "Traceback", "ModelServiceError", "chat/completions"):
        assert leak not in response.text


def test_a_failed_run_can_be_found_by_its_reference_in_the_trace(incident_client, pipeline):
    pipeline["fail"] = model_client.ModelServiceError("down")
    reference = re.search(r"reference: ([0-9a-f-]{36})", incident_client.post("/agent/query", json={"question": "q"}).json()["detail"]).group(1)
    app.dependency_overrides[get_current_user] = lambda: User(id="1", email="m@example.com", hashed_password="x", role=Role.manager, created_at="2026-01-01T00:00:00Z")
    try:
        trace = incident_client.get(f"/agent/runs/{reference}/trace").json()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert [s["node"] for s in trace["steps"]] == ["receive_question", "retrieve_context", "generate"]
    assert trace["steps"][2]["status"] == "error" and len(trace["checkpoints"]) >= 3


def test_the_trace_endpoint_returns_the_steps_and_checkpoints_to_a_manager_only(incident_client, pipeline):
    run_id = incident_client.post("/agent/query", json={"question": "Can we guarantee delivery times on Black Friday?"}).json()["run_id"]
    assert incident_client.get(f"/agent/runs/{run_id}/trace").status_code == 403  # a plain user
    app.dependency_overrides[get_current_user] = lambda: User(id="1", email="m@example.com", hashed_password="x", role=Role.manager, created_at="2026-01-01T00:00:00Z")
    try:
        response = incident_client.get(f"/agent/runs/{run_id}/trace")
        missing = incident_client.get(f"/agent/runs/{uuid.uuid4()}/trace")
        malformed = incident_client.get("/agent/runs/not-a-uuid/trace")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    body = response.json()
    assert [s["node"] for s in body["steps"]] == ["receive_question", "retrieve_context", "generate"]
    assert body["steps"][1]["output"]["context"][0]["source_document"] == "sla-delivery" and CHUNK["text"] not in response.text
    assert [c["next"] for c in body["checkpoints"]] == [["__start__"], ["receive_question"], ["retrieve_context"], ["generate"], []]
    assert missing.status_code == 404 and malformed.status_code == 422


def test_the_router_only_invokes_the_graph_and_has_no_business_logic():
    source = inspect.getsource(agent_router)
    for forbidden in ("rag.", "retrieve", "qdrant", "embed(", "chat/completions", "min_score", "StateGraph", "generate_answer"):
        assert forbidden not in source, forbidden
    assert "agent.run_agent(" in source


def test_the_plain_rag_endpoint_still_exists_next_to_the_agent_endpoint():
    paths = app.openapi()["paths"]
    assert "/knowledge/query" in paths and "/agent/query" in paths and "/agent/runs/{run_id}/trace" in paths
