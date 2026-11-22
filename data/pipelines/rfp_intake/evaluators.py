"""The evaluator agents of Part 2: three independent checks over a department's draft, then one consolidated, structured `EvaluationResult`.

    readability   py-readability-metrics on the prose of the draft (tables and Markdown marks removed): Flesch-Kincaid grade within the limit, enough words, no endless sentences
    relevance     the draft answers what the RFP asks of this department: the aspects the department contributes (CONTEXT 2.1), the client's own figures and requests (from the Part 1
                  `key_aspects`), the open questions flagged, and NO figure that is in neither the RFP nor the knowledge base
    compliance    the five guidelines of CONTEXT section 5 (`rules.py`), each one a test on the text, not an opinion

They are separate functions so the graph runs them in parallel (one node each). All three are deterministic: the compliance check is exactly what CONTEXT-company.md lists.

EvaluationResult (stored with the section):
    section_id, department_id, iteration
    readability: {pass, score, details}
    relevance:   {pass, missing_aspects[], unsupported_figures[]}
    compliance:  {pass, rule_ids[] (the violated ones), violations[], checked_rule_ids[]}
    overall_pass, feedback_for_generator (concrete, actionable), fixes[] (the same, structured)
"""

from __future__ import annotations

import json
import re

from . import reading_cost, rules
from .config import GUIDELINES
from .knowledge import Facts
from .workers import numbers_in

MAX_GRADE = 14.0  # Flesch-Kincaid grade of a business proposal: readable by an operations manager, not a lawyer
MAX_SENTENCE_WORDS = 40
MIN_WORDS = 60

# What each department contributes (CONTEXT 2.1): an aspect is covered when the draft talks about it, even if the figure is still "to be confirmed".
REQUIRED = {
    "warehouse": [
        ("storage capacity", r"capacity|capacidad"),
        ("cost per pallet, SKU or cubic meter", r"per pallet|per sku|per cubic meter|storage rate|standard storage"),
        ("onboarding time", r"onboarding"),
    ],
    "lastmile": [
        ("cost per shipment", r"cost per shipment"),
        ("available carriers by destination", r"carriers?"),
        ("delivery SLA", r"on-time"),
    ],
    "reverse": [
        ("returns processing cost", r"returns? processing cost|cost per unit"),
        ("returns turnaround time", r"turnaround"),
    ],
}


def prose(text: str) -> str:
    """The draft as sentences: tables, headings and Markdown marks removed (the readability library reads prose)."""
    lines = [line for line in text.splitlines() if not line.strip().startswith("|")]
    cleaned = "\n".join(re.sub(r"^#+\s*", "", re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", line)) for line in lines)
    return re.sub(r"[*_`]", "", cleaned)


def evaluate_readability(content: str) -> dict:
    text = prose(content)
    metrics = reading_cost.analyze(text)
    words = metrics["word_count"]
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    longest = max((len(s.split()) for s in sentences), default=0)
    grade = metrics["flesch_kincaid_grade"]
    problems = []
    if words < MIN_WORDS:
        problems.append(f"too short ({words} words; at least {MIN_WORDS})")
    if grade is not None and grade > MAX_GRADE:
        problems.append(f"reading level too high (Flesch-Kincaid grade {grade:.1f}; the limit is {MAX_GRADE:g})")
    if longest > MAX_SENTENCE_WORDS:
        problems.append(f"a sentence has {longest} words (the limit is {MAX_SENTENCE_WORDS})")
    return {
        "pass": not problems,
        "score": grade,
        "details": {
            "flesch_kincaid_grade": grade,
            "gunning_fog": metrics["gunning_fog"],
            "flesch_reading_ease": metrics["flesch_reading_ease"],
            "ari": metrics["ari"],
            "word_count": words,
            "longest_sentence_words": longest,
            "limit_grade": MAX_GRADE,
            "problems": problems,
            "note": metrics["note"],
        },
    }


def allowed_numbers(brief: dict, facts: Facts, derived: list[str]) -> set[str]:
    allowed = numbers_in(json.dumps(brief, default=str)) | facts.numbers() | set(derived) | numbers_in(" ".join(GUIDELINES))
    return allowed | {"1", "2", "3", "24"}  # list markers and "Tier 1..3" are not figures


def evaluate_relevance(content: str, brief: dict, facts: Facts, derived: list[str] | None = None, human_authored: bool = False) -> dict:
    lowered = content.lower()
    missing: list[str] = []
    for aspect, pattern in REQUIRED[brief["department_id"]]:
        if not re.search(pattern, lowered):
            missing.append(aspect)
    aspects, figures = brief["key_aspects"], brief["key_aspects"].get("stated_figures", {})
    if figures.get("skus") and not (numbers_in(str(figures["skus"])) & numbers_in(content)):
        missing.append(f"the client's own figure: {figures['skus']:,} SKUs")
    if brief["monthly_volume"] and not (numbers_in(str(brief["monthly_volume"])) & numbers_in(content)):
        missing.append(f"the client's monthly volume ({brief['monthly_volume']:,} orders)")
    if figures.get("carrier_selection_requested") and "select" not in lowered:
        missing.append("carrier selection (the client asks TrackFlow to choose carriers)")
    for step in figures.get("process_steps_requested", []):
        if step.split()[0].lower()[:6] not in lowered:
            missing.append(f"requested returns step: {step}")
    if re.search(r"integrat", " ".join(aspects.get("asks_of_owner", [])), re.IGNORECASE) and "integrat" not in lowered:
        missing.append("systems integration (the client mentions an ERP or API)")
    if aspects.get("open_questions") and not re.search(r"to be confirmed|not stated|open items", lowered):
        missing.append("the open questions of the intake (what the RFP does not say) are not flagged")
    allowed = allowed_numbers(brief, facts, derived or [])
    # a text written by the department owner is its own source: the figures the owner commits to (a capacity, a price) are not "invented"
    unsupported = [] if human_authored else sorted({number for number in re.findall(r"\d[\d.,]*", content) if re.sub(r"[.,\s]", "", number) and re.sub(r"[.,\s]", "", number) not in allowed})
    unsupported = [number.rstrip(".,") for number in unsupported]
    return {"pass": not missing and not unsupported, "missing_aspects": missing, "unsupported_figures": unsupported}


def evaluate_compliance(content: str, brief: dict, facts: Facts) -> dict:
    violated = rules.check_all(content, brief["client"]["country"], facts.sla_percent)
    return {
        "pass": not violated,
        "rule_ids": list(violated),
        "violations": [f"{rule_id} ({rules.RULES[rule_id]['name']}): {violation}" for rule_id, items in violated.items() for violation in items],
        "checked_rule_ids": list(rules.RULES),
    }


# ------------------------------------------------------------------------------------------------ consolidation
def consolidate(department_id: str, section_id: str, iteration: int, readability: dict, relevance: dict, compliance: dict) -> dict:
    """The structured EvaluationResult, with feedback the generator can act on (what to fix, in which words), and the same fixes as data."""
    fixes: list[dict] = []
    for rule_id in compliance["rule_ids"]:
        detail = " ".join(v.split("): ", 1)[1] for v in compliance["violations"] if v.startswith(f"{rule_id} "))
        fixes.append({"kind": "compliance", "id": rule_id, "instruction": f"{rule_id} {rules.RULES[rule_id]['text']} Problem: {detail}"})
    for aspect in relevance["missing_aspects"]:
        fixes.append({"kind": "relevance", "id": aspect, "instruction": f"Address what the RFP asks: {aspect}."})
    for number in relevance["unsupported_figures"]:
        fixes.append({"kind": "relevance", "id": f"figure:{number}", "instruction": f"The figure {number} is neither in the RFP nor in the knowledge base: remove it or replace it with 'to be confirmed by the owner'."})
    if not readability["pass"]:
        for problem in readability["details"]["problems"]:
            fixes.append({"kind": "readability", "id": "readability", "instruction": f"Readability: {problem}. Use shorter sentences and plainer words."})
    overall = readability["pass"] and relevance["pass"] and compliance["pass"]
    feedback = "The section passed all checks." if overall else "Fix these before the next draft: " + " ".join(f"({index}) {fix['instruction']}" for index, fix in enumerate(fixes, start=1))
    return {
        "section_id": section_id,
        "department_id": department_id,
        "iteration": iteration,
        "readability": readability,
        "relevance": relevance,
        "compliance": compliance,
        "overall_pass": overall,
        "feedback_for_generator": feedback,
        "fixes": fixes,
    }
