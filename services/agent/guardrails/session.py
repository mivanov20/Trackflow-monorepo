"""Content guard: session-based authorization. The agent may only talk about an order that belongs to the customer authenticated in the session.

CONTEXT-company.md: the risk of a user looking up someone else's order by swapping a tracking number is high in this domain and must be blocked explicitly. So this is
not about topics, it is about WHO is asking: every order or tracking reference in a message is checked against the registry of who owns what (`customer_shipments`)
BEFORE the agent, a tool or the model sees the message. A reference that is not the session customer's is refused for lack of authorization, and the refusal
does not reveal whether the order exists (an unknown number and somebody else's number get the same answer), so the guard cannot be used to probe for valid numbers.

The session is the signed-in account. Customers see only their own orders; staff (admin, manager) are the support team and may look up any order, which is logged.
An owned order also gives the agent a VERIFIED record (its real status and its country), which is what decides whether the US or the Spain policy applies, whatever
the user claims.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import Engine
from sqlmodel import Session, or_, select

from ... import database
from ...models import CustomerShipment

STAFF_ROLES = ("admin", "manager")

_REFERENCES = [
    re.compile(r"\b(?:order|orders|pedido|pedidos)\s*(?:number|no\.?|id|num(?:ero)?|nº|n°)?\s*[:#]?\s*#?\s*([a-z]{0,3}\d{3,12}[a-z]{0,3})\b", re.IGNORECASE),
    re.compile(r"(?<!\w)#\s?([a-z]{0,3}\d{3,12}[a-z]{0,3})\b", re.IGNORECASE),
    re.compile(r"\b(?:tracking|track(?:ing)?\s*(?:number|no\.?|id|code|num(?:ero)?)|seguimiento|waybill|awb)\s*(?:number|no\.?|id|code|num(?:ero)?|nº)?\s*[:#]?\s*([a-z0-9][a-z0-9-]{4,24})\b", re.IGNORECASE),
    re.compile(r"\b([a-z]{2}[\s-]?\d{4,}[a-z]{0,2})\b", re.IGNORECASE),
]


@dataclass(frozen=True)
class SessionContext:
    """Who is asking, as the API knows it. `customer_id` is the authenticated account."""

    customer_id: str
    role: str = "user"

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES


@dataclass(frozen=True)
class Shipment:
    tracking_number: str
    order_id: str | None
    customer_id: str
    country: str
    status: str


@dataclass(frozen=True)
class Authorization:
    allowed: bool
    references: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()
    shipments: tuple[Shipment, ...] = ()  # the verified records of the references the session may see


def normalise_reference(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def extract_references(text: str) -> list[str]:
    """Every order number or tracking number mentioned, normalised (upper case, no `#`, no dashes), in order of appearance, without repeats."""
    found: list[str] = []
    for pattern in _REFERENCES:
        for match in pattern.finditer(text or ""):
            ref = normalise_reference(match.group(1))
            if ref and any(c.isdigit() for c in ref) and ref not in found:
                found.append(ref)
    return found


def authorize(session: SessionContext, text: str, engine: Engine | None = None) -> Authorization:
    """May this session talk about every order or tracking number in `text`? One that is not its own (or does not exist) denies the whole message."""
    references = extract_references(text)
    if not references:
        return Authorization(True)
    engine = engine or database.engine
    shipments: list[Shipment] = []
    denied: list[str] = []
    with Session(engine) as db:
        for ref in references:
            row = db.exec(select(CustomerShipment).where(or_(CustomerShipment.tracking_number == ref, CustomerShipment.order_id == ref))).first()
            if row is not None and (row.customer_id == session.customer_id or session.is_staff):
                shipments.append(Shipment(row.tracking_number, row.order_id, row.customer_id, row.country, row.status))
            else:
                denied.append(ref)  # somebody else's, or not there: the same answer for both
    return Authorization(not denied, tuple(references), tuple(denied), tuple(shipments))
