"""The shape of what the agent may propose to remember, and how it is filed.

Three kinds of fact are worth remembering at TrackFlow (CONTEXT-company.md):

| Kind | Example | Scope (how it is consolidated) | Lifetime |
|---|---|---|---|
| `carrier_rule` | "SEUR no longer covers the rural area of Zaragoza; the local carrier has been used since last month" | carrier + country | until superseded or unused for a long time |
| `incident_context` | "The Los Angeles delays this week come from the port strike, not from TrackFlow" (a recurring incident, at least 2 occurrences) | country + topic | days (the incident passes) |
| `client_preference` | "The cosmetics client wants its monthly report with the returns breakdown first" | client | until superseded or unused for a long time |

Carrier rules are filed by carrier + country, not by ticket, so assignment rules do not end up as dozens of loose entries.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

KINDS = ("carrier_rule", "incident_context", "client_preference")
INCIDENT_TTL = timedelta(days=14)
SUMMARY_MIN, SUMMARY_MAX = 15, 300

COUNTRY_ALIASES = {
    "us": "US", "usa": "US", "u.s.": "US", "united states": "US", "america": "US", "la": "US", "los angeles": "US", "california": "US",
    "es": "ES", "spain": "ES", "españa": "ES", "espana": "ES", "zaragoza": "ES", "zgz": "ES", "aragon": "ES", "aragón": "ES", "catalonia": "ES", "madrid": "ES",
}


def normalize_country(value: str | None) -> str | None:
    if value is None:
        return None
    return COUNTRY_ALIASES.get(value.strip().lower())


def slug(value: str | None, limit: int = 40) -> str | None:
    if not value:
        return None
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text[:limit] or None


class MemoryProposal(BaseModel):
    """What the model puts in the `memory_proposal` field of its structured answer."""

    kind: Literal["carrier_rule", "incident_context", "client_preference"]
    summary: str = Field(description="The fact, in one sentence, exactly as it would be stored.")
    reason: str = Field(description="Why it is worth remembering (what pattern or correction it records).")
    carrier: str | None = None
    country: str | None = None
    client: str | None = None
    topic: str | None = None
    occurrences: int | None = Field(default=None, description="For an incident: how many times it has been reported (it must be a recurring one).")

    @field_validator("summary", "reason")
    @classmethod
    def _text(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("empty")
        return value

    def validated(self) -> "MemoryProposal":
        """Raises ValueError if the proposal is not complete for its kind. Returns a copy with the country normalised to US / ES."""
        if not (SUMMARY_MIN <= len(self.summary) <= SUMMARY_MAX):
            raise ValueError(f"the summary must be between {SUMMARY_MIN} and {SUMMARY_MAX} characters")
        country = normalize_country(self.country)
        if self.kind == "carrier_rule" and not (self.carrier and country):
            raise ValueError("a carrier rule needs a carrier and a country (US or ES)")
        if self.kind == "incident_context":
            if not country:
                raise ValueError("incident context needs a country (US or ES)")
            if (self.occurrences or 0) < 2:
                raise ValueError("incident context must be recurring (at least 2 occurrences): a single incident is not memorable")
        if self.kind == "client_preference" and not self.client:
            raise ValueError("a client preference needs the client")
        return self.model_copy(update={"country": country})

    @property
    def scope_key(self) -> str:
        if self.kind == "carrier_rule":
            return f"carrier:{slug(self.carrier)}|country:{self.country}"
        if self.kind == "client_preference":
            return f"client:{slug(self.client)}"
        return f"incident:{self.country}:{slug(self.topic or self.carrier or 'general')}"


def parse_proposal(raw: dict | None) -> MemoryProposal | None:
    """The model's `memory_proposal` field, as a valid proposal. `None` (nothing to remember) stays `None`; a malformed or incomplete one raises ValueError."""
    if raw in (None, {}, False):
        return None
    try:
        return MemoryProposal.model_validate(raw).validated()
    except ValidationError as error:
        raise ValueError("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())) from None
