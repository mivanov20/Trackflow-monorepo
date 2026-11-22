"""The TrackFlow MCP server against a real stack: OAuth (MCP Auth) in front, real incident and inventory API behind. See conftest.py."""

import json
import logging
import re
import time

import httpx
import pytest
from sqlmodel import Session, func, select

from mcps.trackflow_tools import audit, config, errors, server
from mcps.trackflow_tools.config import ConfigError, load_settings
from services.models import Incident, StockEntry, StockExit

NEW_TICKET = {"title": "Damaged parcel", "description": "The box arrived crushed.", "category": "delivery_failure", "origin": "customer", "branch": "central"}
RPC_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
RPC_CALL = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_ticket", "arguments": {"ticket_id": 1}}}


def code_of(result) -> str | None:
    match = re.search(r"\[([A-Z_]+)\]", result.content[0].text) if result.isError else None
    return match.group(1) if match else None


def count(stack, model) -> int:
    with Session(stack.api_engine) as session:
        return session.exec(select(func.count()).select_from(model)).one()


@pytest.fixture(scope="module")
def ticket(stack):
    """One ticket created through the MCP server (so it exists in the real incident manager)."""
    result = stack.call("create_ticket", NEW_TICKET, "ops-bot")
    assert not result.isError, result.content
    return result.structuredContent


# ================================================================== the server starts and exposes its tools through standard discovery
def test_the_server_answers_health_without_a_token_and_returns_no_company_data(stack):
    response = httpx.get(stack.mcp_url.replace("/mcp", "/health"))
    assert response.status_code == 200 and response.json() == {"status": "ok", "server": "trackflow-tools"}


def test_discovery_lists_the_four_tools_with_names_descriptions_and_schemas(stack):
    tools = {t.name: t for t in stack.list_tools().tools}
    assert set(tools) == {"get_ticket", "create_ticket", "update_ticket_status", "query_inventory"}
    for tool in tools.values():
        assert len(tool.description) > 120, tool.name  # enough to understand it with no human context
        assert tool.inputSchema["type"] == "object" and tool.inputSchema["properties"], tool.name
        assert tool.outputSchema and tool.outputSchema["properties"], tool.name  # the shape of what comes back
        assert tool.annotations is not None, tool.name


def test_every_tool_description_says_what_it_needs_and_how_it_fails(stack):
    for tool in stack.list_tools().tools:
        text = tool.description
        assert re.search(r"Needs scope (tickets|inventory):(read|write)", text), tool.name  # the permission it requires
        assert "Errors:" in text or "[INVENTORY_READ_ONLY]" in text, tool.name  # the error codes it can return


def test_the_schemas_list_the_allowed_values_and_mark_required_fields(stack):
    tools = {t.name: t for t in stack.list_tools().tools}
    create = tools["create_ticket"].inputSchema
    assert set(create["required"]) == {"title", "description", "category", "origin", "branch"}
    assert "delivery_failure" in create["properties"]["category"]["enum"] and set(create["properties"]["origin"]["enum"]) == {"customer", "branch", "internal"}
    assert set(tools["update_ticket_status"].inputSchema["properties"]["status"]["enum"]) == {"open", "in_progress", "resolved", "discarded"}
    assert tools["get_ticket"].inputSchema["properties"]["ticket_id"]["minimum"] == 1
    inventory = tools["query_inventory"].inputSchema
    assert inventory["required"] == ["operation"] and inventory["properties"]["operation"]["enum"] == ["search", "get", "list"]  # no write operation is even offered


def test_annotations_tell_a_client_which_tools_only_read(stack):
    tools = {t.name: t.annotations for t in stack.list_tools().tools}
    assert tools["get_ticket"].readOnlyHint is True and tools["query_inventory"].readOnlyHint is True
    assert tools["create_ticket"].readOnlyHint is False and tools["update_ticket_status"].readOnlyHint is False
    assert all(a.destructiveHint is False and a.openWorldHint is False for a in tools.values())


def test_the_server_describes_itself_on_connect(stack):
    instructions = stack.initialize().instructions
    for needle in ("get_ticket", "create_ticket", "update_ticket_status", "query_inventory", "READ ONLY", "tickets:write", "[INSUFFICIENT_SCOPE]", "[VALIDATION_ERROR]", "open -> in_progress -> resolved"):
        assert needle in instructions, needle


def test_the_protected_resource_metadata_is_public_and_names_the_provider_and_scopes(stack):
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        response = httpx.get(stack.settings.resource_origin + path)
        assert response.status_code == 200, path
        body = response.json()
        assert body["resource"] == stack.mcp_url and body["authorization_servers"] == [stack.idp_url]
        assert set(body["scopes_supported"]) == {"tickets:read", "tickets:write", "inventory:read"} and body["bearer_methods_supported"] == ["header"]
    metadata = httpx.get(stack.settings.resource_origin + "/.well-known/oauth-authorization-server").json()
    assert metadata["issuer"] == stack.idp_url and "S256" in metadata["code_challenge_methods_supported"]


# ================================================================== a client without a valid token cannot list or run anything
@pytest.mark.parametrize("payload", [RPC_LIST, RPC_CALL], ids=["list", "call"])
def test_without_a_token_nothing_can_be_listed_or_called(stack, payload):
    response = httpx.post(stack.mcp_url, json=payload, headers={"accept": "application/json, text/event-stream"})
    assert response.status_code == 401
    assert response.json()["error"] == "missing_auth_header"
    assert "tools" not in response.text and "ticket" not in response.text.lower().replace("authorization", "")  # nothing about the tools leaks


def test_a_401_tells_the_client_where_to_find_the_authorization_server(stack):
    response = httpx.post(stack.mcp_url, json=RPC_LIST)
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ") and f'resource_metadata="{stack.settings.resource_origin}/.well-known/oauth-protected-resource/mcp"' in challenge


@pytest.mark.parametrize(
    "header, error",
    [("Basic abc123", "invalid_auth_header_format"), ("Bearer", "invalid_auth_header_format"), ("Bearer not.a.jwt", "invalid_token"), ("Bearer " + "a" * 40, "invalid_token")],
)
def test_a_malformed_or_invalid_token_is_a_401_with_its_own_code(stack, header, error):
    response = httpx.post(stack.mcp_url, json=RPC_LIST, headers={"Authorization": header})
    assert response.status_code == 401 and response.json()["error"] == error


def test_a_token_signed_by_somebody_else_is_rejected(stack):
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    forged = pyjwt.encode({"iss": stack.idp_url, "sub": "evil", "client_id": "evil", "aud": stack.settings.audience, "scope": "tickets:read tickets:write inventory:read", "exp": int(time.time()) + 600}, rsa.generate_private_key(65537, 2048), algorithm="RS256", headers={"kid": "dev-key-1"})
    response = httpx.post(stack.mcp_url, json=RPC_LIST, headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401 and response.json()["error"] == "invalid_token"


def test_an_expired_token_is_rejected(stack, monkeypatch):
    from mcps.dev_auth_server import app as idp_module

    monkeypatch.setattr(idp_module, "TOKEN_LIFETIME_SECONDS", -3600)
    expired = stack.token("ops-bot")
    response = httpx.post(stack.mcp_url, json=RPC_LIST, headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401 and response.json()["error"] == "invalid_token"


def test_a_token_for_another_server_is_rejected_by_audience(stack):
    other = stack.token("ops-bot", resource="https://some-other-server.example/mcp")
    response = httpx.post(stack.mcp_url, json=RPC_LIST, headers={"Authorization": f"Bearer {other}"})
    assert response.status_code == 401 and response.json()["error"] == "invalid_audience"


def test_an_unauthenticated_browser_preflight_still_works_so_a_browser_client_can_send_its_token(stack):
    response = httpx.options(stack.mcp_url, headers={"Origin": "https://www.mcpplayground.tech", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"})
    assert response.status_code == 200 and response.headers["access-control-allow-origin"] == "https://www.mcpplayground.tech"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


# ================================================================== least privilege: each tool needs its own scope
def test_a_client_with_only_tickets_read_can_look_at_a_ticket_and_nothing_else(stack, ticket):
    assert not stack.call("get_ticket", {"ticket_id": ticket["id"]}, "reader").isError
    before = count(stack, Incident)
    denied = [stack.call("create_ticket", NEW_TICKET, "reader"), stack.call("update_ticket_status", {"ticket_id": ticket["id"], "status": "in_progress"}, "reader"), stack.call("query_inventory", {"operation": "list"}, "reader")]
    assert [code_of(r) for r in denied] == [errors.INSUFFICIENT_SCOPE] * 3
    assert count(stack, Incident) == before  # nothing was created
    assert "tickets:write" in denied[0].content[0].text and "inventory:read" in denied[2].content[0].text  # it says which scope is missing


def test_the_agents_credential_can_read_and_cannot_write(stack, ticket):
    assert not stack.call("get_ticket", {"ticket_id": ticket["id"]}, "agent").isError
    assert not stack.call("query_inventory", {"operation": "list"}, "agent").isError
    assert code_of(stack.call("create_ticket", NEW_TICKET, "agent")) == errors.INSUFFICIENT_SCOPE
    assert code_of(stack.call("update_ticket_status", {"ticket_id": ticket["id"], "status": "discarded"}, "agent")) == errors.INSUFFICIENT_SCOPE
    assert stack.call("get_ticket", {"ticket_id": ticket["id"]}, "agent").structuredContent["status"] == "open"  # it did not change


def test_a_token_asked_with_fewer_scopes_gets_only_those(stack, ticket):
    assert code_of(stack.call("create_ticket", NEW_TICKET, "ops-bot")) is None  # ops-bot may
    result = stack.call_with(stack.headers("ops-bot", scope="tickets:read"), "create_ticket", NEW_TICKET)
    assert code_of(result) == errors.INSUFFICIENT_SCOPE  # the client chose to ask for less, and it holds


def test_a_scope_cannot_be_obtained_that_the_client_was_not_registered_for(stack):
    response = httpx.post(f"{stack.idp_url}/token", data={"grant_type": "client_credentials", "client_id": "agent", "client_secret": "agent-secret", "scope": "tickets:write"})
    assert response.status_code == 400 and response.json()["error"] == "invalid_scope"


def test_a_valid_token_with_no_scope_at_all_is_refused_by_every_tool(stack, ticket):
    import jwt as pyjwt

    assert pyjwt.decode(stack.token("nobody"), options={"verify_signature": False})["scope"] == ""  # a genuine token of a client that was granted nothing
    before = count(stack, Incident)
    calls = [("get_ticket", {"ticket_id": ticket["id"]}), ("create_ticket", NEW_TICKET), ("update_ticket_status", {"ticket_id": ticket["id"], "status": "discarded"}), ("query_inventory", {"operation": "list"})]
    results = [stack.call(tool, arguments, "nobody") for tool, arguments in calls]
    assert [code_of(r) for r in results] == [errors.INSUFFICIENT_SCOPE] * 4
    assert all("no scopes" in r.content[0].text for r in results) and count(stack, Incident) == before


# ================================================================== tickets: create, status, check, against the REAL incident manager
def test_create_ticket_creates_a_real_ticket_in_the_incident_manager(stack, ticket):
    assert ticket["status"] == "open" and ticket["title"] == NEW_TICKET["title"] and ticket["id"] >= 1
    assert set(ticket) == {"id", "title", "description", "category", "status", "origin", "branch", "created_at", "updated_at"}  # the incident API's fields
    with Session(stack.api_engine) as session:
        stored = session.get(Incident, ticket["id"])
        assert stored is not None and (stored.title, stored.category, stored.origin, stored.branch, stored.status) == ("Damaged parcel", "delivery_failure", "customer", "central", "open")


def test_get_ticket_returns_the_live_status_of_that_ticket(stack, ticket):
    result = stack.call("get_ticket", {"ticket_id": ticket["id"]}, "reader")
    assert not result.isError and result.structuredContent["id"] == ticket["id"] and result.structuredContent["status"] == "open"
    assert json.loads(result.content[0].text)["status"] == "open"  # also as text, for clients that read content and not structured output


def test_update_ticket_status_goes_through_the_lifecycle_endpoint_and_changes_the_ticket(stack):
    created = stack.call("create_ticket", NEW_TICKET, "ops-bot").structuredContent
    mark = len(stack.api_requests)
    moved = stack.call("update_ticket_status", {"ticket_id": created["id"], "status": "in_progress"}, "ops-bot")
    assert not moved.isError and moved.structuredContent["status"] == "in_progress" and moved.structuredContent["updated_at"] >= created["updated_at"]
    assert stack.call("get_ticket", {"ticket_id": created["id"]}, "reader").structuredContent["status"] == "in_progress"  # really stored
    with Session(stack.api_engine) as session:
        assert session.get(Incident, created["id"]).status == "in_progress"
    patches = [call for call in stack.api_requests[mark:] if call[0] == "PATCH"]
    assert patches == [("PATCH", f"/api/incidents/{created['id']}/status")]  # the status endpoint and no other PATCH on the incident


def test_the_lifecycle_rules_of_the_incident_manager_are_enforced_not_bypassed(stack):
    created = stack.call("create_ticket", NEW_TICKET, "ops-bot").structuredContent
    jump = stack.call("update_ticket_status", {"ticket_id": created["id"], "status": "resolved"}, "ops-bot")  # open -> resolved is not allowed
    assert code_of(jump) == errors.VALIDATION_ERROR and "can only move to" in jump.content[0].text
    stack.call("update_ticket_status", {"ticket_id": created["id"], "status": "discarded"}, "ops-bot")
    final = stack.call("update_ticket_status", {"ticket_id": created["id"], "status": "in_progress"}, "ops-bot")  # discarded is final
    assert code_of(final) == errors.VALIDATION_ERROR and "can no longer change" in final.content[0].text
    assert stack.call("get_ticket", {"ticket_id": created["id"]}, "reader").structuredContent["status"] == "discarded"


def test_no_tool_changes_a_ticket_with_a_generic_patch(stack):
    created = stack.call("create_ticket", NEW_TICKET, "ops-bot").structuredContent
    mark = len(stack.api_requests)
    stack.call("update_ticket_status", {"ticket_id": created["id"], "status": "in_progress"}, "ops-bot")
    stack.call("get_ticket", {"ticket_id": created["id"]}, "ops-bot")
    paths = {path for method, path in stack.api_requests[mark:] if method in ("PATCH", "PUT", "DELETE")}
    assert paths == {f"/api/incidents/{created['id']}/status"}  # never PATCH /api/incidents/{id}


@pytest.mark.parametrize(
    "field, value, needle",
    [("category", "made_up", "Category"), ("origin", "alien", "Origin"), ("branch", "mars", "Branch"), ("title", "", "Title"), ("title", "x" * 121, "Title"), ("description", "", "Description")],
)
def test_create_ticket_names_the_invalid_field(stack, field, value, needle):
    before = count(stack, Incident)
    result = stack.call("create_ticket", {**NEW_TICKET, field: value}, "ops-bot")
    assert code_of(result) == errors.VALIDATION_ERROR and needle.lower() in result.content[0].text.lower()
    assert count(stack, Incident) == before


@pytest.mark.parametrize("ticket_id", [0, -5])
def test_a_ticket_number_that_is_not_positive_is_a_validation_error(stack, ticket_id):
    assert code_of(stack.call("get_ticket", {"ticket_id": ticket_id}, "reader")) == errors.VALIDATION_ERROR
    assert code_of(stack.call("update_ticket_status", {"ticket_id": ticket_id, "status": "in_progress"}, "ops-bot")) == errors.VALIDATION_ERROR


def test_a_ticket_that_does_not_exist_is_not_found(stack):
    for result in (stack.call("get_ticket", {"ticket_id": 987654}, "reader"), stack.call("update_ticket_status", {"ticket_id": 987654, "status": "in_progress"}, "ops-bot")):
        assert code_of(result) == errors.NOT_FOUND and "987654" in result.content[0].text


def test_an_unknown_status_is_a_validation_error_naming_the_values(stack, ticket):
    result = stack.call("update_ticket_status", {"ticket_id": ticket["id"], "status": "finished"}, "ops-bot")
    assert code_of(result) == errors.VALIDATION_ERROR and "in_progress" in result.content[0].text


# ================================================================== inventory: queries work, writes are rejected explicitly
def test_query_inventory_lists_searches_and_gets_real_products(stack):
    listed = stack.call("query_inventory", {"operation": "list"}, "agent").structuredContent
    assert listed["count"] == 2 and {p["sku"] for p in listed["products"]} == {"HOOD-BLU-M", "MOUSE-01"}
    found = stack.call("query_inventory", {"operation": "search", "product_query": "hoodie"}, "agent").structuredContent
    assert found["count"] == 1 and found["products"][0]["current_stock"] == 42 and found["products"][0]["warehouse"] == "ZGZ"  # 50 in, 8 out
    assert stack.call("query_inventory", {"operation": "search", "product_query": "Tech Brand"}, "agent").structuredContent["products"][0]["sku"] == "MOUSE-01"
    one = stack.call("query_inventory", {"operation": "get", "product_id": found["products"][0]["id"]}, "agent").structuredContent
    assert one["count"] == 1 and one["products"][0]["name"] == "Blue Hoodie"
    assert stack.call("query_inventory", {"operation": "search", "product_query": "unicorn"}, "agent").structuredContent["count"] == 0


WRITE_ATTEMPTS = ["create", "update", "delete", "adjust", "add", "remove", "receive", "dispatch", "set quantity", "modify stock", "Update", "INSERT", "decrement"]


@pytest.mark.parametrize("operation", WRITE_ATTEMPTS)
def test_every_write_operation_is_rejected_explicitly_with_its_own_code(stack, operation):
    result = stack.call("query_inventory", {"operation": operation}, "ops-bot")  # even the client with every scope
    assert result.isError and code_of(result) == errors.INVENTORY_READ_ONLY
    assert "read only" in result.content[0].text and "rejected" in result.content[0].text


def test_a_write_attempt_changes_nothing_and_no_write_request_reaches_the_api(stack):
    before = (count(stack, StockEntry), count(stack, StockExit))
    mark = len(stack.api_requests)
    for operation in WRITE_ATTEMPTS:
        stack.call("query_inventory", {"operation": operation, "product_id": 1, "product_query": "hoodie"}, "ops-bot")
    assert (count(stack, StockEntry), count(stack, StockExit)) == before
    assert stack.api_calls("/inventory", mark) == []  # a refused write never even called the inventory API


def test_the_inventory_tool_only_ever_sends_get_requests(stack):
    mark = len(stack.api_requests)
    stack.call("query_inventory", {"operation": "list"}, "agent")
    stack.call("query_inventory", {"operation": "get", "product_id": 1}, "agent")
    stack.call("query_inventory", {"operation": "search", "product_query": "mouse"}, "agent")
    inventory_calls = stack.api_calls("/inventory", mark)
    assert inventory_calls and {method for method, _ in inventory_calls} == {"GET"}


def test_an_unknown_operation_is_a_validation_error_not_a_write_error(stack):
    result = stack.call("query_inventory", {"operation": "frobnicate"}, "agent")
    assert code_of(result) == errors.VALIDATION_ERROR and "search, get, list" in result.content[0].text


def test_missing_arguments_are_validation_errors(stack):
    assert code_of(stack.call("query_inventory", {"operation": "search"}, "agent")) == errors.VALIDATION_ERROR
    assert code_of(stack.call("query_inventory", {"operation": "get"}, "agent")) == errors.VALIDATION_ERROR
    assert code_of(stack.call("query_inventory", {"operation": "get", "product_id": 99999}, "agent")) == errors.NOT_FOUND


def test_the_inventory_client_itself_refuses_any_method_but_get(stack):
    import asyncio

    from mcps.trackflow_tools.upstream import InventoryClient, InventoryReadOnly

    client = InventoryClient(stack.settings, transport=httpx.MockTransport(lambda request: pytest.fail("no request may leave")))
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(InventoryReadOnly):
            asyncio.run(client._send(method, "/inventory/products", json={"name": "x"}))


# ================================================================== distinct codes for authentication, authorization and validation
def test_authentication_authorization_and_validation_failures_have_different_codes_and_messages(stack):
    authentication = httpx.post(stack.mcp_url, json=RPC_LIST)
    authorization = stack.call("create_ticket", NEW_TICKET, "reader")
    validation = stack.call("get_ticket", {"ticket_id": 0}, "reader")
    not_found = stack.call("get_ticket", {"ticket_id": 987654}, "reader")
    read_only = stack.call("query_inventory", {"operation": "delete"}, "ops-bot")
    outcomes = {"authentication": (authentication.status_code, authentication.json()["error"]), "authorization": code_of(authorization), "validation": code_of(validation), "not_found": code_of(not_found), "read_only": code_of(read_only)}
    assert outcomes == {"authentication": (401, "missing_auth_header"), "authorization": "INSUFFICIENT_SCOPE", "validation": "VALIDATION_ERROR", "not_found": "NOT_FOUND", "read_only": "INVENTORY_READ_ONLY"}
    texts = [authorization.content[0].text, validation.content[0].text, not_found.content[0].text, read_only.content[0].text]
    assert len(set(texts)) == 4  # four different messages


def test_the_documented_codes_are_the_ones_the_module_defines():
    assert set(errors.CODES) == {"INSUFFICIENT_SCOPE", "VALIDATION_ERROR", "NOT_FOUND", "INVENTORY_READ_ONLY", "UPSTREAM_UNAVAILABLE"}
    for code in errors.CODES:
        assert f"`{code}`" in errors.__doc__  # the table in the module documentation lists it
    assert errors.EXIT_CONFIG_ERROR == 2


# ================================================================== every tool invocation is logged: client, tool, result
def audit_lines(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "trackflow.mcp.audit"]


def test_every_tool_call_writes_one_audit_line_with_client_tool_and_result(stack, ticket, caplog):
    caplog.set_level(logging.INFO, logger="trackflow.mcp.audit")
    stack.call("get_ticket", {"ticket_id": ticket["id"]}, "agent")
    stack.call("query_inventory", {"operation": "list"}, "agent")
    stack.call("create_ticket", NEW_TICKET, "agent")  # refused: no scope
    stack.call("query_inventory", {"operation": "delete"}, "ops-bot")  # refused: read only
    stack.call("get_ticket", {"ticket_id": 987654}, "reader")  # not found
    lines = audit_lines(caplog)
    assert [(l["tool"], l["client_id"], l["outcome"], l["code"]) for l in lines] == [
        ("get_ticket", "agent", "ok", None),
        ("query_inventory", "agent", "ok", None),
        ("create_ticket", "agent", "error", "INSUFFICIENT_SCOPE"),
        ("query_inventory", "ops-bot", "error", "INVENTORY_READ_ONLY"),
        ("get_ticket", "reader", "error", "NOT_FOUND"),
    ]
    assert all(l["event"] == "tool_call" and isinstance(l["duration_ms"], int) and l["subject"] for l in lines)
    assert lines[0]["arguments"] == {"ticket_id": ticket["id"]} and lines[2]["scopes"] == ["tickets:read", "inventory:read"]


def test_the_audit_log_never_contains_the_access_token_and_shortens_long_text(stack, caplog):
    caplog.set_level(logging.INFO, logger="trackflow.mcp.audit")
    headers = stack.headers("ops-bot")
    stack.call("create_ticket", {**NEW_TICKET, "description": "long " * 100}, "ops-bot")
    text = " ".join(r.getMessage() for r in caplog.records)
    assert headers["Authorization"].split()[1] not in text
    line = audit_lines(caplog)[-1]
    assert line["arguments"]["description"].endswith("…") and len(line["arguments"]["description"]) <= audit.TEXT_LIMIT + 1


# ================================================================== configuration errors and exit codes
def test_missing_or_invalid_configuration_is_a_clear_error():
    base = {"MCP_PUBLIC_URL": "http://localhost:8765/mcp", "OAUTH_ISSUER": "http://localhost:9000", "API_SERVICE_TOKEN": "t"}
    assert load_settings(base).port == 8765 and load_settings(base).audience == "http://localhost:8765/mcp"
    for missing in ("MCP_PUBLIC_URL", "OAUTH_ISSUER"):
        with pytest.raises(ConfigError, match=missing):
            load_settings({k: v for k, v in base.items() if k != missing})
    with pytest.raises(ConfigError, match="identity"):
        load_settings({k: v for k, v in base.items() if k != "API_SERVICE_TOKEN"})
    with pytest.raises(ConfigError, match="path"):
        load_settings({**base, "MCP_PUBLIC_URL": "http://localhost:8765"})
    with pytest.raises(ConfigError, match="number"):
        load_settings({**base, "MCP_PORT": "eighty"})


def test_the_process_exits_with_code_2_on_a_configuration_error_and_3_when_the_provider_is_down(monkeypatch):
    from mcps.trackflow_tools import __main__ as entry

    for name in ("MCP_PUBLIC_URL", "OAUTH_ISSUER", "API_SERVICE_EMAIL", "API_SERVICE_PASSWORD", "API_SERVICE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert entry.main() == 2
    monkeypatch.setenv("MCP_PUBLIC_URL", "http://localhost:8765/mcp")
    monkeypatch.setenv("OAUTH_ISSUER", "http://127.0.0.1:1")  # nothing listens there
    monkeypatch.setenv("API_SERVICE_TOKEN", "t")
    assert entry.main() == 3


def test_the_transport_is_streamable_http_and_stateless(stack):
    assert stack.settings.resource_path == "/mcp"
    response = httpx.post(stack.mcp_url, json=RPC_LIST, headers={**stack.headers("ops-bot"), "accept": "application/json, text/event-stream"})
    assert response.status_code == 200 and "mcp-session-id" not in response.headers  # no session to keep or to hijack
    assert response.headers["content-type"].startswith("application/json")
    assert {t["name"] for t in response.json()["result"]["tools"]} == {"get_ticket", "create_ticket", "update_ticket_status", "query_inventory"}


def test_the_smoke_client_runs_one_flow_per_tool_against_the_real_server(stack, capsys):
    import asyncio

    from mcps.trackflow_tools import smoke_client

    assert asyncio.run(smoke_client.main(stack.mcp_url, stack.token("ops-bot"))) == 0
    out = capsys.readouterr().out
    assert "tools: " in out and "create_ticket (write)" in out and "get_ticket (read)" in out and "query_inventory (read)" in out
    assert "create_ticket: ok" in out and "get_ticket: ok" in out and "update_ticket_status open -> in_progress: ok" in out
    lines = {line.split(":")[0].strip(): line for line in out.splitlines() if line.startswith("  ")}
    assert "ERROR" in lines["update_ticket_status in_progress -> open (not allowed)"] and "[VALIDATION_ERROR]" in lines["update_ticket_status in_progress -> open (not allowed)"]
    assert "ERROR" in lines["query_inventory WRITE attempt (must fail)"] and "[INVENTORY_READ_ONLY]" in lines["query_inventory WRITE attempt (must fail)"]
