"""The two live tools of the support agent: ticket lookup (incident manager) and stock lookup (inventory manager).

LIVE DATA, NOT KNOWLEDGE. The RAG answers from stable documents. A ticket's status or a product's stock changes all the time, so
nothing is copied or indexed: every call reads the real service at that moment.

TRANSPORT: in-process, through the service layer of the incident and inventory managers (the same models, the same `IncidentRead` schema
and the same `sku_read` stock calculation their endpoints use), because they live in this same package. There is no parallel dataset.
AUTH: the agent endpoint already requires a signed-in user, and the incident and inventory routers only require one as well (no per-user
filtering), so an in-process read needs no extra credential and none is hardcoded. If the managers become separate services, only
the two `_read_*` functions change (to an HTTP call with a token read from the environment).

RULES, for both tools:
* read-only: a tool only selects. No insert, update, delete or commit exists in this module (a test enforces it).
* one responsibility each: `lookup_ticket` reads tickets, `lookup_stock` reads stock. They are separate functions and separate graph nodes.
* an explicit numeric timeout (`TOOL_TIMEOUT_SECONDS`, 4 s): a service that does not answer in time does not hang the graph.
* a result that is never an exception: `ToolResult.status` is `ok`, `not_found`, `timeout` or `unavailable`, and the graph has a path for
  each failure (an honest answer, never a made-up status).
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlmodel import Session, select

from .. import database
from ..models import SKU, Incident
from ..routers.inventory import sku_read
from ..schemas import IncidentRead, SKURead

TOOL_TIMEOUT_SECONDS = float(os.getenv("AGENT_TOOL_TIMEOUT_SECONDS", "4"))
MAX_PRODUCTS = 5
DESCRIPTION_PREVIEW = 300
TICKET_SOURCE = "incident-manager"  # the `source_document` of the chunk a live result becomes in the prompt
STOCK_SOURCE = "inventory-manager"

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-tool")


# ----------------------------------------------------------------------------- typed contracts
class TicketLookupInput(BaseModel):
    """Input of `lookup_ticket`: the incident id (the number a support agent calls the ticket number)."""

    ticket_id: int = Field(gt=0)


class StockLookupInput(BaseModel):
    """Input of `lookup_stock`: a product name, SKU or client name, or part of one."""

    product_query: str = Field(min_length=2, max_length=120)


ToolStatus = Literal["ok", "not_found", "timeout", "unavailable"]


class ToolResult(BaseModel):
    """What a tool returns, always (it never raises). `data` is the real service's own record(s): `IncidentRead` for a ticket, a list of
    `SKURead` for stock."""

    tool: Literal["ticket_lookup", "stock_lookup"]
    status: ToolStatus
    input: dict[str, Any]
    data: Any | None = None
    message: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)  # what the trace keeps: no free text of the ticket


class ToolTimeout(Exception):
    pass


def call_with_timeout(function, seconds: float = TOOL_TIMEOUT_SECONDS):
    """Runs `function` and gives up after `seconds`: the graph never waits longer. (The worker thread may finish later; its result is dropped.)"""
    future = _executor.submit(function)
    try:
        return future.result(timeout=seconds)
    except FutureTimeout:
        raise ToolTimeout(f"no answer within {seconds:g} seconds") from None


# ----------------------------------------------------------------------------- the reads (the only code that touches the services)
def _read_ticket(ticket_id: int) -> dict | None:
    with Session(database.engine) as session:
        incident = session.get(Incident, ticket_id)
        return IncidentRead.model_validate(incident).model_dump(mode="json") if incident else None


def _read_stock(product_query: str) -> list[dict]:
    with Session(database.engine) as session:
        statement = select(SKU).where(or_(SKU.name.icontains(product_query, autoescape=True), SKU.sku.icontains(product_query, autoescape=True), SKU.client_name.icontains(product_query, autoescape=True)))
        products = session.exec(statement.order_by(SKU.name, SKU.warehouse).limit(MAX_PRODUCTS)).all()
        return [sku_read(session, product).model_dump(mode="json") for product in products]


# ----------------------------------------------------------------------------- the tools
def lookup_ticket(ticket_id: int, *, timeout: float = TOOL_TIMEOUT_SECONDS) -> ToolResult:
    """Current state of one ticket, read from the incident manager. Read-only."""
    request = TicketLookupInput(ticket_id=ticket_id)
    base = {"tool": "ticket_lookup", "input": request.model_dump()}
    try:
        record = call_with_timeout(lambda: _read_ticket(request.ticket_id), timeout)
    except ToolTimeout as error:
        return ToolResult(**base, status="timeout", message=f"The incident manager did not answer ({error}).")
    except Exception as error:  # noqa: BLE001 - any failure of the service is a result, not a crash
        return ToolResult(**base, status="unavailable", message=f"The incident manager could not be read ({type(error).__name__}).")
    if record is None:
        return ToolResult(**base, status="not_found", message=f"There is no ticket {request.ticket_id} in the incident manager.")
    ticket = IncidentRead.model_validate(record)
    summary = {"id": ticket.id, "status": ticket.status, "category": ticket.category, "origin": record["origin"], "branch": ticket.branch, "updated_at": record["updated_at"]}
    return ToolResult(**base, status="ok", data=record, summary=summary)


def lookup_stock(product_query: str, *, timeout: float = TOOL_TIMEOUT_SECONDS) -> ToolResult:
    """Current stock of the products matching a name, SKU or client, read from the inventory manager. Read-only."""
    request = StockLookupInput(product_query=product_query.strip())
    base = {"tool": "stock_lookup", "input": request.model_dump()}
    try:
        records = call_with_timeout(lambda: _read_stock(request.product_query), timeout)
    except ToolTimeout as error:
        return ToolResult(**base, status="timeout", message=f"The inventory manager did not answer ({error}).")
    except Exception as error:  # noqa: BLE001
        return ToolResult(**base, status="unavailable", message=f"The inventory manager could not be read ({type(error).__name__}).")
    if not records:
        return ToolResult(**base, status="not_found", message=f"No product matching '{request.product_query}' in the inventory manager.")
    products = [SKURead.model_validate(r) for r in records]
    summary = {"matches": [{"sku": p.sku, "warehouse": p.warehouse, "current_stock": p.current_stock} for p in products]}
    return ToolResult(**base, status="ok", data=records, summary=summary)


# ----------------------------------------------------------------------------- results in the words of the agent
def _ticket_text(ticket: dict) -> str:
    description = " ".join(str(ticket.get("description", "")).split())[:DESCRIPTION_PREVIEW]
    return (
        f"Ticket {ticket['id']} (LIVE from the incident manager, read just now): status {ticket['status']}; category {ticket['category']}; "
        f"source (origin) {ticket['origin']}; branch {ticket['branch']}; created {ticket['created_at']}; last updated {ticket['updated_at']}. "
        f"Title: {ticket['title']}. Description: {description}"
    )


def _stock_text(records: list[dict]) -> str:
    lines = [f"{p['name']} (SKU {p['sku']}, client {p['client_name']}, warehouse {p['warehouse']}): {p['current_stock']} units in stock" for p in records]
    return "Stock (LIVE from the inventory manager, read just now): " + "; ".join(lines)


def live_chunks(results: list[dict]) -> list[dict]:
    """Tool results as context blocks for `rag.generate_answer`, so generation, sources and the prompt rules are the same as for the RAG.

    A failed lookup also becomes a block, telling the model the value is UNKNOWN: the model must say it could not confirm it.
    """
    chunks = []
    for index, raw in enumerate(results):
        result = ToolResult.model_validate(raw)
        is_ticket = result.tool == "ticket_lookup"
        source = TICKET_SOURCE if is_ticket else STOCK_SOURCE
        label = f"Ticket {result.input['ticket_id']} (live)" if is_ticket else f"Stock of '{result.input['product_query']}' (live)"
        if result.status == "ok":
            text = _ticket_text(result.data) if is_ticket else _stock_text(result.data)
        else:
            text = f"LIVE lookup could not be completed ({result.status}): {result.message} The current value is UNKNOWN. Say that you could not confirm it right now; do not guess it."
        chunks.append({"source_document": source, "section": label, "chunk_index": index, "text": text, "score": 1.0})
    return chunks


def fallback_message(results: list[dict]) -> str:
    """The honest answer when every live lookup failed and there is nothing else to answer from. Never a status or a number."""
    parts = []
    for raw in results:
        result = ToolResult.model_validate(raw)
        if result.tool == "ticket_lookup":
            number = result.input["ticket_id"]
            parts.append(f"I couldn't find a ticket {number} in the incident manager. Please check the number." if result.status == "not_found" else f"I couldn't confirm ticket {number}'s status right now: the incident manager didn't respond.")
        else:
            product = result.input["product_query"]
            parts.append(f"I couldn't find a product matching '{product}' in the inventory manager. Please check the name or SKU." if result.status == "not_found" else f"I couldn't check the stock of '{product}' right now: the inventory manager didn't respond.")
    return " ".join(parts) + " Please try again in a moment or check the system directly before telling the client anything."
