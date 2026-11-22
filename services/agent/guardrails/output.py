"""Output guard: the model's answer is validated BEFORE the user sees it. Three different checks, because there are three different ways an answer can be wrong.

| Check | Failure type | Looks for | If it fails |
|---|---|---|---|
| format (structural) | `structural` | not a string, empty, over-long, a raw JSON dump of an internal structure (`memory_proposal`, tool fields), unfilled template markers, untrusted-data delimiters | the answer is replaced by a safe fallback, or the delimiters are stripped |
| leaked instructions (security) | `security` | the system prompt's canary, ten consecutive words copied from its core, "my system prompt says..." | the answer is replaced by the refusal; nothing of it is shown |
| sensitive data (content) | `content` / `security` | negotiated carrier rates or commercial terms with B2B clients, warehouse addresses or internal routes, and the order or tracking number of ANOTHER customer | the answer is replaced by a refusal that reveals nothing |
| scope (content) | `content` | a casual / out-of-domain answer that does not steer back to the company's context | the redirect sentence is appended |

It runs on EVERY final answer of the agent, including the fixed texts of the other guards, so a bug upstream cannot put a forbidden answer in front of a user.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..memory import policy as memory_policy
from . import events, messages, prompt
from .session import SessionContext, extract_references, normalise_reference

MAX_ANSWER_CHARS = 4000

_RAW_STRUCTURE = re.compile(r'"memory_proposal"\s*:|"answer"\s*:\s*"|"tool"\s*:\s*"(?:ticket_lookup|stock_lookup)"|\{\s*"(?:status|kind|summary)"\s*:')
_TEMPLATE = re.compile(r"\{\{[^}]*\}\}|\{(?:question|context|answer|memories?)\}|<<\s*/?\s*(?:END_)?UNTRUSTED_DATA[^>]*>>")
_SELF_DISCLOSURE = re.compile(r"\b(?:my|the) (?:system )?(?:prompt|instructions) (?:say|says|tell|tells|state|states|are|is|read|reads)\b|\b(?:i was|i am|i've been) (?:told|instructed|programmed|configured) (?:to|that)\b[^.?!]{0,80}\b(?:never|not|must|always)\b|\bmy (?:hidden|internal|secret|initial) (?:instructions|rules|prompt)\b", re.IGNORECASE)
_CARRIERS = r"(?:ups|fedex|dhl|mrw|seur)"
_NEGOTIATED = [
    re.compile(r"\bnegotiated (?:rates?|prices?|pricing|terms|discounts?|tariffs?|fees?)\b", re.IGNORECASE),
    re.compile(rf"\b{_CARRIERS}\b[^.?!\n]{{0,60}}\b(?:charges?|charged|rates?|costs?|prices?|discounts?|rebates?|tariffs?|fees?|per (?:parcel|package|kg|kilo|shipment|label))\b[^.?!\n]{{0,40}}(?:[€$£]\s?\d|\d[\d.,]*\s?%|\d[\d.,]*\s?(?:eur|usd|euros?|dollars?|percent|cents?)\b)", re.IGNORECASE),
    re.compile(rf"\b(?:our|trackflow'?s|the|its)\b[^.?!\n]{{0,15}}\b(?:rates?|prices?|discounts?|rebates?|contract|deal|terms|agreement)\b[^.?!\n]{{0,25}}\b(?:with|from|for)\b[^.?!\n]{{0,15}}\b{_CARRIERS}\b", re.IGNORECASE),
    re.compile(r"\b(?:commercial|contract(?:ual)?|pricing) terms\b[^.?!\n]{0,40}\b(?:with|between|for)\b[^.?!\n]{0,40}\b(?:client|brand|customer|b2b|trackflow)\b|\b(?:the |our )?(?:client|brand|b2b customer)'?s? (?:negotiated|special|custom|preferential|private) (?:rate|price|discount|contract|terms)\b", re.IGNORECASE),
]
# a real redirect points back to what the agent does ("I can help with your shipments..."); a keyword that happens to appear in the answer ("for example for returns") is not one
_REDIRECT_MARKERS = re.compile(r"\b(?:trackflow|i can help|i'm here to help|i am here to help|happy to help|can i help|how can i help|help you with|help with your|anything (?:i|else)|what can i do|back to your (?:shipment|order|return|parcel))\b", re.IGNORECASE)


@dataclass
class OutputVerdict:
    answer: str
    outcome: str | None = None  # a new outcome when the answer was replaced
    actions: list[str] = field(default_factory=list)
    guard: str | None = None  # which guard replaced it, and its type of failure
    failure_type: str | None = None


def _allowed_references(session: SessionContext | None, allowed: set[str], question: str) -> set[str]:
    return set(allowed) | {normalise_reference(r) for r in extract_references(question)}


def validate(answer, *, question: str = "", session: SessionContext | None = None, authorized_refs: set[str] | None = None, scope: str = "domain", outcome: str = "generated", trusted: bool = False) -> OutputVerdict:
    """The final answer, checked and, if needed, replaced or amended. Never raises.

    `trusted=True` is for the harness's own fixed refusals: they are only checked for format, because they legitimately talk about "my instructions" ("I can't share my internal instructions")."""
    # ---- structural
    if not isinstance(answer, str) or not answer.strip():
        events.record("output_format", "structural", "replaced", "the answer is empty or not text")
        return OutputVerdict(messages.OUTPUT_FALLBACK, "output_invalid", ["format"], "output_format", "structural")
    if len(answer) > MAX_ANSWER_CHARS or _RAW_STRUCTURE.search(answer):
        events.record("output_format", "structural", "replaced", "the answer is over-long or a dump of an internal structure", text=answer[:80])
        return OutputVerdict(messages.OUTPUT_FALLBACK, "output_invalid", ["format"], "output_format", "structural")
    verdict = OutputVerdict(answer.strip())
    if _TEMPLATE.search(verdict.answer):
        verdict.answer = _TEMPLATE.sub("", verdict.answer).strip() or messages.OUTPUT_FALLBACK
        events.record("output_format", "structural", "redacted", "template or delimiter markers were in the answer")
        verdict.actions.append("markers_stripped")
    if trusted:
        return verdict
    # ---- leaked instructions
    if prompt.leaks_prompt(verdict.answer) or _SELF_DISCLOSURE.search(verdict.answer):
        events.record("output_leak", "security", "replaced", "the answer reproduces or describes the system prompt", text=verdict.answer[:80])
        return OutputVerdict(messages.OUTPUT_LEAK, "blocked_output", ["leak"], "output_leak", "security")
    # ---- sensitive data
    if any(p.search(verdict.answer) for p in _NEGOTIATED):
        events.record("output_sensitive", "content", "replaced", "negotiated carrier rates or commercial terms", text=verdict.answer[:80])
        return OutputVerdict(messages.OUTPUT_WITHHELD, "blocked_output", ["negotiated_rates"], "output_sensitive", "content")
    found = memory_policy.check(verdict.answer)
    if "address" in found.categories or "warehouse_route_or_access" in found.categories:
        events.record("output_sensitive", "content", "replaced", f"location or access detail ({found.reason})", text=verdict.answer[:80])
        return OutputVerdict(messages.OUTPUT_WITHHELD, "blocked_output", ["locations"], "output_sensitive", "content")
    if session is not None and not session.is_staff:
        foreign = [r for r in extract_references(verdict.answer) if normalise_reference(r) not in _allowed_references(session, authorized_refs or set(), question)]
        if foreign:
            events.record("output_other_customer", "security", "replaced", "the answer names an order or tracking number that is not this customer's", text=" ".join(foreign))
            return OutputVerdict(messages.AUTHORIZATION, "blocked_output", ["other_customer"], "output_other_customer", "security")
    # ---- scope: a casual answer must steer back
    if scope == "casual" and outcome == "generated" and not _REDIRECT_MARKERS.search(verdict.answer[-260:]):
        verdict.answer += messages.REDIRECT
        events.record("out_of_domain", "content", "redirected", "the casual answer did not steer back to the company's context; the redirect was added")
        verdict.actions.append("redirect_added")
    return verdict
