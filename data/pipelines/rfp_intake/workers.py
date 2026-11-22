"""The worker agents: one per department, each reading only its own subtask (shared metadata + its extracts) and writing that department's `key_aspects`.

`key_aspects` is what Sales reads to know what to ask whom:

    summary         one sentence: what this RFP asks of the department
    requirements    what the client asks that touches this department (quoted from the RFP)
    stated_figures  numbers the RFP states (SKUs, monthly volume...), each one found in the document
    asks_of_owner   what the owner has to provide for the proposal (CONTEXT 2.1: what the department contributes)
    open_questions  what the RFP does NOT say and the department needs. A missing volume is a question, never a guess.
    constraints     guidelines of CONTEXT section 5 that bind this department's answer

Rules build all of it. When the model is configured it may reword requirements, asks and questions, but ONLY if every number in its text is a number of the
subtask (`grounded_numbers`); otherwise its answer is dropped and the rules' version stands. The three workers share a skeleton and differ in what they look for.
"""

from __future__ import annotations

import re

from . import llm, metadata
from .config import DEPARTMENTS

MAX_ITEMS = 8
MAX_ITEM_CHARS = 300


def clean_line(text: str) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"^[\s\-*•]+", "", text)).strip()
    return text if len(text) <= MAX_ITEM_CHARS else text[: MAX_ITEM_CHARS - 1].rstrip() + "…"


def numbers_in(text: str) -> set[str]:
    return {re.sub(r"[.,\s]", "", number) for number in re.findall(r"\d[\d.,]*", text or "") if re.sub(r"\D", "", number)}


def grounded_numbers(items: list[str], task: dict) -> bool:
    """True when every number the model wrote appears in the subtask (shared metadata or extracts). Dates and years count as numbers too."""
    allowed = numbers_in(" ".join(task["extracts"])) | numbers_in(str(task["shared"]))
    for item in items:
        if numbers_in(item) - allowed:
            return False
    return True


def money(currency: str | None) -> str:
    return currency or "the client's currency (country not confirmed)"


def volume_text(shared: dict) -> str | None:
    return f"{shared['monthly_volume']:,} orders per month" if shared.get("monthly_volume") else None


def find_figure(extracts: list[str], pattern: str) -> str | None:
    match = re.search(pattern, " ".join(extracts), re.IGNORECASE)
    return match.group(0).strip() if match else None


def requirements_from(task: dict) -> list[str]:
    return [clean_line(block) for block in task["extracts"]][:MAX_ITEMS]


# ------------------------------------------------------------------------------------------------ rules per department
def warehouse_rules(task: dict) -> dict:
    shared, extracts = task["shared"], task["extracts"]
    text = " ".join(extracts)
    figures, asks, open_questions = {}, [], []
    skus = re.search(r"(\d{1,3}(?:[.,]\d{3})+|\d+)\s*SKUs?\b", text, re.IGNORECASE)
    pallets = re.search(r"(\d{1,3}(?:[.,]\d{3})+|\d+)\s*pallets?\b", text, re.IGNORECASE)
    if skus:
        figures["skus"] = metadata.parse_number(skus.group(1))
    if pallets:
        figures["pallets"] = metadata.parse_number(pallets.group(1))
    if shared.get("monthly_volume"):
        figures["monthly_orders"] = shared["monthly_volume"]
    place = re.search(r"(?:cercano a|cerca de|near|close to|en|in)\s+(Zaragoza|Madrid|Barcelona|Valencia|Los Angeles|California)\b", text)
    if place:
        figures["preferred_location"] = place.group(1)
    integration = re.search(r"\bERP\b|\bAPI\b|integraci\w+|integration", text, re.IGNORECASE)

    stated = "; ".join(filter(None, [f"{figures['skus']:,} SKUs" if "skus" in figures else None, f"{figures['pallets']:,} pallets" if "pallets" in figures else None, volume_text(shared)]))
    where = f" in or near {figures['preferred_location']}" if "preferred_location" in figures else ""
    asks.append(f"Storage capacity{where}: can the warehouse hold what the client describes ({stated})? State the capacity you can commit, in pallets or SKUs." if stated else
                f"Storage capacity{where}: the RFP gives no SKU, pallet or volume figure. State the capacity you can commit, in pallets or SKUs, so Sales can size the offer.")
    asks.append(f"Cost per pallet or per SKU, quoted in {money(shared.get('currency'))}.")
    asks.append("Onboarding time: how long until the client's stock can be received and shipped" + (f", given the deadline ({shared['deadline']})." if shared.get("deadline") else "."))
    if integration:
        asks.append("Confirm that the client's systems can be integrated (the RFP mentions an ERP or API connection) and what that takes.")
    if not shared.get("monthly_volume"):
        open_questions.append("Monthly order volume is not stated: ask the client before sizing capacity or onboarding throughput.")
    if "skus" not in figures and "pallets" not in figures:
        open_questions.append("The RFP does not state how many SKUs or pallets will be stored.")
    summary = f"{shared.get('client_name') or 'The client'} needs warehousing" + (f" ({stated})" if stated else "") + where + "."
    constraints = [f"Prices in {shared['currency']}."] if shared.get("currency") else []
    return {"summary": summary, "stated_figures": figures, "asks_of_owner": asks, "open_questions": open_questions, "constraints": constraints}


def lastmile_rules(task: dict) -> dict:
    shared, extracts = task["shared"], task["extracts"]
    text = " ".join(extracts)
    figures, asks, open_questions = {}, [], []
    if shared.get("monthly_volume"):
        figures["monthly_orders"] = shared["monthly_volume"]
    coverage = find_figure(extracts, r"across the (?:US|United States|USA)|nationwide|coast to coast|en toda (?:Espa[nñ]a|la pen[ií]nsula)|Spain and Portugal|Espa[nñ]a y Portugal")
    if coverage:
        figures["destinations"] = coverage
    needs_selection = bool(re.search(r"carrier selection|select(?:ing)? (?:the )?carriers?|no (?:carrier )?relationships|don'?t have relationships|selecci[oó]n de transportista", text, re.IGNORECASE))
    if needs_selection:
        figures["carrier_selection_requested"] = True
    asks.append(f"Cost per shipment, quoted in {money(shared.get('currency'))}" + (f", for about {volume_text(shared).replace('orders', 'shipments')}." if volume_text(shared) else ". The RFP gives no volume: say which volume tier your price assumes."))
    asks.append("Available carriers by destination" + (f" ({coverage})." if coverage else ": the RFP does not detail the destinations, so list coverage by region."))
    asks.append("Delivery SLA: the on-time delivery percentage TrackFlow can commit to (every proposal must state it).")
    if needs_selection:
        asks.append("Carrier selection support: the client has no carrier relationships and asks TrackFlow to choose carriers for them.")
    if not shared.get("monthly_volume"):
        open_questions.append("Monthly shipment volume is not stated: ask the client before quoting a cost per shipment.")
    open_questions.append("Parcel sizes and weights are not stated, and they change the cost per shipment.")
    if not coverage:
        open_questions.append("Delivery destinations (regions or countries) are not detailed.")
    summary = f"{shared.get('client_name') or 'The client'} needs last-mile delivery" + (f" for about {volume_text(shared).replace('orders', 'shipments')}" if volume_text(shared) else "") + (", with carrier selection" if needs_selection else "") + "."
    constraints = ["Do not disclose negotiated rates with specific carriers: quote only the final cost offered to the client."]
    if shared.get("currency"):
        constraints.insert(0, f"Prices in {shared['currency']}.")
    return {"summary": summary, "stated_figures": figures, "asks_of_owner": asks, "open_questions": open_questions, "constraints": constraints}


def reverse_rules(task: dict) -> dict:
    shared, extracts = task["shared"], task["extracts"]
    text = " ".join(extracts)
    figures, asks, open_questions = {}, [], []
    steps = [label for label, pattern in (("inspection", r"inspecci\w+|inspection"), ("refurbishing", r"reacondicion\w+|refurbish\w*"), ("restock", r"restock|reposici\w+")) if re.search(pattern, text, re.IGNORECASE)]
    if steps:
        figures["process_steps_requested"] = steps
    asks.append(f"Returns processing cost per unit, quoted in {money(shared.get('currency'))}" + (f", covering {', '.join(steps)}." if steps else "."))
    asks.append("Returns turnaround time: how long from receiving a return to restocking or closing it. It cannot be promised in under 48 hours.")
    if steps:
        asks.append(f"Confirm the team can perform what the client lists: {', '.join(steps)}.")
    open_questions.append("The expected return rate (returns per month or % of orders) is not stated: it sizes the team and the cost.")
    if not shared.get("monthly_volume"):
        open_questions.append("Monthly order volume is not stated, so the volume of returns cannot be estimated.")
    summary = f"{shared.get('client_name') or 'The client'} needs returns management" + (f" ({', '.join(steps)})" if steps else "") + "."
    constraints = ["Returns processing cannot be promised in under 48 hours."]
    if shared.get("currency"):
        constraints.insert(0, f"Prices in {shared['currency']}.")
    return {"summary": summary, "stated_figures": figures, "asks_of_owner": asks, "open_questions": open_questions, "constraints": constraints}


RULES = {"warehouse": warehouse_rules, "lastmile": lastmile_rules, "reverse": reverse_rules}

SYSTEM = (
    "You are the {name} analyst at TrackFlow reading ONE client's logistics request. Your department contributes: {contributes}. "
    "From the extracts and client data below, write what the client asks of your department, what the department head ({owner}) has to provide for the proposal, "
    "and what the request does NOT say. Use only numbers that appear in the extracts or the client data; never estimate a volume, a price or a date. "
    'Return JSON: {{"requirements": [..], "asks_of_owner": [..], "open_questions": [..]}}, each a short list of plain sentences.'
)


def _strings(value) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return [clean_line(item) for item in value if item.strip()][:MAX_ITEMS]


def run_worker(task: dict) -> dict:
    """The department's section: `{department_id, owner, department, engine, key_aspects}`."""
    department_id = task["department_id"]
    spec = DEPARTMENTS[department_id]
    built = RULES[department_id](task)
    key_aspects = {
        "summary": built["summary"],
        "requirements": requirements_from(task),
        "stated_figures": built["stated_figures"],
        "asks_of_owner": built["asks_of_owner"],
        "open_questions": built["open_questions"],
        "constraints": built["constraints"],
        "contributes": spec["contributes"],
    }
    engine = "rules"
    answer = llm.ask_json(
        SYSTEM.format(name=spec["name"], contributes=spec["contributes"], owner=spec["owner"]),
        f"Client data: {task['shared']}\n\nExtracts of the request for your department:\n{llm.wrap_document(chr(10).join(task['extracts']))}",
        required=("requirements", "asks_of_owner", "open_questions"),
    )
    if answer:
        requirements, asks, questions = _strings(answer["requirements"]), _strings(answer["asks_of_owner"]), _strings(answer["open_questions"])
        if requirements is not None and asks and questions is not None and grounded_numbers(requirements + asks + questions, task):
            key_aspects["requirements"] = requirements or key_aspects["requirements"]
            key_aspects["asks_of_owner"] = asks
            # what the rules know is missing always stays: the model may add questions, not remove them
            key_aspects["open_questions"] = list(dict.fromkeys(built["open_questions"] + questions))[:MAX_ITEMS]
            engine = "llm"
    return {"department_id": department_id, "department": spec["name"], "owner": spec["owner"], "engine": engine, "key_aspects": key_aspects}
