"""The company guidelines of CONTEXT-company.md section 5, as five rules a draft is checked against. Not an opinion: each rule is a test on the text.

    GL-1 currency       Pricing is in USD for US operations and EUR for Spain operations, decided by `client_country`
    GL-2 sla            Every proposal states the on-time delivery SLA (%) TrackFlow commits to (and it is the contractual one)
    GL-3 returns-48h    No proposal may promise returns processing in under 48 hours
    GL-4 discount-tiers Every proposal includes a volume-based discount tier table
    GL-5 carrier-rates  No proposal discloses negotiated rates with specific carriers, only the final cost offered to the client

`check_all` returns, for each rule that is violated, the sentences or facts that violate it, so the feedback to the generator can point at them.
"""

from __future__ import annotations

import re

from .config import COUNTRY_CURRENCY

RULES = {
    "GL-1": {"name": "currency", "text": "Quote prices in USD for US operations and EUR for Spain operations (client_country decides)."},
    "GL-2": {"name": "sla", "text": "State the on-time delivery SLA (%) TrackFlow commits to."},
    "GL-3": {"name": "returns-48h", "text": "Never promise returns processing in under 48 hours."},
    "GL-4": {"name": "discount-tiers", "text": "Include a volume-based discount tier table."},
    "GL-5": {"name": "carrier-rates", "text": "Never disclose negotiated rates with specific carriers; give only the final cost offered to the client."},
}

CARRIERS = r"\b(?:UPS|FedEx|DHL|MRW|SEUR)\b"
MONEY = r"(?:\d[\d.,]*\s?(?:USD|EUR|€|\$|dollars?|euros?)|(?:USD|EUR|€|\$)\s?\d)"
USD_TOKEN = re.compile(r"\bUSD\b|US\$|\$|\bdollars?\b", re.IGNORECASE)
EUR_TOKEN = re.compile(r"\bEUR\b|€|\beuros?\b", re.IGNORECASE)
NEGATION = re.compile(r"\b(?:cannot|can't|can not|never|not|no|nor|without)\b", re.IGNORECASE)
RETURN_CONTEXT = re.compile(r"return|devoluci|reverse|turnaround|restock|refurbish|reconditioning", re.IGNORECASE)


def sentences(text: str) -> list[str]:
    """Sentences and table rows (a table row is its own unit)."""
    units: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("|"):
            units.append(line)
        else:
            units.extend(piece.strip() for piece in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9*])", line) if piece.strip())
    return units


def expected_currency(country: str | None) -> str | None:
    return COUNTRY_CURRENCY.get(country or "")


def check_currency(text: str, country: str | None) -> list[str]:
    expected = expected_currency(country)
    if expected is None:
        return ["The client's country is unknown, so the currency cannot be decided (US: USD, Spain: EUR)."]
    wrong, right = (EUR_TOKEN, USD_TOKEN) if expected == "USD" else (USD_TOKEN, EUR_TOKEN)
    violations = []
    if wrong.search(text):
        violations.append(f"The text quotes prices in {'EUR' if expected == 'USD' else 'USD'}, but the client's country requires {expected}.")
    if not right.search(text):
        violations.append(f"The text never states the currency; prices must be quoted in {expected}.")
    return violations


def sla_values(text: str) -> list[float]:
    values = []
    for match in re.finditer(r"(\d{1,3}(?:\.\d+)?)\s?%", text):
        window = text[max(0, match.start() - 90) : match.end() + 90]
        if re.search(r"on[- ]time|SLA|punctual", window, re.IGNORECASE):
            values.append(float(match.group(1)))
    return values


def check_sla(text: str, expected: float | None) -> list[str]:
    values = sla_values(text)
    if not values:
        return ["The on-time delivery SLA (%) TrackFlow commits to is not stated."]
    if expected is not None and expected not in values:
        return [f"The SLA stated ({', '.join(f'{value:g}%' for value in values)}) is not TrackFlow's contractual on-time delivery SLA ({expected:g}%)."]
    return []


def returns_promises_under_48h(text: str) -> list[str]:
    """Sentences about returns that promise less than 48 hours: hours below 48, one day or less, same/next day, 'under 48'."""
    found = []
    for sentence in sentences(text):
        if not RETURN_CONTEXT.search(sentence):
            continue
        fast = False
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)?\s*(?:\d+(?:\.\d+)?\s*)?(hours?|hrs?|h|business days?|working days?|days?)\b", sentence, re.IGNORECASE):
            amount = float(match.group(1))
            hours = amount * (24 if match.group(2).lower().startswith(("day", "business", "working")) else 1)
            if hours < 48:
                fast = True
        if re.search(r"same[- ]day|next[- ]day|overnight|within the day|\b24[- ]?h\b|\bsub-48\b", sentence, re.IGNORECASE):
            fast = True
        if re.search(r"(?:under|less than|fewer than|below|within)\s*(?:the )?(?:first )?4[0-7]\b", sentence, re.IGNORECASE):
            fast = True
        if re.search(r"(?:under|less than|fewer than|below)\s*48", sentence, re.IGNORECASE) and not NEGATION.search(sentence):
            fast = True
        if fast:
            found.append(sentence)
    return found


def check_returns_48h(text: str) -> list[str]:
    return [f"Promises returns processing in under 48 hours: \"{sentence[:160]}\"" for sentence in returns_promises_under_48h(text)]


def discount_tables(text: str) -> list[list[str]]:
    tables, current = [], []
    for line in text.splitlines():
        if line.strip().startswith("|"):
            current.append(line.strip())
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def has_discount_tier_table(text: str) -> bool:
    for table in discount_tables(text):
        header = table[0].lower()
        rows = [row for row in table[1:] if not re.fullmatch(r"[|\s:\-]+", row)]
        if re.search(r"volume|orders|shipments|returns|units|pallets|cubic|m3|m³", header) and re.search(r"discount|rebate|reduction|tier", header) and len(rows) >= 2:
            return True
    return False


def check_discount_tiers(text: str) -> list[str]:
    return [] if has_discount_tier_table(text) else ["There is no volume-based discount tier table (a table with a volume column, a discount column and at least two tiers)."]


def check_carrier_rates(text: str) -> list[str]:
    violations = []
    for sentence in sentences(text):
        carrier = re.search(CARRIERS, sentence)
        if carrier and (re.search(MONEY, sentence) or re.search(r"negotiated|contract(?:ed)? rate|our rate with|rate (?:with|from|of)|per (?:parcel|package|shipment|kg)", sentence, re.IGNORECASE)):
            violations.append(f"Discloses a rate tied to a specific carrier ({carrier.group(0)}): \"{sentence[:160]}\"")
    return violations


def check_all(text: str, country: str | None, expected_sla: float | None = None) -> dict[str, list[str]]:
    """{rule_id: violations} for the rules that are violated; an empty dict is a fully compliant text."""
    results = {
        "GL-1": check_currency(text, country),
        "GL-2": check_sla(text, expected_sla),
        "GL-3": check_returns_48h(text),
        "GL-4": check_discount_tiers(text),
        "GL-5": check_carrier_rates(text),
    }
    return {rule_id: violations for rule_id, violations in results.items() if violations}
