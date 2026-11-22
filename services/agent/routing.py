"""The planner: from the text of the question, which sources does the agent need? Never from an instruction of the user.

    live ticket status ("what's the status of ticket 482?")   -> ticket tool        (incident manager: changes all the time)
    live stock ("do we have stock of X?")                      -> stock tool         (inventory manager: changes all the time)
    policies, SLA, returns, carriers, storage pricing...       -> RAG                (stable documents)
    a question with both ("ticket 482 ... what's our returns policy?") -> the tool(s) first, then the RAG
    a ticket question with no ticket number                    -> ask for the number (never guess a ticket)
    anything else                                              -> the RAG, whose threshold decides "I don't have information"

It is a set of explicit rules (a pure function, `plan_sources`), not a model call: the same question always takes the same path, the plan is
written to the trace, and it is tested without a model. The price is that unusual wording can be missed (a paraphrase with no ticket word
and no number); that falls back to the RAG, which says honestly that it has no information. Swapping in a model-based planner only means
replacing this function: it returns the same plan.
"""

from __future__ import annotations

import re
from typing import Any

# "ticket 482", "incident #12", "case number 7", "#482"
TICKET_ID = re.compile(r"\b(?:ticket|incident|case)s?\s*(?:number|no\.?|id|nº|n°)?\s*[:#]?\s*#?\s*(\d{1,9})\b|#(\d{1,9})\b", re.IGNORECASE)
TICKET_WORDS = re.compile(r"\b(ticket|incident|case)\b", re.IGNORECASE)
TICKET_STATUS_WORDS = re.compile(r"\b(status|state|open|closed|resolved|pending|in progress|update|progress|assigned|whatever happened)\b", re.IGNORECASE)

# a product is asked for after one of these phrases; the first pattern that gives a name wins
STOCK_PATTERNS = (
    re.compile(r"\b(?:stock|inventory|units|availability|available)\s+(?:of|for|on)\s+(?P<q>[^?.!\n]+)", re.IGNORECASE),
    re.compile(r"\b(?:do|does|can)\s+(?:we|you|trackflow)\s+have\s+(?:any\s+)?(?P<q>[^?.!\n]+)", re.IGNORECASE),
    re.compile(r"\bhow\s+many\s+(?P<q>[^?.!\n]+?)\s+(?:do\s+we\s+have|are\s+(?:there|left|in\s+stock|available)|we\s+have|left)\b", re.IGNORECASE),
    re.compile(r"\b(?P<q>[^?.!\n]+?)\s+(?:in\s+stock|out\s+of\s+stock)\b", re.IGNORECASE),
)
STOCK_CUT = re.compile(
    r"\s+(?:and\s+(?:what|how|who|which|is|are|can|do|does|when|where)\b|(?:in\s+stock|in\s+inventory|in\s+the\s+warehouse|available|left|are|is|do\s+we|we\s+have|right\s+now|today|at\s+\w+|in\s+(?:zaragoza|los\s+angeles))\b).*$",
    re.IGNORECASE,
)
STOCK_LEADING = re.compile(r"^(?:is|are|do|does|we|have|got|the|a|an|any|some|of|for|on)\s+", re.IGNORECASE)

# the topics of the knowledge base (docs/company-knowledge-base/)
KNOWLEDGE_WORDS = re.compile(
    r"\b(sla|delivery|deliveries|shipping|express|customs|return|returns|refund|reverse logistics|carrier|carriers|coverage|ups|fedex|dhl|mrw|seur|storage|pricing|price|prices|rate|rates|fee|fees|discount|"
    r"black friday|christmas|january sales|peak|policy|policies|procedure|window|compensation|aging report|long-term|grace period|who pays|how long|how much does)\b",
    re.IGNORECASE,
)

STEP_ORDER = ("ticket", "stock", "rag")


_ORDER_WORDS = re.compile(r"\b(?:order|orders|tracking|parcel|package|shipment|pedido|paquete|envio|seguimiento)\b", re.IGNORECASE)


def extract_ticket_id(question: str) -> int | None:
    match = TICKET_ID.search(question)
    if not match:
        return None
    if match.group(2) and not match.group(1) and _ORDER_WORDS.search(question):
        return None  # a bare "#45821" next to the word order or tracking is an order number, which the session guard handles, not a ticket
    value = int(next(group for group in match.groups() if group))
    return value if value > 0 else None


def extract_product_query(question: str) -> str | None:
    for pattern in STOCK_PATTERNS:
        match = pattern.search(question)
        if not match:
            continue
        name = STOCK_CUT.sub("", match.group("q")).strip(" ,;:-\"'")
        while STOCK_LEADING.match(name):
            name = STOCK_LEADING.sub("", name, count=1).strip(" ,;:-\"'")
        if len(name) >= 2 and not KNOWLEDGE_WORDS.fullmatch(name):
            return name
    return None


def plan_sources(question: str) -> dict[str, Any]:
    """The plan for one question: `steps` in the order they will run (tools first, then the RAG), and why. JSON-friendly: it goes to the trace."""
    ticket_id = extract_ticket_id(question)
    product = extract_product_query(question)
    knowledge = bool(KNOWLEDGE_WORDS.search(question))
    asks_ticket_without_number = ticket_id is None and bool(TICKET_WORDS.search(question)) and bool(TICKET_STATUS_WORDS.search(question)) and not knowledge
    # a stock question is only live if it names a product; "do we have a returns policy?" is a knowledge question
    stock_query = product if (product and not (knowledge and not re.search(r"\b(stock|inventory|units|sku)\b", question, re.IGNORECASE))) else None
    live = ticket_id is not None or stock_query is not None
    steps = []
    if ticket_id is not None:
        steps.append("ticket")
    if stock_query is not None:
        steps.append("stock")
    if knowledge or not live:
        steps.append("rag")
    reasons = []
    if ticket_id is not None:
        reasons.append(f"names ticket {ticket_id}: live ticket status")
    if stock_query is not None:
        reasons.append(f"asks for the stock of '{stock_query}': live stock")
    if knowledge:
        reasons.append("touches a topic of the knowledge base")
    if not live and not knowledge:
        reasons.append("no live data requested: knowledge base")
    if asks_ticket_without_number:
        steps, reasons = [], ["asks about a ticket without its number: ask for it"]
    return {"steps": steps, "ticket_id": ticket_id, "stock_query": stock_query, "clarify": asks_ticket_without_number, "reason": "; ".join(reasons)}
