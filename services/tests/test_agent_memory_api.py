"""Agent memory through the HTTP API: opt in with a conversation id, the proposal and the user's decision in the same conversation, the audit and the forgetting."""

import inspect
import json
import uuid

import pytest

from data.pipelines import rag
from data.process import model_client
from services import database
from services.accounts import Role, User
from services.auth import get_current_user
from services.main import app
from services.routers import agent as agent_router

CHUNK = {"source_document": "carrier-coverage", "section": "Carrier Coverage > Spain carriers (Zaragoza)", "chunk_index": 1, "text": "SEUR: best coverage in rural areas of Aragón.", "score": 0.9}
SEUR_RULE = {"kind": "carrier_rule", "summary": "SEUR no longer covers the rural area of Zaragoza; the local carrier has been used since last month.", "reason": "A support agent corrected the carrier rule.", "carrier": "SEUR", "country": "ES"}
MESSAGE = "Actually SEUR no longer covers that rural area of Zaragoza, we've had to use the local carrier since last month."


def user(role: Role, user_id: str) -> User:
    return User(id=user_id, email=f"{user_id}@example.com", hashed_password="x", role=role, created_at="2026-01-01T00:00:00Z")


@pytest.fixture
def scripted(monkeypatch, incident_engine):
    """The model is scripted (turns queued by the test); the graph, the memory store, the classifier and the API are the real ones."""
    monkeypatch.setattr(database, "engine", incident_engine)
    monkeypatch.setenv("LLM_MODEL", "chat-model")
    monkeypatch.setattr(rag, "retrieve", lambda question, **kw: [CHUNK])
    state = {"turns": [], "prompts": []}

    def post(path, payload):
        state["prompts"].append(payload["messages"][-1]["content"])
        return {"choices": [{"message": {"content": json.dumps(state["turns"].pop(0))}}]}

    monkeypatch.setattr(model_client, "post", post)
    return state


@pytest.fixture
def as_manager(incident_client):
    app.dependency_overrides[get_current_user] = lambda: user(Role.manager, "mgr-valentina")
    yield incident_client
    app.dependency_overrides.pop(get_current_user, None)


def ask(client, message, conversation_id):
    return client.post("/agent/query", json={"question": message, "conversation_id": conversation_id})


def new_conversation() -> str:
    return f"conv-{uuid.uuid4().hex[:12]}"


def test_a_conversation_without_an_id_uses_no_memory_at_all(incident_client, incident_engine, monkeypatch):
    monkeypatch.setattr(database, "engine", incident_engine)
    monkeypatch.setattr(rag, "retrieve", lambda *a, **k: [CHUNK])
    monkeypatch.setattr(rag, "generate_answer", lambda question, context: "plain")
    body = incident_client.post("/agent/query", json={"question": MESSAGE}).json()
    assert body["answer"] == "plain" and body["conversation_id"] is None and body["memory"] is None


@pytest.mark.parametrize("bad", ["short", "has spaces in it", "x" * 65, "semi;colon-id-here", ""])
def test_a_malformed_conversation_id_is_a_422(incident_client, bad):
    assert incident_client.post("/agent/query", json={"question": "hi", "conversation_id": bad}).status_code == 422


def test_the_proposal_arrives_inside_the_answer_and_the_next_message_answers_it(as_manager, scripted):
    conversation = new_conversation()
    scripted["turns"].append({"answer": "Understood, thanks.", "memory_proposal": SEUR_RULE})
    first = ask(as_manager, MESSAGE, conversation).json()
    assert "Want me to remember this for next time?" in first["answer"] and SEUR_RULE["summary"] in first["answer"]
    assert first["conversation_id"] == conversation and first["memory"]["pending_proposal"]["kind"] == "carrier_rule" and first["memory"]["notice"] is None
    assert as_manager.get("/agent/memory").json() == {"entries": []}  # proposed, not remembered
    second = ask(as_manager, "Yes, please", conversation).json()
    assert second["answer"].startswith("Saved to memory:") and second["memory"]["notice"] == second["answer"] and second["memory"]["pending_proposal"] is None
    (entry,) = as_manager.get("/agent/memory").json()["entries"]
    assert entry["content"] == SEUR_RULE["summary"] and entry["scope_key"] == "carrier:seur|country:ES"


def test_the_audit_endpoint_shows_who_decided_and_when_for_approved_and_rejected_proposals(as_manager, scripted):
    approved, rejected = new_conversation(), new_conversation()
    scripted["turns"].extend([{"answer": "ok", "memory_proposal": SEUR_RULE}, {"answer": "ok", "memory_proposal": {"kind": "client_preference", "summary": "The cosmetics client wants the returns breakdown first in its monthly report.", "reason": "A standing preference.", "client": "Cosmetics Co"}}])
    ask(as_manager, MESSAGE, approved)
    ask(as_manager, "yes", approved)
    ask(as_manager, "The cosmetics client always wants the returns breakdown first in its monthly report.", rejected)
    ask(as_manager, "No thanks", rejected)
    log = {p["conversation_id"]: p for p in as_manager.get("/agent/memory/audit").json()["proposals"]}
    assert log[approved]["status"] == "approved" and log[rejected]["status"] == "rejected"
    for row in log.values():
        assert row["decided_by"] == "mgr-valentina" and row["actor"] == "mgr-valentina" and row["proposed_at"] and row["resolved_at"] and row["decision_message"] and row["decision_confidence"] >= 0.8
    assert log[rejected]["entry_id"] is None and log[approved]["entry_id"]
    assert len(as_manager.get("/agent/memory").json()["entries"]) == 1  # only the approved one is in memory
    assert [p["conversation_id"] for p in as_manager.get(f"/agent/memory/audit?conversation_id={rejected}").json()["proposals"]] == [rejected]


def test_a_later_conversation_receives_what_was_approved(as_manager, scripted):
    scripted["turns"].extend([{"answer": "ok", "memory_proposal": SEUR_RULE}, {"answer": "Use the local carrier for rural Zaragoza."}])
    first = new_conversation()
    ask(as_manager, MESSAGE, first)
    ask(as_manager, "yes", first)
    later = ask(as_manager, "Does SEUR still cover the rural area of Zaragoza?", new_conversation()).json()
    assert SEUR_RULE["summary"] in scripted["prompts"][-1] and later["memory"]["used"]  # the note is in the prompt and the response says a memory was used
    assert later["answer"] == "Use the local carrier for rural Zaragoza."


def test_a_reply_that_is_not_a_clear_yes_saves_nothing(as_manager, scripted):
    conversation = new_conversation()
    scripted["turns"].extend([{"answer": "ok", "memory_proposal": SEUR_RULE}, {"answer": "ok"}])
    ask(as_manager, MESSAGE, conversation)
    reply = ask(as_manager, "maybe", conversation).json()
    assert as_manager.get("/agent/memory").json() == {"entries": []} and "wasn't sure" in reply["answer"]
    assert as_manager.get("/agent/memory/audit").json()["proposals"][0]["status"] == "discarded_unclear"


def test_answering_the_proposal_and_asking_something_else_in_one_message_works_over_http(as_manager, scripted):
    conversation = new_conversation()
    scripted["turns"].extend([{"answer": "ok", "memory_proposal": SEUR_RULE}, {"answer": "Express takes 24 to 48 hours."}])
    ask(as_manager, MESSAGE, conversation)
    reply = ask(as_manager, "Yes, remember it. And what is the SLA for express?", conversation).json()
    assert reply["answer"].startswith("Saved to memory:") and "Express takes 24 to 48 hours." in reply["answer"]
    assert len(as_manager.get("/agent/memory").json()["entries"]) == 1


def test_a_forbidden_proposal_never_reaches_the_user(as_manager, scripted):
    scripted["turns"].append({"answer": "Noted.", "memory_proposal": {**SEUR_RULE, "summary": "SEUR delivers to the customer at 1234 Sunset Boulevard apt 5B every Monday."}})
    body = ask(as_manager, "Actually SEUR changed the Monday round for one customer.", new_conversation()).json()
    assert "Sunset" not in json.dumps(body) and body["memory"]["pending_proposal"] is None
    (row,) = as_manager.get("/agent/memory/audit").json()["proposals"]
    assert row["status"] == "blocked_by_policy" and "Sunset" not in json.dumps(row)


def test_memory_can_be_forgotten_and_the_audit_record_stays(as_manager, scripted):
    conversation = new_conversation()
    scripted["turns"].append({"answer": "ok", "memory_proposal": SEUR_RULE})
    ask(as_manager, MESSAGE, conversation)
    ask(as_manager, "yes", conversation)
    (entry,) = as_manager.get("/agent/memory").json()["entries"]
    assert as_manager.delete(f"/agent/memory/{entry['id']}").json() == {"forgotten": entry["id"]}
    assert as_manager.get("/agent/memory").json() == {"entries": []}
    assert as_manager.delete(f"/agent/memory/{entry['id']}").status_code == 404 and as_manager.delete(f"/agent/memory/{uuid.uuid4()}").status_code == 404
    assert as_manager.get("/agent/memory/audit").json()["proposals"][0]["status"] == "approved"  # the record of who approved it is never erased
    assert as_manager.delete("/agent/memory/not-a-uuid").status_code == 422


def test_only_admins_and_managers_can_read_or_change_the_memory(incident_client, scripted):
    assert incident_client.get("/agent/memory").status_code == 403
    assert incident_client.get("/agent/memory/audit").status_code == 403
    assert incident_client.delete(f"/agent/memory/{uuid.uuid4()}").status_code == 403


def test_a_plain_user_can_still_converse_and_answer_proposals(incident_client, scripted):
    scripted["turns"].append({"answer": "ok", "memory_proposal": SEUR_RULE})
    conversation = new_conversation()
    assert ask(incident_client, MESSAGE, conversation).json()["memory"]["pending_proposal"]
    assert ask(incident_client, "yes", conversation).json()["answer"].startswith("Saved to memory:")


def test_the_router_has_no_memory_logic_it_only_calls_the_agent():
    source = inspect.getsource(agent_router)
    for forbidden in ("classify", "decisions", "policy", "AgentMemory", "Session(", "select(", "resolve(", "propose("):
        assert forbidden not in source, forbidden
    assert "agent.memory_entries()" in source and "agent.memory_audit(" in source and "agent.forget_memory(" in source
