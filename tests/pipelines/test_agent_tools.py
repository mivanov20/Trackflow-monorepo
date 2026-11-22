"""The live tools of the support agent (ticket lookup, stock lookup) and the planner that decides when to use them.

The tools are tested against REAL rows of the incident and inventory tables of an in-memory database, read through the same models, the same
`IncidentRead` schema and the same `sku_read` the endpoints use: there is no simulated dataset anywhere.
"""

import ast
import inspect
import os
import time
from pathlib import Path
from typing import get_args

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "test-only-secret-with-at-least-32-characters")

from sqlalchemy import event, func
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from services import database
from services.agent import routing, tools
from services.models import SKU, Incident, StockEntry, StockExit
from services.schemas import Category, IncidentRead, SKURead


@pytest.fixture
def engine(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)  # the tools read the database of the services, at call time
    return engine


@pytest.fixture
def data(engine):
    with Session(engine) as session:
        session.add(Incident(id=482, title="Parcel damaged in transit", description="The customer reports a crushed box.", category="delivery_failure", status="in_progress", origin="customer", branch="central"))
        session.add(Incident(id=7, title="Stock count mismatch", description="Two units missing.", category="inventory_discrepancy", status="open", origin="internal", branch="la_warehouse"))
        hoodie = SKU(name="Blue Hoodie", sku="HOOD-BLU-M", client_name="Fashion Co", category=get_args(Category)[0], warehouse="ZGZ")
        mouse = SKU(name="Wireless Mouse", sku="MOUSE-01", client_name="Tech Brand", category=get_args(Category)[0], warehouse="LA")
        session.add(hoodie)
        session.add(mouse)
        session.commit()
        session.add(StockEntry(sku_id=hoodie.id, quantity=50, reference="PO-1", warehouse="ZGZ", user_uid="u1"))
        session.add(StockExit(sku_id=hoodie.id, quantity=8, exit_type="dispatch", tracking_number="TRK-1", warehouse="ZGZ", user_uid="u1"))
        session.commit()
    return engine


def counts(engine) -> dict:
    with Session(engine) as session:
        return {table.__tablename__: session.exec(select(func.count()).select_from(table)).one() for table in (Incident, SKU, StockEntry, StockExit)}


# ================================================================== the ticket tool: typed contract, real data
def test_the_ticket_tool_returns_the_fields_the_incident_api_exposes(data):
    result = tools.lookup_ticket(482)
    assert result.status == "ok" and result.tool == "ticket_lookup" and result.input == {"ticket_id": 482}
    ticket = IncidentRead.model_validate(result.data)  # the output IS the incident API's own contract
    assert (ticket.id, ticket.status, ticket.category, ticket.origin, ticket.branch) == (482, "in_progress", "delivery_failure", "customer", "central")
    assert ticket.created_at and ticket.updated_at and result.data["created_at"].endswith("Z")
    assert set(result.data) == set(IncidentRead.model_fields)


def test_the_ticket_tool_shows_the_current_value_not_a_copy(data):
    assert tools.lookup_ticket(482).data["status"] == "in_progress"
    with Session(data) as session:  # someone changes the status in the incident manager
        incident = session.get(Incident, 482)
        incident.status = "resolved"
        session.add(incident)
        session.commit()
    assert tools.lookup_ticket(482).data["status"] == "resolved"  # the next call already sees it


def test_a_ticket_that_does_not_exist_is_not_found_not_an_error(data):
    result = tools.lookup_ticket(99999)
    assert result.status == "not_found" and result.data is None and "99999" in result.message


@pytest.mark.parametrize("bad", [0, -3])
def test_the_ticket_input_contract_rejects_an_invalid_id(bad):
    with pytest.raises(ValueError):
        tools.TicketLookupInput(ticket_id=bad)
    with pytest.raises(ValueError):
        tools.lookup_ticket(bad)


def test_the_trace_summary_of_a_ticket_keeps_no_free_text(data):
    summary = tools.lookup_ticket(482).summary
    assert summary == {"id": 482, "status": "in_progress", "category": "delivery_failure", "origin": "customer", "branch": "central", "updated_at": summary["updated_at"]}
    assert "crushed" not in str(summary) and "damaged" not in str(summary)


# ================================================================== the stock tool
def test_the_stock_tool_returns_the_current_stock_calculated_like_the_inventory_endpoint(data):
    result = tools.lookup_stock("hoodie")
    assert result.status == "ok" and result.tool == "stock_lookup"
    (product,) = [SKURead.model_validate(r) for r in result.data]
    assert (product.name, product.sku, product.warehouse, product.current_stock) == ("Blue Hoodie", "HOOD-BLU-M", "ZGZ", 42)  # 50 in, 8 out
    assert tools.lookup_stock("MOUSE-01").data[0]["current_stock"] == 0
    assert result.summary == {"matches": [{"sku": "HOOD-BLU-M", "warehouse": "ZGZ", "current_stock": 42}]}


def test_the_stock_tool_matches_by_name_sku_or_client_and_says_so_when_nothing_matches(data):
    assert [r["sku"] for r in tools.lookup_stock("Fashion Co").data] == ["HOOD-BLU-M"]
    missing = tools.lookup_stock("Purple Unicorn")
    assert missing.status == "not_found" and "Purple Unicorn" in missing.message
    assert tools.lookup_stock("100%").status == "not_found"  # a % in the query is text, not a wildcard


def test_the_stock_input_contract_rejects_a_blank_query():
    with pytest.raises(ValueError):
        tools.lookup_stock(" ")


# ================================================================== read-only
def test_the_tools_never_change_the_data(data):
    before = counts(data)
    for call in (lambda: tools.lookup_ticket(482), lambda: tools.lookup_ticket(99999), lambda: tools.lookup_stock("hoodie"), lambda: tools.lookup_stock("zzz")):
        call()
    assert counts(data) == before
    with Session(data) as session:
        assert session.get(Incident, 482).status == "in_progress"


def test_no_write_operation_exists_in_the_tools_module():
    source = Path(tools.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not called & {"add", "add_all", "commit", "delete", "merge", "flush", "execute", "bulk_save_objects", "post", "put", "patch", "refresh"}
    assert not any(isinstance(node, ast.Name) and node.id in {"insert", "update", "delete"} for node in ast.walk(tree))
    assert "session.get(" in source and "session.exec(" in source  # only reads


def test_during_a_lookup_the_database_session_never_flushes_a_change(data):
    seen = []
    event.listen(data, "before_cursor_execute", lambda conn, cur, statement, *a: seen.append(statement.strip().split()[0].upper()))
    tools.lookup_ticket(482)
    tools.lookup_stock("hoodie")
    assert set(seen) <= {"SELECT", "PRAGMA", "BEGIN"} and "INSERT" not in seen and "UPDATE" not in seen and "DELETE" not in seen


# ================================================================== an explicit numeric timeout, and failures that are results
def test_the_timeout_is_an_explicit_number_of_seconds():
    assert isinstance(tools.TOOL_TIMEOUT_SECONDS, float) and 1.0 <= tools.TOOL_TIMEOUT_SECONDS <= 5.0
    assert inspect.signature(tools.lookup_ticket).parameters["timeout"].default == tools.TOOL_TIMEOUT_SECONDS
    assert inspect.signature(tools.lookup_stock).parameters["timeout"].default == tools.TOOL_TIMEOUT_SECONDS


def test_a_service_that_does_not_answer_in_time_is_a_timeout_result_and_the_call_does_not_hang(monkeypatch):
    monkeypatch.setattr(tools, "_read_ticket", lambda ticket_id: time.sleep(2) or {})
    started = time.perf_counter()
    result = tools.lookup_ticket(482, timeout=0.2)
    assert result.status == "timeout" and "did not answer" in result.message and result.data is None
    assert time.perf_counter() - started < 1.0  # gave up after 0.2 s, did not wait the 2 s


def test_a_slow_inventory_is_a_timeout_result_too(monkeypatch):
    monkeypatch.setattr(tools, "_read_stock", lambda query: time.sleep(2) or [])
    assert tools.lookup_stock("hoodie", timeout=0.2).status == "timeout"


def test_a_failing_service_is_an_unavailable_result_not_an_exception(monkeypatch):
    def broken(ticket_id):
        raise RuntimeError("connection refused: postgresql://user:secret@db.internal/prod")

    monkeypatch.setattr(tools, "_read_ticket", broken)
    result = tools.lookup_ticket(482)
    assert result.status == "unavailable" and "RuntimeError" in result.message
    assert "secret" not in result.message and "db.internal" not in result.message  # the exception text (with its address) is not passed on
    monkeypatch.setattr(tools, "_read_stock", lambda q: (_ for _ in ()).throw(ConnectionError("down")))
    assert tools.lookup_stock("hoodie").status == "unavailable"


def test_live_results_become_prompt_blocks_with_their_own_source_and_failures_say_unknown(data, monkeypatch):
    ok = tools.lookup_ticket(482).model_dump(mode="json")
    monkeypatch.setattr(tools, "_read_ticket", lambda i: (_ for _ in ()).throw(TimeoutError()))
    failed = tools.lookup_ticket(9).model_dump(mode="json")
    good, bad = tools.live_chunks([ok, failed])
    assert good["source_document"] == "incident-manager" and "status in_progress" in good["text"] and "LIVE" in good["text"]
    assert bad["source_document"] == "incident-manager" and "UNKNOWN" in bad["text"] and "do not guess" in bad["text"] and "in_progress" not in bad["text"]


def test_the_fallback_message_is_honest_and_never_contains_a_status_or_a_number(data):
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
    assert "SKU" not in ticket_source and "Incident" not in stock_source  # a tool of tickets never reads stock and the other way round
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
        assert not any(word in question.lower() for word in ("tool", "rag", "knowledge base", "database"))
        assert routing.plan_sources(question)["steps"]  # it chose by itself
