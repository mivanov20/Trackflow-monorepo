"""The TrackFlow MCP server: the tools, their schemas, their scopes and their rules.

Tools (what a client discovers with `tools/list`):

| Tool | Does | Scope | Kind |
|---|---|---|---|
| `get_ticket` | status and details of one ticket | `tickets:read` | read |
| `create_ticket` | opens a ticket in the Incidents Manager | `tickets:write` | write |
| `update_ticket_status` | moves a ticket along its lifecycle (`PATCH /api/incidents/{id}/status`) | `tickets:write` | write |
| `query_inventory` | products and current stock, READ ONLY | `inventory:read` | read |

LEAST PRIVILEGE. The transport only lets in a valid token (MCP Auth, in `app.py`). Each tool then asks for the one scope it needs, from the validated
token, so a client that was only given `tickets:read` can look at a ticket and nothing else. The agent's client has `tickets:read inventory:read`: it can read
and cannot create, change or reach anything else.

The incident tools are three separate tools, not one with a switch: each does one thing, has its own scope and its own schema. The inventory
tool is read-only by design: its client only sends GET (`upstream.InventoryClient`), and a write operation asked of the tool is REFUSED explicitly with
`[INVENTORY_READ_ONLY]`, not silently ignored and not mistaken for a typo.
"""

from __future__ import annotations

import re
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from mcpauth import MCPAuth
from pydantic import BaseModel, Field
from trackflow_shared.incidents import BRANCHES, CATEGORIES, ORIGINS, STATUSES

from . import audit, errors
from .config import SCOPES, Settings
from .errors import INSUFFICIENT_SCOPE, INVENTORY_READ_ONLY, VALIDATION_ERROR, ToolFailure
from .upstream import IncidentsClient, InventoryClient, InventoryReadOnly

INSTRUCTIONS = """TrackFlow company tools. Two systems, four tools:
* Incidents Manager (tickets): `get_ticket` (read), `create_ticket` and `update_ticket_status` (write).
* Inventory manager: `query_inventory`, READ ONLY. It never changes stock; any write operation is rejected with [INVENTORY_READ_ONLY].
Every call needs an OAuth bearer token; each tool needs its own scope (tickets:read, tickets:write, inventory:read). A missing scope answers [INSUFFICIENT_SCOPE].
Failures start with a code in brackets: [VALIDATION_ERROR] (fix the argument and retry), [NOT_FOUND] (that ticket or product does not exist), [UPSTREAM_UNAVAILABLE]
(the company system did not answer: retry later, do not guess the value). Ticket lifecycle: open -> in_progress -> resolved; open or in_progress -> discarded; resolved and discarded are final."""

ALLOWED_OPERATIONS = ("search", "get", "list")
# anything that asks the inventory to change is a write, whatever the wording
WRITE_WORDS = re.compile(r"(?i)\b(create|add|insert|new|update|modify|edit|patch|put|set|adjust|correct|change|delete|remove|drop|write|receive|dispatch|ship|transfer|move|reserve|increment|decrement|subtract)\b")


class Ticket(BaseModel):
    """A ticket of the Incidents Manager, exactly as its API returns it."""

    id: int = Field(description="The ticket number.")
    title: str
    description: str
    category: str = Field(description=f"One of: {', '.join(CATEGORIES)}.")
    status: str = Field(description=f"One of: {', '.join(STATUSES)}.")
    origin: str = Field(description=f"Where it came from, one of: {', '.join(ORIGINS)}.")
    branch: str = Field(description=f"The branch, one of: {', '.join(BRANCHES)}.")
    created_at: str = Field(description="UTC, ISO 8601.")
    updated_at: str = Field(description="UTC, ISO 8601.")


class Product(BaseModel):
    """A product (SKU) in one warehouse, with its stock computed from the inventory movements."""

    id: int
    name: str
    sku: str
    client_name: str
    category: str
    warehouse: str = Field(description="LA (Los Angeles) or ZGZ (Zaragoza).")
    current_stock: int = Field(description="Units in stock now: all inbound minus all outbound for this product in this warehouse.")


class InventoryResult(BaseModel):
    operation: str
    count: int
    products: list[Product]


def _choice(values) -> dict:
    return {"enum": list(values)}


TicketId = Annotated[int, Field(description="The ticket number (a positive integer).", json_schema_extra={"minimum": 1})]


def build_server(settings: Settings, mcp_auth: MCPAuth, incidents: IncidentsClient, inventory: InventoryClient) -> FastMCP:
    host = settings.public_url.split("/")[2]
    server = FastMCP(
        "trackflow-tools",
        instructions=INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
        streamable_http_path=settings.resource_path,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=[host, "localhost:*", "127.0.0.1:*"], allowed_origins=[settings.resource_origin, *settings.allowed_origins, "http://localhost:*", "http://127.0.0.1:*"]),
    )

    def caller() -> dict:
        info = mcp_auth.auth_info
        if info is None:  # the middleware guarantees a token; this is the guard if a tool is ever reached without it
            raise ToolFailure(INSUFFICIENT_SCOPE, "No validated access token for this call.")
        return {"client_id": info.client_id, "subject": info.subject, "scopes": list(info.scopes)}

    def require_scope(scope: str) -> None:
        if scope not in caller()["scopes"]:
            raise ToolFailure(INSUFFICIENT_SCOPE, f"This tool needs the '{scope}' scope ({SCOPES[scope]}); the token has: {', '.join(caller()['scopes']) or 'no scopes'}.")

    def audited(name: str):
        return audit.audited(name, lambda: _safe_caller())

    def _safe_caller() -> dict:
        try:
            return caller()
        except ToolFailure:
            return {"client_id": None, "subject": None, "scopes": []}

    def check_ticket_id(ticket_id: int) -> None:
        if not isinstance(ticket_id, int) or isinstance(ticket_id, bool) or ticket_id < 1:
            raise ToolFailure(VALIDATION_ERROR, "ticket_id must be a positive integer (the ticket number).")

    @server.tool(
        name="get_ticket",
        description="Check a ticket of the TrackFlow Incidents Manager: its current status, category, origin, branch and dates, read live (never cached). Read only. "
        "Use it to answer 'what is the status of ticket N'. Needs scope tickets:read. Errors: [NOT_FOUND] if there is no such ticket, [VALIDATION_ERROR] for an invalid number.",
        annotations=ToolAnnotations(title="Get ticket", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    @audited("get_ticket")
    async def get_ticket(ticket_id: TicketId) -> Ticket:
        require_scope("tickets:read")
        check_ticket_id(ticket_id)
        return Ticket.model_validate(await incidents.get_ticket(ticket_id))

    @server.tool(
        name="create_ticket",
        description="Open a new ticket in the TrackFlow Incidents Manager. Writes data: it creates a ticket with status 'open'. All five fields are required; category, origin and branch "
        "must be one of the listed values. Returns the created ticket with its number. Needs scope tickets:write. Errors: [VALIDATION_ERROR] naming each invalid field.",
        annotations=ToolAnnotations(title="Create ticket", readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
    )
    @audited("create_ticket")
    async def create_ticket(
        title: Annotated[str, Field(description="Short summary of the problem (up to 120 characters).")],
        description: Annotated[str, Field(description="What happened, with the details a colleague needs (up to 5000 characters).")],
        category: Annotated[str, Field(description="The kind of incident.", json_schema_extra=_choice(CATEGORIES))],
        origin: Annotated[str, Field(description="Who reported it.", json_schema_extra=_choice(ORIGINS))],
        branch: Annotated[str, Field(description="The branch it concerns.", json_schema_extra=_choice(tuple(BRANCHES)))],
    ) -> Ticket:
        require_scope("tickets:write")
        payload = {"title": title, "description": description, "category": category, "origin": origin, "branch": branch}
        return Ticket.model_validate(await incidents.create_ticket(payload))

    @server.tool(
        name="update_ticket_status",
        description="Move a ticket along its lifecycle through the Incidents Manager's status endpoint. Writes data. Allowed moves: open -> in_progress or discarded; "
        "in_progress -> resolved or discarded; resolved and discarded are final. Returns the updated ticket. Needs scope tickets:write. "
        "Errors: [VALIDATION_ERROR] for an unknown status or a move the lifecycle does not allow, [NOT_FOUND] if there is no such ticket.",
        annotations=ToolAnnotations(title="Update ticket status", readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
    )
    @audited("update_ticket_status")
    async def update_ticket_status(
        ticket_id: TicketId,
        status: Annotated[str, Field(description="The new status.", json_schema_extra=_choice(STATUSES))],
    ) -> Ticket:
        require_scope("tickets:write")
        check_ticket_id(ticket_id)
        return Ticket.model_validate(await incidents.update_status(ticket_id, status))

    @server.tool(
        name="query_inventory",
        description="READ ONLY. Look up products and their current stock in the TrackFlow inventory manager. operation='search' with product_query (a name, SKU or client, or part of one), "
        "operation='get' with product_id, or operation='list' for all products. This tool NEVER changes inventory: any write operation (create, update, adjust, delete, receive, dispatch...) is "
        "rejected with [INVENTORY_READ_ONLY]. Needs scope inventory:read. Errors: [VALIDATION_ERROR] for an unknown operation or a missing argument, [NOT_FOUND] for an unknown product_id.",
        annotations=ToolAnnotations(title="Query inventory (read only)", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    @audited("query_inventory")
    async def query_inventory(
        operation: Annotated[str, Field(description="What to do: 'search', 'get' or 'list'. Only reading is possible.", json_schema_extra=_choice(ALLOWED_OPERATIONS))],
        product_query: Annotated[str | None, Field(description="For 'search': a product name, SKU or client name, or part of one.")] = None,
        product_id: Annotated[int | None, Field(description="For 'get': the product id.")] = None,
    ) -> InventoryResult:
        require_scope("inventory:read")
        wanted = (operation or "").strip().lower()
        if wanted not in ALLOWED_OPERATIONS:
            if WRITE_WORDS.search(wanted):
                raise ToolFailure(INVENTORY_READ_ONLY, f"The inventory tool is read only: '{operation}' would change inventory and is rejected. Allowed operations: {', '.join(ALLOWED_OPERATIONS)}.")
            raise ToolFailure(VALIDATION_ERROR, f"Unknown operation '{operation}'. Allowed operations: {', '.join(ALLOWED_OPERATIONS)}.")
        try:
            if wanted == "get":
                if product_id is None or product_id < 1:
                    raise ToolFailure(VALIDATION_ERROR, "operation 'get' needs product_id (a positive integer).")
                found = [await inventory.get_product(product_id)]
            else:
                products = await inventory.list_products()
                if wanted == "search":
                    needle = (product_query or "").strip().lower()
                    if len(needle) < 2:
                        raise ToolFailure(VALIDATION_ERROR, "operation 'search' needs product_query (at least 2 characters).")
                    products = [p for p in products if needle in " ".join(str(p.get(k, "")) for k in ("name", "sku", "client_name")).lower()]
                found = products
        except InventoryReadOnly as refusal:  # defence in depth: the client itself refuses any method but GET
            raise errors.ToolFailure(INVENTORY_READ_ONLY, str(refusal)) from None
        return InventoryResult(operation=wanted, count=len(found), products=[Product.model_validate(p) for p in found])

    return server
