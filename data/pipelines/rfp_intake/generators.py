"""The generator agents of Part 2: one per department, each drafting ITS section of the pricing proposal from the Part 1 handoff.

Each generator receives a `brief` (built from the handoff: client, currency, volume, deadline, the Part 1 summary and ITS department's `key_aspects`), the facts of the knowledge
base for the client's country, and, on a retry, the feedback of the evaluators and the previous draft. It never receives the PDF or the RFP's Markdown.

A draft is Markdown with the sections CONTEXT-company.md asks of a proposal: scope, what the department contributes (capacity and onboarding / carriers and SLA / returns
process and turnaround), pricing in the country currency, a volume-based discount tier table, the on-time delivery SLA and the open items.

Figures: a price or a figure appears only if it comes from the knowledge base (`knowledge.Facts`) or from the RFP itself. What neither source has (cost per pallet, cost per shipment, returns
cost, capacity, onboarding time, the discount of each tier) is written as "to be confirmed by <owner>" and listed among the open items: a first draft that invents prices would be
worse than one that says what is missing. Discounts are never invented: the knowledge base says a preferential rate always goes through Miguel Torres.

With a model configured the generator asks it for the section (same inputs, the knowledge base text and the feedback) and falls back to the rules if it answers nothing usable;
either way the draft goes through the same evaluators.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import llm
from .config import COMMERCIAL_DIRECTOR, DEPARTMENTS
from .knowledge import Facts

MIN_WORDS = 60


@dataclass
class Draft:
    content: str
    engine: str = "rules"
    derived_numbers: list[str] = field(default_factory=list)  # numbers the generator computed (tier boundaries): not invented, derived from the RFP's volume


def tbc(owner: str, what: str) -> str:
    return f"To be confirmed by {owner} ({what})"


def count(value: int | float | None) -> str:
    return f"{value:,.0f}" if value is not None else ""


def tier_rows(volume: int | None, unit: str, discount_owner: str) -> tuple[list[str], list[str]]:
    """Three volume tiers. With the client's monthly volume they are built around it (the figures are derived from it); without it, they are named but not sized, and the table says so."""
    if volume:
        bands = [f"Up to {count(volume)} {unit} per month", f"{count(volume + 1)} to {count(volume * 2)} {unit} per month", f"Above {count(volume * 2)} {unit} per month"]
        derived = [str(volume), str(volume + 1), str(volume * 2)]
    else:
        bands = [f"Tier 1: lowest volume band ({unit} per month)", f"Tier 2: middle volume band ({unit} per month)", f"Tier 3: highest volume band ({unit} per month)"]
        derived = []
    rows = [
        f"| {bands[0]} | Standard price, no discount |",
        f"| {bands[1]} | To be set by {discount_owner} |",
        f"| {bands[2]} | To be set by {discount_owner} |",
    ]
    return rows, derived


def sla_paragraph(brief: dict, facts: Facts, concise: bool) -> list[str]:
    if facts.sla_percent is None:
        return ["TrackFlow's on-time delivery commitment could not be taken from the knowledge base: the owner must provide it."]
    lines = [f"TrackFlow's contractual on-time delivery SLA is {facts.sla_percent:g}% of deliveries per {facts.sla_period or 'month'}, measured by country."]
    if "lastmile" not in brief["departments_needed"]:
        lines.append(f"{brief['client']['name']} keeps its own carrier, so this commitment does not cover its last-mile deliveries.")
    if not concise:
        if facts.sla_compensation_percent is not None:
            lines.append(f"If a country falls below it for two consecutive months, the client is entitled to a {facts.sla_compensation_percent:g}% compensation on that country's monthly fee.")
        if facts.high_demand_notice:
            lines.append("The SLA is not guaranteed during declared high-demand dates (Black Friday, Christmas, January Sales in Spain), and clients are told in advance.")
    return lines


def open_items(brief: dict, owner: str, extra: list[str]) -> list[str]:
    items = list(brief["key_aspects"].get("open_questions", []))
    items.extend(extra)
    items.extend(f"Requested by the owner at the approval step: {request}" for request in brief.get("reviewer_requests", []))
    return list(dict.fromkeys(items))


def header(brief: dict, title: str) -> list[str]:
    client, spec = brief["client"], DEPARTMENTS[brief["department_id"]]
    return [
        f"## {title}: {client['name'] or 'the client'}",
        "",
        f"Prepared by {spec['name']} ({spec['owner']}). Prices are quoted in {client['currency'] or 'the currency of the client (country not confirmed)'}.",
        "",
    ]


def scope_line(brief: dict, subject: str) -> str:
    volume = brief.get("rfp_monthly_volume", brief["monthly_volume"])  # what the CLIENT said, even when the arbiter capped the volume this proposal is quoted for
    deadline = brief.get("deadline")
    text = f"{brief['key_aspects']['summary'].rstrip('.')}."
    text += f" The client expects about {count(volume)} orders per month." if volume else " The RFP does not state the monthly order volume, so the figures below that depend on it are to be confirmed."
    if deadline:
        text += f" Date given by the client: {deadline}."
    for note in brief.get("arbitration_notes", []):  # a decision of the fixed arbiter (Part 3) that changes what this section may say
        text += f" {note}"
    return text


# ----------------------------------------------------------------------------------------------------------- one generator per department
def draft_warehouse(brief: dict, facts: Facts, concise: bool) -> Draft:
    owner, aspects, client = DEPARTMENTS["warehouse"]["owner"], brief["key_aspects"], brief["client"]
    figures = aspects.get("stated_figures", {})
    currency = client["currency"] or "the client's currency"
    rows, derived = tier_rows(brief["monthly_volume"], "orders", COMMERCIAL_DIRECTOR)
    lines = header(brief, "Warehousing proposal")
    lines += ["### Scope", scope_line(brief, "warehousing")]
    if figures.get("skus"):
        lines.append(f"The client lists {count(figures['skus'])} SKUs" + (f", in or near {figures['preferred_location']}." if figures.get("preferred_location") else "."))
    lines += ["", "### Capacity and onboarding"]
    lines.append(f"- Storage capacity: {tbc(owner, 'the capacity in pallets or SKUs that TrackFlow commits')}.")
    lines.append(f"- Onboarding time: {tbc(owner, 'time until the client stock can be received and shipped')}.")
    if re.search(r"integrat", " ".join(aspects.get("asks_of_owner", [])), re.IGNORECASE):
        lines.append("- Systems integration: the client asks to connect its ERP through an API; the owner confirms what it takes.")
    lines += ["", f"### Pricing ({currency})", f"| Item | Price ({currency}) | Basis |", "|---|---|---|"]
    if facts.storage_rate is not None:
        lines.append(f"| Standard storage in {facts.storage_location} | {facts.storage_rate:g} {facts.storage_currency} per cubic meter per month | Standard rate of the knowledge base |")
        if facts.storage_grace_days:
            lines.append(f"| New stock | No storage charge during the first {facts.storage_grace_days} days | Grace period |")
        if facts.long_term_days and facts.long_term_surcharge_percent:
            lines.append(f"| Long-term inventory | {facts.long_term_surcharge_percent:g}% over the standard rate from day {facts.long_term_days + 1} | Applies to stock with no movement for more than {facts.long_term_days} days |")
    else:
        lines.append(f"| Standard storage | {tbc(owner, 'the storage rate was not found in the knowledge base')} | Owner |")
    lines.append(f"| Cost per pallet or per SKU | {tbc(owner, 'not in the knowledge base')} | Owner |")
    if not concise:
        lines += ["", "Storage is charged on the volume occupied, not on the number of units."]
    lines += ["", "### Volume discount tiers", "| Monthly volume (orders) | Discount on storage |", "|---|---|", *rows]
    lines += ["", "### Service level", *sla_paragraph(brief, facts, concise)]
    items = open_items(brief, owner, [f"Capacity, onboarding time and the cost per pallet or SKU are for {owner} to confirm.", f"Discounts per tier are set by {COMMERCIAL_DIRECTOR}."])
    lines += ["", "### Open items to confirm", *[f"- {item}" for item in items]]
    return Draft("\n".join(lines), derived_numbers=derived)


def draft_lastmile(brief: dict, facts: Facts, concise: bool) -> Draft:
    owner, aspects, client = DEPARTMENTS["lastmile"]["owner"], brief["key_aspects"], brief["client"]
    figures = aspects.get("stated_figures", {})
    currency = client["currency"] or "the client's currency"
    rows, derived = tier_rows(brief["monthly_volume"], "shipments", COMMERCIAL_DIRECTOR)
    lines = header(brief, "Last-mile delivery proposal")
    lines += ["### Scope", scope_line(brief, "last mile")]
    lines += ["", "### Carriers and coverage"]
    destination = figures.get("destinations")
    if facts.carriers:
        place = "the United States (Los Angeles)" if client["country"] == "US" else "Spain (Zaragoza)"
        lines.append(f"For {'delivery ' + destination if destination else place}, TrackFlow can use: " + ", ".join(facts.carriers) + (" and two local last-mile carriers in the Zaragoza metropolitan area." if client["country"] == "ES" else "."))
        for name in facts.carriers:
            note = facts.carrier_notes.get(name)
            foreign = r"Aragón|Zaragoza" if client["country"] == "US" else r"Los Angeles|California"
            if note and not concise and not re.search(foreign, note):  # a note about the other country's network is not for this client
                note = re.sub(r"best rate for", "suited to", re.sub(r"most competitive rates for", "suited to", note))
                lines.append(f"- {name}: {note}.")
    else:
        lines.append(f"- {tbc(owner, 'available carriers by destination were not found in the knowledge base')}.")
    if figures.get("carrier_selection_requested"):
        lines.append("The client has no carrier relationships and asks TrackFlow to choose carriers: the system selects the carrier of each shipment by destination, weight, product type and urgency.")
    elif not concise:
        lines.append("The carrier of each shipment is selected by the system by destination, weight, product type and urgency.")
    lines += ["", "### Delivery times"]
    if facts.standard_shipping:
        lines.append(f"- Standard shipping: {facts.standard_shipping}.")
    if facts.express_shipping and not concise:
        express = re.sub(r" and the Zaragoza-Madrid corridor", "", facts.express_shipping) if client["country"] == "US" else re.sub(r"the Los Angeles metropolitan area and ", "", facts.express_shipping)
        lines.append(f"- Express shipping: {express}.")
    if not facts.standard_shipping:
        lines.append(f"- {tbc(owner, 'delivery times were not found in the knowledge base')}.")
    lines += ["", f"### Pricing ({currency})", f"| Item | Price ({currency}) | Basis |", "|---|---|---|"]
    lines.append(f"| Cost per shipment | {tbc(owner, 'final cost offered to the client')} | Owner: only the final cost is quoted, never the carriers' own rates |")
    lines += ["", "### Volume discount tiers", "| Monthly volume (shipments) | Discount on cost per shipment |", "|---|---|", *rows]
    lines += ["", "### Service level", *sla_paragraph(brief, facts, concise)]
    items = open_items(brief, owner, [f"The cost per shipment is for {owner} to confirm.", f"Discounts per tier are set by {COMMERCIAL_DIRECTOR}."])
    lines += ["", "### Open items to confirm", *[f"- {item}" for item in items]]
    return Draft("\n".join(lines), derived_numbers=derived)


def draft_reverse(brief: dict, facts: Facts, concise: bool) -> Draft:
    owner, aspects, client = DEPARTMENTS["reverse"]["owner"], brief["key_aspects"], brief["client"]
    figures = aspects.get("stated_figures", {})
    currency = client["currency"] or "the client's currency"
    rows, derived = tier_rows(brief["monthly_volume"], "returns", COMMERCIAL_DIRECTOR)
    steps = figures.get("process_steps_requested", [])
    lines = header(brief, "Returns management proposal")
    lines += ["### Scope", scope_line(brief, "returns")]
    if steps:
        lines.append(f"The client asks for: {', '.join(steps)}.")
    lines += ["", "### Returns process"]
    lines.append("1. The end customer requests the return through the brand's portal, and TrackFlow generates the return label when the reason is within the rules the client configures.")
    lines.append("2. At the warehouse an operative inspects each product and classifies it as fit for resale, fit for reconditioning, or not fit (disposal).")
    if "refurbishing" in steps or "restock" in steps:
        lines.append("TrackFlow executes refurbishing and restock according to the rules each client defines; it does not recondition or resell product on its own account.")
    if facts.return_window_days:
        lines.append(f"The standard return window is {facts.return_window_days} days from delivery, unless the client's contract sets another one.")
    if not concise:
        lines.append("Return shipping is paid by the client brand, unless the cause is a TrackFlow error (damaged in transit or wrong delivery).")
        lines.append("International returns (bought in one country, returned in another) are not part of the automated process and are handled manually by the Returns team.")
    lines += ["", "### Turnaround time", f"- Returns turnaround: {tbc(owner, 'it will never be shorter than 48 hours')}."]
    lines += ["", f"### Pricing ({currency})", f"| Item | Price ({currency}) | Basis |", "|---|---|---|"]
    lines.append(f"| Returns processing cost per unit | {tbc(owner, 'not in the knowledge base')} | Owner |")
    lines += ["", "### Volume discount tiers", "| Monthly volume (returns) | Discount on returns processing |", "|---|---|", *rows]
    lines += ["", "### Service level", *sla_paragraph(brief, facts, concise)]
    items = open_items(brief, owner, [f"The returns processing cost and the turnaround time are for {owner} to confirm.", f"Discounts per tier are set by {COMMERCIAL_DIRECTOR}."])
    lines += ["", "### Open items to confirm", *[f"- {item}" for item in items]]
    return Draft("\n".join(lines), derived_numbers=derived)


GENERATORS = {"warehouse": draft_warehouse, "lastmile": draft_lastmile, "reverse": draft_reverse}

SYSTEM = (
    "You are the {name} proposal writer at TrackFlow, a logistics company. Write ONLY the {name} section of a pricing proposal for the client below, in Markdown, in English, in "
    "short plain sentences. The section must contain: scope, what the department contributes ({contributes}), a pricing table in {currency}, a volume-based discount tier table "
    "(volume column and discount column, at least two tiers), the on-time delivery SLA percentage TrackFlow commits to, and the open items. Rules: quote prices ONLY in {currency}; state "
    "the on-time delivery SLA percentage from the knowledge base text; never promise returns processing in under 48 hours; never mention a rate negotiated with a specific carrier; "
    "use only figures from the client data or the knowledge base text, and write 'To be confirmed by {owner}' for any price or figure you do not have. "
    'Return JSON: {{"content": "<the Markdown section>"}}.'
)


def generate(brief: dict, facts: Facts, feedback: dict | None = None, previous: str | None = None) -> Draft:
    """One draft. `feedback` is the evaluators' structured feedback of the previous attempt (`{"text": ..., "fixes": [...]}`); a readability fix makes the rules draft concise."""
    human = [fix["instruction"] for fix in (feedback or {}).get("fixes", []) if fix.get("kind") == "human"]
    if human:  # the department owner asked for changes at the approval step: the rules draft lists the requests among its open items, a model draft receives them as feedback
        brief = {**brief, "reviewer_requests": human}
    department_id = brief["department_id"]
    concise = bool(feedback) and any(fix.get("kind") == "readability" for fix in feedback.get("fixes", []))
    spec = DEPARTMENTS[department_id]
    prompt = (
        f"Client data: {brief['client']}; monthly volume: {brief['monthly_volume']}; deadline: {brief.get('deadline')}.\n"
        f"Summary from the intake: {brief['summary']}\nKey aspects for your department: {brief['key_aspects']}\n"
        f"Knowledge base text:\n" + "\n".join(f"- {text}" for text in facts.texts)
    )
    if feedback and previous:
        prompt += f"\n\nYour previous draft was rejected. Fix exactly this and keep the rest:\n{feedback['text']}\n\nPrevious draft:\n{previous}"
    answer = llm.ask_json(
        SYSTEM.format(name=spec["name"], contributes=spec["contributes"], currency=brief["client"]["currency"] or "the client's currency", owner=spec["owner"]),
        prompt,
        required=("content",),
        max_tokens=1500,
    )
    content = answer.get("content") if answer else None
    if isinstance(content, str) and len(content.split()) >= MIN_WORDS:
        return Draft(content.strip(), engine="llm")
    return GENERATORS[department_id](brief, facts, concise)
