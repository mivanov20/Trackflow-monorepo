"""POST /knowledge/query: the answer comes from the pipeline in data/pipelines, the client only ever sees the answer."""

import ast
import inspect

import pytest
from fastapi.testclient import TestClient

from data.pipelines import rag
from data.process import model_client
from services.main import app
from services.routers import knowledge

CHUNK = {"source_document": "sla-delivery", "section": "Delivery SLA > High-demand dates (no SLA guarantee)", "chunk_index": 2, "text": "TrackFlow does not guarantee delivery SLA during declared high-demand dates.", "score": 0.91}


def test_the_endpoint_needs_a_signed_in_user():
    with TestClient(app) as client:
        assert client.post("/knowledge/query", json={"question": "What is the SLA?"}).status_code == 401


def test_the_response_is_only_the_answer_the_model_generated(incident_client, monkeypatch):
    monkeypatch.setattr(knowledge.rag, "query", lambda question: f"generated: {question}")
    response = incident_client.post("/knowledge/query", json={"question": "  What is the standard return window? "})
    assert response.status_code == 200
    assert response.json() == {"answer": "generated: What is the standard return window?"}  # nothing else: no chunks, no scores


def test_the_whole_pipeline_behind_the_endpoint_never_leaks_chunks_or_scores(incident_client, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "chat-model")
    monkeypatch.setattr(rag, "retrieve", lambda question, **kw: [CHUNK])
    monkeypatch.setattr(model_client, "post", lambda path, payload: {"choices": [{"message": {"content": "No SLA is guaranteed on Black Friday.\nSource: sla-delivery"}}]})
    response = incident_client.post("/knowledge/query", json={"question": "Can I promise delivery times on Black Friday?"})
    body = response.json()
    assert set(body) == {"answer"} and body["answer"].startswith("No SLA is guaranteed")
    raw = response.text
    assert CHUNK["text"] not in raw and "0.91" not in raw and "score" not in raw and "chunk_index" not in raw


@pytest.mark.parametrize("payload", [{}, {"question": ""}, {"question": "   "}, {"question": "x" * 1001}, {"question": 123}])
def test_a_bad_question_is_a_422_and_never_reaches_the_pipeline(incident_client, monkeypatch, payload):
    monkeypatch.setattr(knowledge.rag, "query", lambda question: pytest.fail("must not be called"))
    assert incident_client.post("/knowledge/query", json=payload).status_code == 422


def test_when_a_backing_service_fails_the_client_gets_a_clear_503_without_internals(incident_client, monkeypatch):
    def broken(question):
        raise model_client.ModelServiceError("The model service answered 401 for /embeddings. key=super-secret http://models.internal")

    monkeypatch.setattr(knowledge.rag, "query", broken)
    response = incident_client.post("/knowledge/query", json={"question": "anything"})
    assert response.status_code == 503
    assert response.json() == {"detail": knowledge.UNAVAILABLE}
    assert "super-secret" not in response.text and "models.internal" not in response.text


def test_the_router_reuses_the_pipeline_and_does_not_duplicate_retrieval_or_generation():
    source = inspect.getsource(knowledge)
    tree = ast.parse(source)
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    assert "rag" in imported  # from data.pipelines import rag
    for forbidden in ("qdrant", "query_points", "embed(", "chat/completions", "httpx", "min_score"):
        assert forbidden not in source, forbidden
    assert "rag.query(" in source


def test_the_path_is_the_one_of_the_context(incident_client):
    paths = app.openapi()["paths"]
    assert "/knowledge/query" in paths and "post" in paths["/knowledge/query"]
