"""What must NEVER enter the agent's memory (CONTEXT-company.md, milestone 8), as code.

| Category | Forbidden | Why |
|---|---|---|
| `address` | an exact street address of an end customer (B2C) **or of a B2B client site** (street + number, apartment / floor, ZIP or postcode with city) | sensitive physical-security information; the context asks for both client types, not just one |
| `warehouse_route_or_access` | internal warehouse routes and access details (aisles, racks, bays, dock doors, gate / alarm / access codes, badges, key boxes, pick paths) | same: it tells a person how to get in and where the goods are |
| `single_package_incident` | the data of one isolated package or complaint (a tracking number, an order number, "this package") | an isolated incident is not a pattern, so it is not memorable |
| `commercial_negotiation` | anything from an active commercial negotiation (contract terms, discounts, quotes, renewals) | that belongs to the commercial team's CRM, not the support agent's memory |
| `personal_contact` | emails and phone numbers | personal data nobody asked the memory to keep |

The check is deliberately conservative: it prefers to refuse a legitimate note (the user can restate it without the detail) over keeping a forbidden one.
It runs three times: on the model's proposal before the user is even asked, on a user's edit of it, and again on the text that is about to be written.
`redact()` removes the same patterns from text kept in the audit log, so the audit trail cannot become the place where sensitive data ends up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CATEGORIES = ("address", "warehouse_route_or_access", "single_package_incident", "commercial_negotiation", "personal_contact")

_STREET_WORDS = r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|way|court|ct|place|pl|terrace|calle|avenida|avda|paseo|plaza|camino|carrer|ronda|travesia|travesía)"
_PATTERNS: dict[str, list[re.Pattern]] = {
    "address": [
        re.compile(rf"\b\d{{1,5}}\s+(?:[A-Za-zÁÉÍÓÚÑáéíóúñ0-9'.]+\s+){{0,3}}{_STREET_WORDS}\b", re.IGNORECASE),  # 1234 Sunset Blvd
        re.compile(r"\b(?:calle|c/|avenida|avda\.?|av\.|paseo|plaza|camino|carrer|ronda)\s+[\wÁÉÍÓÚÑáéíóúñ' .]{2,40}?,?\s*(?:n[ºo°.]?\s*)?\d{1,5}\b", re.IGNORECASE),  # Calle Mayor 12
        re.compile(r"\b(?:apt|apartment|suite|ste|unit|floor|piso|puerta|planta|escalera)\b\.?\s*#?\s*(?:\d{1,4}[A-Za-z]?|[A-Za-z]\d{0,3})\b", re.IGNORECASE),  # "apt 5B", "unit 12", "planta 3": not "United States" or "floor plan"
        re.compile(r"\b(?:CA|California)\s+9\d{4}\b|\b50\d{3}\s+Zaragoza\b|\b\d{5}\s+Los\s+Angeles\b", re.IGNORECASE),  # ZIP / postcode with its city
        re.compile(r"\b(?:lives|delivers?|deliver(?:ed|y)? to|ship(?:s|ped)? to)\s+(?:at|in)\s+\d{1,5}\s+\w+", re.IGNORECASE),
    ],
    "warehouse_route_or_access": [
        re.compile(r"\b(?:aisle|rack|racking|bay|shelf|shelves|slot|bin|pallet position)\s*[#-]?\s*[A-Za-z]?\d{1,3}[A-Za-z]?\b", re.IGNORECASE),
        re.compile(r"\b(?:dock|loading dock|gate|door|bay)\s*(?:door\s*)?#?\s*\d{1,3}\b", re.IGNORECASE),
        re.compile(r"\b(?:gate|door|access|entry|security|alarm|key ?pad|lock ?box|key ?box|padlock|badge|pin|combination)\s*(?:code|number|pin|combo)?\s*(?:is|=|:)?\s*\d{3,}\b", re.IGNORECASE),
        re.compile(r"\b(?:alarm|access|gate|security|door|entry)\s+code\b|\block ?box\b|\bkey ?box\b|\bbadge\b", re.IGNORECASE),
        re.compile(r"\b(?:warehouse|internal|picking|pick(?:ing)?|forklift|inside the warehouse)\s+(?:route|path|layout|map|corridor)\b|\bpick path\b|\bzone\s+[A-Z]\d\b", re.IGNORECASE),
    ],
    "single_package_incident": [
        re.compile(r"\b[A-Z]{2}\s?\d{4,}[A-Z]{0,2}\b"),  # a tracking number such as XJ4471
        re.compile(r"\b(?:tracking|track(?:ing)? (?:number|no\.?|id)|waybill|awb|shipment (?:id|number))\s*[:#]?\s*[A-Z0-9-]{5,}\b", re.IGNORECASE),
        re.compile(r"\b(?:order|parcel|package|shipment)\s*(?:number|no\.?|id|#)\s*[:#]?\s*[A-Z0-9-]{3,}\b", re.IGNORECASE),
        re.compile(r"\b(?:this|that|one|a single|an isolated)\s+(?:package|parcel|shipment|order|complaint|customer complaint)\b", re.IGNORECASE),
        re.compile(r"\bone[- ]off\b|\bisolated (?:incident|case|complaint)\b|\bjust this once\b", re.IGNORECASE),
    ],
    "commercial_negotiation": [
        re.compile(r"\bnegotiat\w*\b", re.IGNORECASE),
        re.compile(r"\bcontract\s+(?:renewal|terms|price|pricing|negotiation|signing|draft|clause|extension)\b|\b(?:renew|renewing|signing)\s+(?:the\s+)?contract\b|\bnew contract\b", re.IGNORECASE),
        re.compile(r"\bdiscounts?\b|\brebates?\b|\bcounter-?offers?\b|\bquotes?\b|\bquotation\b|\bpricing (?:proposal|offer|discussion)\b|\bprice (?:cut|reduction|offer|match)\b|\bcommercial (?:terms|offer|proposal)\b|\bRFP\b|\bdeal terms\b|\bvolume (?:commitment|tier)\b", re.IGNORECASE),
    ],
    "personal_contact": [
        re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
        re.compile(r"(?<!\d)(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{3}\)?[ .-]?)\d{3}[ .-]?\d{3,4}(?!\d)"),
    ],
}

_REDACTION_LABELS = {
    "address": "[removed: address]",
    "warehouse_route_or_access": "[removed: site access]",
    "single_package_incident": "[removed: package ref]",
    "commercial_negotiation": "[removed: negotiation]",
    "personal_contact": "[removed: contact]",
}


@dataclass(frozen=True)
class PolicyResult:
    allowed: bool
    categories: tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        return ", ".join(self.categories)


def check(*texts: str | None) -> PolicyResult:
    """Is it allowed to remember this? Looks at every text given; the result names the forbidden categories found (never the offending text)."""
    found = []
    for category in CATEGORIES:
        if any(pattern.search(text) for text in texts if text for pattern in _PATTERNS[category]):
            found.append(category)
    return PolicyResult(allowed=not found, categories=tuple(found))


def redact(text: str | None, limit: int = 500) -> str:
    """The same text with whatever the policy forbids replaced by a label, shortened: what the audit log may keep."""
    result = text or ""
    for category in ("address", "single_package_incident", "personal_contact", "warehouse_route_or_access"):
        for pattern in _PATTERNS[category]:
            result = pattern.sub(_REDACTION_LABELS[category], result)
    return result[:limit]
