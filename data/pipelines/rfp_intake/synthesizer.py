"""The synthesizer agent: turns the workers' sections into what Sales reads, and into the handoff Part 2 starts from.

Everything structured (who to ask, what to ask, what is missing) is assembled by code from the workers' output, so it is always complete and consistent. The model, when
configured, only writes the opening paragraph, and it is accepted only if every number in it is a number of the ticket.

The handoff (`build_handoff`) is the contract with Part 2 (docs/rfp/handoff-contract.md): `ticket_id` + the synthesizer payload, so Part 2 never parses the PDF again.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import llm
from .config import COMMERCIAL_DIRECTOR, COUNTRY_CURRENCY, DEPARTMENTS, GUIDELINES, HANDOFF_SCHEMA_VERSION
from .workers import numbers_in

SERVICE_LABEL = {"warehousing": "warehousing", "last_mile": "last-mile delivery", "reverse_logistics": "returns (reverse logistics)"}
COUNTRY_NAME = {"US": "United States", "ES": "Spain"}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def headline(meta: dict, sections: list[dict]) -> str:
    """The opening of the summary: client, country and currency, services, the departments that are (and are not) needed, volume and deadline."""
    client = meta.get("client_name") or "An unnamed client"
    country = COUNTRY_NAME.get(meta.get("client_country") or "")
    where = f" ({country}, quoted in {meta['currency']})" if country and meta.get("currency") else " (country not confirmed)"
    services = _join([SERVICE_LABEL[service] for service in meta["services_requested"]]) or "no clear service"
    text = f"{client}{where} asks for {services}."
    left_out = [DEPARTMENTS_BY_SERVICE[service] for service in meta.get("services_excluded", [])]
    if left_out:
        text += f" It does not need {_join(left_out)} (the RFP says it handles that itself), so that department is not involved."
    text += f" Departments involved: {_join([DEPARTMENTS[section['department_id']]['name'] for section in sections]) or 'none'}."
    text += f" Monthly volume: {meta['monthly_volume']:,} orders." if meta.get("monthly_volume") else " Monthly volume: not stated."
    if meta.get("deadline") and meta.get("deadline_kind") == "go_live":
        text += f" Go-live target: {meta['deadline']}; no proposal due date is given."
    elif meta.get("deadline"):
        text += f" Deadline: {meta['deadline']}."
    return text


DEPARTMENTS_BY_SERVICE = {spec["service"]: spec["name"] for spec in DEPARTMENTS.values()}


def summary_text(meta: dict, sections: list[dict], readability: dict) -> str:
    lines = [headline(meta, sections), "", "Who to ask, and for what:"]
    for section in sections:
        aspects = section["key_aspects"]
        lines.append(f"- {section['owner']} ({section['department']}): " + " ".join(aspects["asks_of_owner"]))
    open_questions = list(meta.get("open_questions", []))
    if any("Monthly" in question and "volume" in question for section in sections for question in section["key_aspects"]["open_questions"]):
        open_questions = [question for question in open_questions if not question.startswith("The monthly order volume")]  # the departments already ask it, in their own words
    for section in sections:
        open_questions.extend(f"[{section['department']}] {question}" for question in section["key_aspects"]["open_questions"])
    if open_questions:
        lines.extend(["", "Open questions (the RFP does not say):", *[f"- {question}" for question in open_questions]])
    if readability:
        lines.extend(["", f"Processing cost: {readability.get('cost_band', 'unknown')} (about {readability.get('estimated_tokens', '?')} tokens of text)."])
    return "\n".join(lines)


def polish_headline(meta: dict, sections: list[dict], draft: str) -> tuple[str, str]:
    """(headline, engine). The model may write the opening paragraph; its numbers must all be ticket numbers or it is discarded."""
    answer = llm.ask_json(
        "You are the synthesizer of TrackFlow's RFP intake. Rewrite this opening paragraph for the Sales team in two or three plain sentences. Keep every fact, add none, "
        'and use only the numbers it contains. Return JSON: {"headline": "..."}.',
        draft,
        required=("headline",),
        max_tokens=300,
    )
    text = answer.get("headline") if answer else None
    if isinstance(text, str) and 20 <= len(text.strip()) <= 800 and not (numbers_in(text) - numbers_in(draft)):
        return text.strip(), "llm"
    return draft, "rules"


def synthesize(meta: dict, sections: list[dict], readability: dict) -> dict:
    """The Sales-facing result: `summary`, `workstreams` (one per active department) and `open_questions` (client-level plus each department's)."""
    draft = headline(meta, sections)
    opening, engine = polish_headline(meta, sections, draft)
    summary = summary_text(meta, sections, readability).replace(draft, opening, 1)
    workstreams = [
        {
            "department_id": section["department_id"],
            "department": section["department"],
            "owner": section["owner"],
            "contributes": DEPARTMENTS[section["department_id"]]["contributes"],
            "ask": section["key_aspects"]["asks_of_owner"],
            "open_questions": section["key_aspects"]["open_questions"],
            "constraints": section["key_aspects"]["constraints"],
        }
        for section in sections
    ]
    return {"summary": summary, "workstreams": workstreams, "engine": engine}


def build_handoff(ticket_id: str, rfp_id: str, meta: dict, readability: dict, sections: list[dict], synthesis: dict) -> dict:
    """The contract with Part 2 (see docs/rfp/handoff-contract.md): everything it needs, nothing it has to re-derive from the PDF."""
    return {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "ticket_id": ticket_id,
        "rfp_id": rfp_id,
        "status_when_handed_off": "intake_complete",
        "next_stage": "drafting",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "client": {"name": meta.get("client_name"), "country": meta.get("client_country"), "currency": meta.get("currency") or COUNTRY_CURRENCY.get(meta.get("client_country") or ""), "contact": meta.get("client_contact") or {}},
        "services_requested": meta["services_requested"],
        "departments_needed": meta["departments_needed"],
        "services_excluded": meta.get("services_excluded", []),
        "monthly_volume": meta.get("monthly_volume"),
        "deadline": meta.get("deadline"),
        "budget_range": meta.get("budget_range"),
        "cost_band": readability.get("cost_band"),
        "arbiter": COMMERCIAL_DIRECTOR,
        "guidelines": list(GUIDELINES),
        "summary": synthesis["summary"],
        "open_questions": meta.get("open_questions", []),
        "workstreams": [
            {"department_id": section["department_id"], "owner": section["owner"], "key_aspects": section["key_aspects"]} for section in sections
        ],
    }
