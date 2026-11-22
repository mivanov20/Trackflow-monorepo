"""The live tools of the support agent (ticket lookup, stock lookup) and the planner that decides when to use them.

The tools reach the company systems ONLY through the TrackFlow MCP server (see services/agent/mcp_client.py). These unit tests replace the MCP client with a
fake that answers with the same structured shapes the real server returns; the real chain (agent -> OAuth -> MCP server -> incident API -> database) is
tested in tests/mcps/test_agent_mcp_integration.py.
"""

import ast
import inspect
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "test-only-secret-with-at-least-32-characters")

from services.agent import mcp_client, routing, tools
from services.schemas import IncidentRead, SKURead

TICKET = {"id": 482, "title": "Parcel damaged in transit", "description": "The customer reports a crushed box.", "category": "delivery_failure", "status": "in_progress", "origin": "customer", "branch": "central", "created_at": "2026-11-20T09:00:00Z", "updated_at": "2026-11-21T10:30:00Z"}
HOODIE = {"id": 1, "name": "Blue Hoodie", "sku": "HOOD-BLU-M", "client_name": "Fashion Co", "category": "fashion", "warehouse": "ZGZ", "current_stock": 42}
MOUSE = {"id": 2, "name": "Wireless Mouse", "sku": "MOUSE-01", "client_name": "Tech Brand", "category": "electronics", "warehouse": "LA", "current_stock": 0}


class FakeMcp:
    """What the MCP server would answer, and a record of every tool the agent called."""

    def __init__(self, monkeypatch):
        self.tickets = {482: dict(TICKET)}
        self.products = [HOODIE, MOUSE]
        self.calls = []
        monkeypatch.setattr(mcp_client, "call_tool", self.call_tool)

    def call_tool(self, name, arguments, **kwargs):
        self.calls.append((name, arguments))
        if name == "get_ticket":
            ticket = self.tickets.get(arguments["ticket_id"])
            if ticket is None:
                raise mcp_client.McpError("NOT_FOUND", f"Ticket {arguments['ticket_id']} was not found.")
            return ticket
        if name == "query_inventory":
            assert arguments["operation"] == "search"
            needle = arguments["product_query"].lower()
            found = [p for p in self.products if needle in f"{p['name']} {p['sku']} {p['client_name']}".lower()]
            return {"operation": "search", "count": len(found), "products": found}
        raise AssertionError(f"the agent must not call {name}")


@pytest.fixture
def mcp(monkeypatch):
    return FakeMcp(monkeypatch)


# ================================================================== the ticket tool: typed contract, data from the MCP server
def test_the_ticket_tool_returns_the_fields_the_incident_api_exposes(mcp):
    result = tools.lookup_ticket(482)
    assert result.status == "ok" and result.tool == "ticket_lookup" and result.input == {"ticket_id": 482}
    ticket = IncidentRead.model_validate(result.data)  # the output IS the incident API's own contract
    assert (ticket.id, ticket.status, ticket.category, ticket.origin, ticket.branch) == (482, "in_progress", "delivery_failure", "customer", "central")
    assert result.data["created_at"].endswith("Z") and set(result.data) == set(IncidentRead.model_fields)
    assert mcp.calls == [("get_ticket", {"ticket_id": 482})]  # one MCP tool, with the ticket number


def test_the_ticket_tool_shows_the_current_value_not_a_copy(mcp):
    assert tools.lookup_ticket(482).data["status"] == "in_progress"
    mcp.tickets[482]["status"] = "resolved"  # someone changes the status in the incident manager
    assert tools.lookup_ticket(482).data["status"] == "resolved"  # the next call already sees it: nothing is cached or indexed


def test_a_ticket_that_does_not_exist_is_not_found_not_an_error(mcp):
    result = tools.lookup_ticket(99999)
    assert result.status == "not_found" and result.data is None and "99999" in result.message


@pytest.mark.parametrize("bad", [0, -3])
def test_the_ticket_input_contract_rejects_an_invalid_id(bad):
    with pytest.raises(ValueError):
        tools.TicketLookupInput(ticket_id=bad)
    with pytest.raises(ValueError):
        tools.lookup_ticket(bad)


def test_the_trace_summary_of_a_ticket_keeps_no_free_text(mcp):
    summary = tools.lookup_ticket(482).summary
    assert summary == {"id": 482, "status": "in_progress", "category": "delivery_failure", "origin": "customer", "branch": "central", "updated_at": "2026-11-21T10:30:00Z"}
    assert "crushed" not in str(summary) and "damaged" not in str(summary)


# ================================================================== the stock tool
def test_the_stock_tool_returns_the_current_stock_of_the_inventory_manager(mcp):
    result = tools.lookup_stock("hoodie")
    assert result.status == "ok" and result.tool == "stock_lookup"
    (product,) = [SKURead.model_validate(r) for r in result.data]
    assert (product.name, product.sku, product.warehouse, product.current_stock) == ("Blue Hoodie", "HOOD-BLU-M", "ZGZ", 42)
    assert result.summary == {"matches": [{"sku": "HOOD-BLU-M", "warehouse": "ZGZ", "current_stock": 42}]}
    assert mcp.calls == [("query_inventory", {"operation": "search", "product_query": "hoodie"})]  # the read-only operation


def test_the_stock_tool_says_so_when_nothing_matches_and_never_returns_more_than_five(mcp):
    missing = tools.lookup_stock("Purple Unicorn")
    assert missing.status == "not_found" and "Purple Unicorn" in missing.message
    mcp.products = [{**HOODIE, "id": i, "sku": f"SKU-{i}"} for i in range(9)]
    assert len(tools.lookup_stock("hoodie").data) == tools.MAX_PRODUCTS


def test_the_stock_input_contract_rejects_a_blank_query():
    with pytest.raises(ValueError):
        tools.lookup_stock(" ")


# ================================================================== read-only, and the MCP server is the only way in
def test_the_agent_only_ever_calls_the_two_read_tools_of_the_mcp_server(mcp):
    for call in (lambda: tools.lookup_ticket(482), lambda: tools.lookup_ticket(99999), lambda: tools.lookup_stock("hoodie"), lambda: tools.lookup_stock("zzz")):
        call()
    assert {name for name, _ in mcp.calls} == {"get_ticket", "query_inventory"}
    assert all(arguments.get("operation", "search") == "search" for name, arguments in mcp.calls if name == "query_inventory")


def test_no_write_tool_and_no_direct_access_exist_in_the_agent_package():
    agent_dir = Path(tools.__file__).parent
    for path in (agent_dir / "tools.py", agent_dir / "graph.py", agent_dir / "routing.py", agent_dir / "runner.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):  # docstrings may talk about the write tools; the code must not mention them
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant):
                node.body = node.body[1:] or [ast.Pass()]
        code = ast.unparse(tree)
        assert "create_ticket" not in code and "update_ticket_status" not in code, path.name  # the agent never writes
        imports = {alias.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom) for alias in node.names}
        modules = {node.module or "" for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)}
        assert not {"Incident", "SKU", "sku_read", "StockEntry", "StockExit"} & imports, path.name  # no models of the managers
        assert not any(m.endswith(("routers.inventory", "routers.incidents")) for m in modules), path.name  # no service layer of the managers
        assert "/api/incidents" not in code and "/inventory/" not in code, path.name  # no direct HTTP to them either


def test_the_agent_package_has_one_path_to_the_incident_manager_the_mcp_client():
    agent_dir = Path(tools.__file__).parent
    users = [p.name for p in agent_dir.glob("*.py") if "mcp_client" in p.read_text(encoding="utf-8") and p.name != "mcp_client.py"]
    assert users == ["tools.py"]  # only the tools module talks to the MCP client
    assert "database" not in {a.name for n in ast.walk(ast.parse((agent_dir / "tools.py").read_text(encoding="utf-8"))) if isinstance(n, ast.ImportFrom) for a in n.names}


# ================================================================== an explicit numeric timeout, and failures that are results
def test_the_timeout_is_an_explicit_number_of_seconds():
    assert isinstance(tools.TOOL_TIMEOUT_SECONDS, float) and 1.0 <= tools.TOOL_TIMEOUT_SECONDS <= 5.0
    assert inspect.signature(tools.lookup_ticket).parameters["timeout"].default == tools.TOOL_TIMEOUT_SECONDS
    assert inspect.signature(tools.lookup_stock).parameters["timeout"].default == tools.TOOL_TIMEOUT_SECONDS
    assert inspect.signature(mcp_client.call_tool).parameters["timeout"].default == 4.0  # the MCP call has its own explicit number too


def test_the_mcp_call_itself_receives_a_timeout_just_under_the_outer_one(mcp, monkeypatch):
    seen = {}
    monkeypatch.setattr(mcp_client, "call_tool", lambda name, arguments, **kw: seen.update(kw) or dict(TICKET))
    tools.lookup_ticket(482)
    assert seen["timeout"] == pytest.approx(tools.TOOL_TIMEOUT_SECONDS - tools.MCP_MARGIN_SECONDS)


def test_a_service_that_does_not_answer_in_time_is_a_timeout_result_and_the_call_does_not_hang(monkeypatch):
    monkeypatch.setattr(tools, "_read_ticket", lambda ticket_id: time.sleep(2) or {})
    started = time.perf_counter()
    result = tools.lookup_ticket(482, timeout=0.2)
    assert result.status == "timeout" and "did not answer" in result.message and result.data is None
    assert time.perf_counter() - started < 1.0  # gave up after 0.2 s, did not wait the 2 s


def test_a_slow_inventory_is_a_timeout_result_too(monkeypatch):
    monkeypatch.setattr(tools, "_read_stock", lambda query: time.sleep(2) or [])
    assert tools.lookup_stock("hoodie", timeout=0.2).status == "timeout"


def test_an_mcp_timeout_is_a_timeout_result(monkeypatch):
    monkeypatch.setattr(mcp_client, "call_tool", lambda *a, **k: (_ for _ in ()).throw(mcp_client.McpError("MCP_TIMEOUT", "no answer")))
    assert tools.lookup_ticket(482).status == "timeout" and tools.lookup_stock("hoodie").status == "timeout"


@pytest.mark.parametrize("code", ["UPSTREAM_UNAVAILABLE", "MCP_UNAVAILABLE", "AUTH_FAILED", "INSUFFICIENT_SCOPE", "AUTH_PROVIDER_UNAVAILABLE", "BAD_RESPONSE", "NOT_CONFIGURED"])
def test_every_failure_of_the_mcp_path_is_an_unavailable_result_not_an_exception(monkeypatch, code):
    def broken(*args, **kwargs):
        raise mcp_client.McpError(code, "details that may name an address: http://models.internal key=secret")

    monkeypatch.setattr(mcp_client, "call_tool", broken)
    ticket, stock = tools.lookup_ticket(482), tools.lookup_stock("hoodie")
    assert ticket.status == "unavailable" and stock.status == "unavailable"
    assert code in ticket.message and "secret" not in ticket.message and "models.internal" not in ticket.message  # the code, not the details


def test_a_failing_service_is_an_unavailable_result_not_an_exception(monkeypatch):
    def broken(ticket_id):
        raise RuntimeError("connection refused: postgresql://user:secret@db.internal/prod")

    monkeypatch.setattr(tools, "_read_ticket", broken)
    result = tools.lookup_ticket(482)
    assert result.status == "unavailable" and "RuntimeError" in result.message
    assert "secret" not in result.message and "db.internal" not in result.message  # the exception text (with its address) is not passed on
    monkeypatch.setattr(tools, "_read_stock", lambda q: (_ for _ in ()).throw(ConnectionError("down")))
    assert tools.lookup_stock("hoodie").status == "unavailable"


def test_live_results_become_prompt_blocks_with_their_own_source_and_failures_say_unknown(mcp, monkeypatch):
    ok = tools.lookup_ticket(482).model_dump(mode="json")
    monkeypatch.setattr(tools, "_read_ticket", lambda i: (_ for _ in ()).throw(TimeoutError()))
    failed = tools.lookup_ticket(9).model_dump(mode="json")
    good, bad = tools.live_chunks([ok, failed])
    assert good["source_document"] == "incident-manager" and "status in_progress" in good["text"] and "LIVE" in good["text"]
    assert bad["source_document"] == "incident-manager" and "UNKNOWN" in bad["text"] and "do not guess" in bad["text"] and "in_progress" not in bad["text"]


def test_the_fallback_message_is_honest_and_never_contains_a_status_or_a_number():
    unavailable = tools.ToolResult(tool="ticket_lookup", status="timeout", input={"ticket_id": 482}, message="x").model_dump(mode="json")
    not_found = tools.ToolResult(tool="ticket_lookup", status="not_found", input={"ticket_id": 99999}, message="x").model_dump(mode="json")
    stock = tools.ToolResult(tool="stock_lookup", status="unavailable", input={"product_query": "hoodie"}, message="x").model_dump(mode="json")
    assert "couldn't confirm ticket 482's status right now" in tools.fallback_message([unavailable])
    assert "couldn't find a ticket 99999" in tools.fallback_message([not_found])
    assert "couldn't check the stock of 'hoodie' right now" in tools.fallback_message([stock])
    for word in ("in_progress", "resolved", "open", "units in stock"):
        assert word not in tools.fallback_message([unavailable, stock])


# ================================================================== each tool does one thing
def test_the_two_tools_are_separate_functions_with_separate_contracts_and_nodes():
    from services.agent import graph

    assert tools.lookup_ticket is not tools.lookup_stock and tools.TicketLookupInput is not tools.StockLookupInput
    assert set(tools.TicketLookupInput.model_fields) == {"ticket_id"} and set(tools.StockLookupInput.model_fields) == {"product_query"}
    ticket_source, stock_source = inspect.getsource(tools._read_ticket), inspect.getsource(tools._read_stock)
    assert "query_inventory" not in ticket_source and "get_ticket" not in stock_source  # a tool of tickets never reads stock and the other way round
    assert graph.LOOKUP_TICKET != graph.LOOKUP_STOCK and "tools.lookup_stock" not in inspect.getsource(graph.lookup_ticket) and "tools.lookup_ticket" not in inspect.getsource(graph.lookup_stock)


# ================================================================== the planner: which sources does a question need?
@pytest.mark.parametrize(
    "question, steps",
    [
        ("What's the status of ticket 482?", ["ticket"]),
        ("Is incident #12 resolved?", ["ticket"]),
        ("can you check #482", ["ticket"]),
        ("Do we have stock of Blue Hoodie?", ["stock"]),
        ("do we have any wireless mouse in stock?", ["stock"]),
        ("How many units of SKU-123 are left?", ["stock"]),
        ("Is Acme Boots in stock?", ["stock"]),
        ("What's the standard return window?", ["rag"]),
        ("Which carrier best covers rural Aragón?", ["rag"]),
        ("Can we guarantee delivery times on Black Friday?", ["rag"]),
        ("Can I offer a storage discount?", ["rag"]),
        ("Do we have a returns policy?", ["rag"]),
        ("How long does express shipping take?", ["rag"]),
        ("What's the weather in Zaragoza?", ["rag"]),  # no live data asked: the knowledge base decides it has nothing
        ("Ticket 482 is about a damaged parcel, who pays the return shipping?", ["ticket", "rag"]),
        ("stock of Acme Boots and what's the storage price?", ["stock", "rag"]),
        ("What is the status of the ticket?", []),  # no number: ask for it
        ("Any update on my ticket?", []),
    ],
)
def test_the_planner_picks_the_sources_from_the_content_of_the_question(question, steps):
    assert routing.plan_sources(question)["steps"] == steps


def test_the_plan_carries_the_ids_and_the_reason_and_is_json_friendly():
    import json

    plan = routing.plan_sources("Ticket 482 is about a damaged parcel, who pays the return shipping?")
    assert plan["ticket_id"] == 482 and plan["steps"] == ["ticket", "rag"] and "ticket 482" in plan["reason"] and "knowledge base" in plan["reason"]
    assert json.loads(json.dumps(plan)) == plan
    assert routing.plan_sources("Do we have stock of Blue Hoodie?")["stock_query"] == "Blue Hoodie"
    assert routing.plan_sources("What is the status of the ticket?")["clarify"] is True


def test_the_tools_run_before_the_rag_when_a_question_needs_both():
    assert routing.plan_sources("stock of Acme Boots and what's the storage price?")["steps"] == ["stock", "rag"]
    assert routing.STEP_ORDER == ("ticket", "stock", "rag")


def test_the_planner_never_needs_the_user_to_name_a_source():
    for question in ("What's the status of ticket 482?", "What's the standard return window?"):
        assert not any(word in question.lower() for word in ("tool", "rag", "knowledge base", "database", "mcp"))
        assert routing.plan_sources(question)["steps"]  # it chose by itself
