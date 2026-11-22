"""The support agent as an MCP client, against the real chain: agent -> OAuth token -> MCP server -> incident / inventory API -> database.

This is what the migration promises: the agent no longer reads the Incidents Manager itself. These tests run the agent's real tools and graph through the
MCP server and check what the server saw (the audit log) and what reached the API.
"""

import json
import logging

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from data.pipelines import rag
from services.agent import graph as agent_graph
from services.agent import mcp_client, runner, tools

NEW_TICKET = {"title": "Lost parcel", "description": "Never arrived.", "category": "lost_parcel", "origin": "customer", "branch": "central"}


@pytest.fixture
def agent_env(stack, monkeypatch):
    """Points the agent at the real MCP server with the agent's own OAuth client (scopes: tickets:read inventory:read)."""
    monkeypatch.setenv("AGENT_MCP_URL", stack.mcp_url)
    monkeypatch.setenv("AGENT_OAUTH_ISSUER", stack.idp_url)
    monkeypatch.setenv("AGENT_OAUTH_CLIENT_ID", "agent")
    monkeypatch.setenv("AGENT_OAUTH_CLIENT_SECRET", "agent-secret")
    return stack


@pytest.fixture
def ticket(stack):
    return stack.call("create_ticket", NEW_TICKET, "ops-bot").structuredContent


@pytest.fixture
def trace_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def compiled():
    return agent_graph.compile_graph(agent_graph.build_graph(), MemorySaver())


def audit_lines(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "trackflow.mcp.audit"]


def nodes(trace):
    return [step["node"] for step in trace]


# ================================================================== the tools reach the incident manager and the inventory only through the MCP server
def test_the_ticket_tool_reads_the_real_ticket_through_the_mcp_server(agent_env, ticket, caplog):
    caplog.set_level(logging.INFO, logger="trackflow.mcp.audit")
    result = tools.lookup_ticket(ticket["id"])
    assert result.status == "ok" and result.data["id"] == ticket["id"] and result.data["status"] == "open" and result.data["title"] == "Lost parcel"
    (line,) = audit_lines(caplog)  # the MCP server logged it: the call came through the server, as the agent's client
    assert (line["tool"], line["client_id"], line["outcome"], line["scopes"]) == ("get_ticket", "agent", "ok", ["tickets:read", "inventory:read"])


def test_the_ticket_status_is_live_not_a_copy(agent_env, ticket):
    assert tools.lookup_ticket(ticket["id"]).data["status"] == "open"
    agent_env.call("update_ticket_status", {"ticket_id": ticket["id"], "status": "in_progress"}, "ops-bot")  # someone else changes it
    assert tools.lookup_ticket(ticket["id"]).data["status"] == "in_progress"


def test_a_ticket_that_does_not_exist_is_not_found_through_the_mcp_server(agent_env):
    result = tools.lookup_ticket(987654)
    assert result.status == "not_found" and "987654" in result.message


def test_the_stock_tool_reads_the_real_inventory_through_the_read_only_mcp_tool(agent_env, caplog):
    caplog.set_level(logging.INFO, logger="trackflow.mcp.audit")
    result = tools.lookup_stock("hoodie")
    assert result.status == "ok" and result.data[0]["current_stock"] == 42 and result.data[0]["warehouse"] == "ZGZ"
    (line,) = audit_lines(caplog)
    assert (line["tool"], line["client_id"], line["arguments"]) == ("query_inventory", "agent", {"operation": "search", "product_query": "hoodie"})


def test_the_agents_own_credential_cannot_write_even_if_its_code_tried(agent_env, ticket):
    with pytest.raises(mcp_client.McpError) as failure:
        mcp_client.call_tool("create_ticket", NEW_TICKET)
    assert failure.value.code == "INSUFFICIENT_SCOPE"
    with pytest.raises(mcp_client.McpError) as failure:
        mcp_client.call_tool("update_ticket_status", {"ticket_id": ticket["id"], "status": "discarded"})
    assert failure.value.code == "INSUFFICIENT_SCOPE"
    with pytest.raises(mcp_client.McpError) as failure:
        mcp_client.call_tool("query_inventory", {"operation": "delete"})
    assert failure.value.code == "INVENTORY_READ_ONLY"
    assert tools.lookup_ticket(ticket["id"]).data["status"] == "open"


def test_the_agent_asks_the_provider_only_for_the_scopes_it_needs(agent_env):
    import jwt as pyjwt

    token = mcp_client.TokenProvider(mcp_client.load_settings()).token()
    claims = pyjwt.decode(token, options={"verify_signature": False})
    assert claims["scope"] == "tickets:read inventory:read" and claims["aud"] == agent_env.mcp_url and claims["client_id"] == "agent"


# ================================================================== the graph and its routing, now with the MCP client behind the tool nodes
def test_a_ticket_question_runs_the_graph_through_the_mcp_server_and_not_the_rag(agent_env, ticket, compiled, trace_engine, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="trackflow.mcp.audit")
    seen = {}
    monkeypatch.setattr(rag, "retrieve", lambda *a, **k: pytest.fail("a live ticket question must not use the RAG"))
    monkeypatch.setattr(rag, "generate_answer", lambda question, context: seen.update(context=context) or f"Ticket {ticket['id']} is open.")
    result = runner.run_agent(f"What's the status of ticket {ticket['id']}?", graph=compiled, engine=trace_engine, run_id="mcp-1")
    trace = runner.run_trace("mcp-1", engine=trace_engine)
    assert nodes(trace) == ["receive_question", "plan_sources", "lookup_ticket", "generate"]
    assert result.sources == ("tool:ticket_lookup",) and result.outcome == "generated"
    assert seen["context"][0]["source_document"] == "incident-manager" and "status open" in seen["context"][0]["text"] and "Lost parcel" in seen["context"][0]["text"]  # the real record
    assert [(l["tool"], l["client_id"]) for l in audit_lines(caplog)] == [("get_ticket", "agent")]  # and the server saw exactly that call


def test_a_policy_question_still_goes_to_the_rag_and_never_touches_the_mcp_server(agent_env, compiled, trace_engine, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="trackflow.mcp.audit")
    monkeypatch.setattr(rag, "retrieve", lambda *a, **k: [{"source_document": "returns-policy", "section": "S", "chunk_index": 1, "text": "30 days", "score": 0.9}])
    monkeypatch.setattr(rag, "generate_answer", lambda question, context: "30 days.")
    result = runner.run_agent("What's the standard return window?", graph=compiled, engine=trace_engine, run_id="mcp-2")
    assert nodes(runner.run_trace("mcp-2", engine=trace_engine)) == ["receive_question", "plan_sources", "retrieve_context", "generate"] and result.sources == ("rag",)
    assert audit_lines(caplog) == []  # not one call reached the MCP server


def test_a_question_that_needs_both_uses_the_mcp_server_first_and_then_the_rag(agent_env, ticket, compiled, trace_engine, monkeypatch):
    seen = {}
    monkeypatch.setattr(rag, "retrieve", lambda *a, **k: [{"source_document": "returns-policy", "section": "S", "chunk_index": 2, "text": "The brand pays unless damaged in transit.", "score": 0.9}])
    monkeypatch.setattr(rag, "generate_answer", lambda question, context: seen.update(context=context) or "answer")
    result = runner.run_agent(f"Ticket {ticket['id']} is about a lost parcel, who pays the return shipping?", graph=compiled, engine=trace_engine, run_id="mcp-3")
    assert nodes(runner.run_trace("mcp-3", engine=trace_engine)) == ["receive_question", "plan_sources", "lookup_ticket", "retrieve_context", "generate"]
    assert result.sources == ("tool:ticket_lookup", "rag") and [b["source_document"] for b in seen["context"]] == ["incident-manager", "returns-policy"]


def test_a_stock_question_runs_through_the_inventory_mcp_tool(agent_env, compiled, trace_engine, monkeypatch):
    seen = {}
    monkeypatch.setattr(rag, "retrieve", lambda *a, **k: pytest.fail("a stock question must not use the RAG"))
    monkeypatch.setattr(rag, "generate_answer", lambda question, context: seen.update(context=context) or "answer")
    result = runner.run_agent("Do we have stock of Blue Hoodie?", graph=compiled, engine=trace_engine, run_id="mcp-4")
    assert nodes(runner.run_trace("mcp-4", engine=trace_engine)) == ["receive_question", "plan_sources", "lookup_stock", "generate"] and result.sources == ("tool:stock_lookup",)
    assert "42 units in stock" in seen["context"][0]["text"] and seen["context"][0]["source_document"] == "inventory-manager"


# ================================================================== recovery: when the MCP server or its auth fails, the run recovers and there is no second path
def unreachable_server(monkeypatch, stack):
    monkeypatch.setenv("AGENT_MCP_URL", "http://127.0.0.1:1/mcp")  # nothing listens there
    monkeypatch.setenv("AGENT_OAUTH_ISSUER", stack.idp_url)
    monkeypatch.setenv("AGENT_OAUTH_CLIENT_ID", "agent")
    monkeypatch.setenv("AGENT_OAUTH_CLIENT_SECRET", "agent-secret")


def test_when_the_mcp_server_is_down_the_agent_answers_honestly_and_does_not_fall_back_to_another_path(stack, ticket, compiled, trace_engine, monkeypatch):
    unreachable_server(monkeypatch, stack)
    monkeypatch.setattr(rag, "generate_answer", lambda *a, **k: pytest.fail("no model may be asked when the only source failed"))
    mark = len(stack.api_requests)
    result = runner.run_agent(f"What's the status of ticket {ticket['id']}?", graph=compiled, engine=trace_engine, run_id="down-1")
    trace = runner.run_trace("down-1", engine=trace_engine)
    assert nodes(trace) == ["receive_question", "plan_sources", "lookup_ticket", "answer_unavailable"]
    assert trace[2]["output"]["live_data"][0]["status"] == "unavailable" and "MCP_UNAVAILABLE" in trace[2]["output"]["live_data"][0]["message"]
    assert f"couldn't confirm ticket {ticket['id']}'s status right now" in result.answer and result.outcome == "tool_unavailable"
    assert "open" not in result.answer.split("right now")[0] and "in_progress" not in result.answer
    assert stack.api_requests[mark:] == []  # the agent did not reach the incident API by any other road: with the server down, nothing was read


def test_wrong_agent_credentials_are_an_honest_unavailable_not_a_crash(stack, ticket, monkeypatch):
    monkeypatch.setenv("AGENT_MCP_URL", stack.mcp_url)
    monkeypatch.setenv("AGENT_OAUTH_ISSUER", stack.idp_url)
    monkeypatch.setenv("AGENT_OAUTH_CLIENT_ID", "agent")
    monkeypatch.setenv("AGENT_OAUTH_CLIENT_SECRET", "not-the-secret")
    result = tools.lookup_ticket(ticket["id"])
    assert result.status == "unavailable" and "AUTH_FAILED" in result.message and "not-the-secret" not in result.message


def test_an_agent_with_no_mcp_configuration_is_unavailable_not_a_direct_read(monkeypatch):
    for name in ("AGENT_MCP_URL", "AGENT_OAUTH_ISSUER", "AGENT_OAUTH_CLIENT_ID", "AGENT_OAUTH_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert tools.lookup_ticket(1).status == "unavailable" and "NOT_CONFIGURED" in tools.lookup_ticket(1).message
    assert tools.lookup_stock("hoodie").status == "unavailable"


def test_a_slow_mcp_server_is_a_timeout_result(agent_env, monkeypatch):
    import asyncio

    async def slow(*args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(mcp_client, "acall_tool", slow)
    started = __import__("time").perf_counter()
    result = tools.lookup_ticket(1)
    assert result.status == "timeout" and __import__("time").perf_counter() - started < tools.TOOL_TIMEOUT_SECONDS + 1
