"""Arbitration of Part 3: detectable contradictions in the STRUCTURED state, resolved by the fixed arbiter of CONTEXT-company.md section 7. Not agents negotiating, not an LLM voting.

    trigger id           fires when                                                                           arbiter (fixed)                                  resolution rule
    volume-vs-capacity   the warehouse's committed capacity cannot support the monthly volume that last      Miguel Torres (Commercial Director)             cap the proposal volume to the warehouse capacity;
                         mile assumes (both are structured commitments, not free text)                                                                         last mile revises its quoted volume and cost downward
    returns-sla-breach   reverse (or any section) promises returns turnaround under 48 hours (GL-3)             Sofía Ramos rejects her section; if another     force request_changes on every section that states it;
                                                                                                                 section still embeds it, Miguel Torres          no final document until compliant
    currency-mismatch    two sections quote different currencies, or a section quotes a currency other than   Miguel Torres                                   rewrite the offending sections to the country currency;
                         the one of `client_country` (US: USD, Spain: EUR)                                                                                     rejected if unresolved after the iteration limit

`detect` only looks at data and returns `Conflict`s; `resolve` turns each into the actions the arbiter's rule dictates. Applying them (rewriting a section, sending it back for approval) is the
coordinator's job (`approval_runner.py`), and every step is traced under the name of the arbiter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import rules
from .config import COMMERCIAL_DIRECTOR, COUNTRY_CURRENCY, DEPARTMENTS

VOLUME_VS_CAPACITY = "volume-vs-capacity"
RETURNS_SLA_BREACH = "returns-sla-breach"
CURRENCY_MISMATCH = "currency-mismatch"
TRIGGER_IDS = (VOLUME_VS_CAPACITY, RETURNS_SLA_BREACH, CURRENCY_MISMATCH)

RULES = {
    VOLUME_VS_CAPACITY: "Cap the proposal volume to the warehouse capacity; last mile must revise its quoted volume and cost downward.",
    RETURNS_SLA_BREACH: "Force request_changes on every section that states a returns turnaround under 48 hours; no final document until compliant.",
    CURRENCY_MISMATCH: "Rewrite the offending sections to the currency of the client's country; reject them if unresolved after the iteration limit.",
}


@dataclass
class Conflict:
    trigger_id: str
    arbiter: str
    departments: list[str]  # the sections the resolution acts on
    evidence: list[str]
    rule: str = ""
    arbiters: dict[str, str] = field(default_factory=dict)  # per section, when the arbiter depends on the section (returns-sla-breach)
    cap: int | None = None  # volume-vs-capacity: the volume the proposal is capped to

    def as_dict(self) -> dict:
        return {"trigger_id": self.trigger_id, "arbiter": self.arbiter, "departments": self.departments, "evidence": self.evidence, "rule": self.rule, "cap": self.cap}


def currencies_in(content: str) -> set[str]:
    found = set()
    if rules.USD_TOKEN.search(content):
        found.add("USD")
    if rules.EUR_TOKEN.search(content):
        found.add("EUR")
    return found


def detect(sections: list[dict], client_country: str | None, handoff_volume: int | None) -> list[Conflict]:
    """The conflicts present in the sections: `[{department_id, content, commitments}]`. Deterministic and side-effect free."""
    by_department = {section["department_id"]: section for section in sections}
    conflicts: list[Conflict] = []

    # volume-vs-capacity: both numbers are structured commitments (the owners' own statements), compared as orders per month
    warehouse, lastmile = by_department.get("warehouse"), by_department.get("lastmile")
    if warehouse and lastmile:
        capacity = (warehouse.get("commitments") or {}).get("capacity_orders_per_month")
        assumed = (lastmile.get("commitments") or {}).get("assumed_monthly_volume") or handoff_volume
        if capacity and assumed and capacity < assumed:
            conflicts.append(
                Conflict(
                    VOLUME_VS_CAPACITY,
                    COMMERCIAL_DIRECTOR,
                    ["lastmile"],
                    [f"Warehouse commits capacity for {capacity:,} orders per month; last mile assumes {assumed:,} orders per month."],
                    RULES[VOLUME_VS_CAPACITY],
                    cap=capacity,
                )
            )

    # returns-sla-breach: any section that promises returns processing in under 48 hours
    offenders = {section["department_id"]: rules.returns_promises_under_48h(section["content"]) for section in sections}
    offenders = {department_id: promises for department_id, promises in offenders.items() if promises}
    if offenders:
        arbiters = {department_id: (DEPARTMENTS["reverse"]["owner"] if department_id == "reverse" else COMMERCIAL_DIRECTOR) for department_id in offenders}
        conflicts.append(
            Conflict(
                RETURNS_SLA_BREACH,
                DEPARTMENTS["reverse"]["owner"] if "reverse" in offenders else COMMERCIAL_DIRECTOR,
                list(offenders),
                [f"{department_id}: {promises[0][:140]}" for department_id, promises in offenders.items()],
                RULES[RETURNS_SLA_BREACH],
                arbiters=arbiters,
            )
        )

    # currency-mismatch: a section in a currency that is not the country's, or sections in different currencies
    expected = COUNTRY_CURRENCY.get(client_country or "")
    quoted = {section["department_id"]: currencies_in(section["content"]) for section in sections}
    if expected:
        wrong = [department_id for department_id, currencies in quoted.items() if currencies - {expected}]
    else:
        distinct = set().union(*quoted.values()) if quoted else set()
        wrong = list(quoted) if len(distinct) > 1 else []
    if wrong:
        conflicts.append(
            Conflict(
                CURRENCY_MISMATCH,
                COMMERCIAL_DIRECTOR,
                wrong,
                [f"{department_id} quotes {', '.join(sorted(quoted[department_id]))}; the client's country ({client_country or 'unknown'}) requires {expected or 'a single currency'}." for department_id in wrong],
                RULES[CURRENCY_MISMATCH],
            )
        )
    return conflicts


def resolve(conflict: Conflict) -> list[dict]:
    """The actions the arbiter's rule dictates, one per affected section: `{department_id, decision, arbiter, feedback, fixes, notes, commitments}`."""
    actions = []
    for department_id in conflict.departments:
        arbiter = conflict.arbiters.get(department_id, conflict.arbiter)
        action = {"department_id": department_id, "arbiter": arbiter, "trigger_id": conflict.trigger_id, "notes": [], "commitments": {}}
        if conflict.trigger_id == VOLUME_VS_CAPACITY:
            action |= {
                "decision": "request_changes",
                "feedback": f"{COMMERCIAL_DIRECTOR} caps the proposal volume to {conflict.cap:,} orders per month, the warehouse's committed capacity. Revise the quoted volume and cost downward.",
                "fixes": [{"kind": "arbitration", "id": VOLUME_VS_CAPACITY, "instruction": f"Quote for at most {conflict.cap:,} orders per month (warehouse capacity)."}],
                "notes": [f"The proposal volume is capped at {conflict.cap:,} orders per month, the warehouse's committed capacity (decision of {COMMERCIAL_DIRECTOR}, Commercial Director)."],
                "commitments": {"assumed_monthly_volume": conflict.cap},
                "volume": conflict.cap,
            }
        elif conflict.trigger_id == RETURNS_SLA_BREACH:
            action |= {
                "decision": "reject" if department_id == "reverse" else "request_changes",
                "feedback": f"{arbiter}: returns processing cannot be promised in under 48 hours (guideline 3 of the company). Remove the promise.",
                "fixes": [{"kind": "compliance", "id": "GL-3", "instruction": "Never promise returns processing in under 48 hours."}],
            }
        else:
            action |= {
                "decision": "request_changes",
                "feedback": f"{COMMERCIAL_DIRECTOR}: quote prices only in the currency of the client's country.",
                "fixes": [{"kind": "compliance", "id": "GL-1", "instruction": "Quote prices in USD for US operations and EUR for Spain operations."}],
            }
        actions.append(action)
    return actions
