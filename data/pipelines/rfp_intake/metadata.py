"""RFP metadata from the converted Markdown (CONTEXT 2.2): client, country, services, monthly volume, deadline, budget.

Rules first (they are deterministic and tested against the three sample PDFs); the model may fill what the rules left empty, but only with values that appear in
the document (`grounded`). A value that is not in the RFP stays `None` and becomes an open question: volumes and figures are never invented.

The samples are bilingual (English headings, Spanish body), so every pattern understands both.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

from . import llm
from .config import COUNTRY_CURRENCY, DEPARTMENTS, SERVICE_TO_DEPARTMENT, SERVICES


def fold(text: str) -> str:
    """Lower case without accents, same length as the input so positions still match the original."""
    return "".join(unicodedata.normalize("NFKD", ch)[0].lower() for ch in text)


# ---------------------------------------------------------------- numbers and dates
def parse_number(raw: str) -> int | None:
    """`15.000` and `5,000` are both fifteen thousand / five thousand; `5k` is five thousand; `2.5` is not a count."""
    raw = raw.strip().lower().replace(" ", "")
    multiplier = 1
    if raw.endswith("k"):
        raw, multiplier = raw[:-1], 1000
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", raw):
        return int(re.sub(r"[.,]", "", raw)) * multiplier
    if re.fullmatch(r"\d+", raw):
        return int(raw) * multiplier
    if multiplier > 1 and re.fullmatch(r"\d+[.,]\d", raw):
        return round(float(raw.replace(",", ".")) * multiplier)
    return None


MONTHS = {
    "january": 1, "enero": 1, "jan": 1, "ene": 1, "february": 2, "febrero": 2, "feb": 2, "march": 3, "marzo": 3, "mar": 3, "april": 4, "abril": 4, "apr": 4, "abr": 4,
    "may": 5, "mayo": 5, "june": 6, "junio": 6, "jun": 6, "july": 7, "julio": 7, "jul": 7, "august": 8, "agosto": 8, "aug": 8, "ago": 8,
    "september": 9, "septiembre": 9, "setiembre": 9, "sep": 9, "sept": 9, "october": 10, "octubre": 10, "oct": 10, "november": 11, "noviembre": 11, "nov": 11,
    "december": 12, "diciembre": 12, "dec": 12, "dic": 12,
}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY_SUFFIX = r"(?:st|nd|rd|th|o)?"
DATE_PATTERNS = (
    re.compile(rf"\b(?P<month>{_MONTH})\.?\s+(?P<day>\d{{1,2}}){_DAY_SUFFIX}(?:,?\s+(?P<year>20\d\d))?\b", re.IGNORECASE),  # August 28, 2026 | August 24th
    re.compile(rf"\b(?P<day>\d{{1,2}}){_DAY_SUFFIX}\s+(?:de\s+|of\s+)?(?P<month>{_MONTH})\.?(?:,?\s+(?:de\s+)?(?P<year>20\d\d))?\b", re.IGNORECASE),  # 28 ago 2026 | 28 de agosto de 2026
    re.compile(r"\b(?P<year>20\d\d)-(?P<m>\d\d)-(?P<d>\d\d)\b"),
)


def find_dates(text: str) -> list[dict]:
    """Every date written in the text: `iso` when the year is there, otherwise only the words as written."""
    found = []
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groupdict()
            try:
                if "m" in groups and groups.get("m"):
                    month, day = int(groups["m"]), int(groups["d"])
                else:
                    month, day = MONTHS[fold(groups["month"]).rstrip(".")], int(groups["day"])
                year = int(groups["year"]) if groups.get("year") else None
                iso = date(year, month, day).isoformat() if year else None
            except (ValueError, KeyError):
                continue
            found.append({"iso": iso, "raw": match.group(0).strip(), "start": match.start(), "end": match.end()})
    found.sort(key=lambda item: item["start"])
    deduplicated, last_end = [], -1
    for item in found:
        if item["start"] >= last_end:
            deduplicated.append(item)
            last_end = item["end"]
    return deduplicated


# ---------------------------------------------------------------- country
US_MARKERS = (r"\busa\b", r"\bu\.s\.a?\b", r"\bunited states\b", r"\bestados unidos\b", r",\s*(?:ca|ny|tx|fl|wa|il|nv|az|nj)\b(?!\w)", r"\bcalifornia\b", r"\blos angeles\b", r"\busd\b", r"\$\s?\d", r"\bacross the us\b", r"\bnationwide\b")
ES_MARKERS = (r"\bespana\b", r"\bspain\b", r"\bs\.\s?l\.?\b", r"\bs\.\s?a\.?\b", r"\.es\b", r"\bzaragoza\b", r"\bmadrid\b", r"\bbarcelona\b", r"\bvalencia\b", r"\beur\b", "€", r"\bpenins\w+")


def detect_country(text: str) -> str | None:
    """`US` or `ES`, by which country the document points to more; a tie (or nothing) is unknown, never a guess."""
    folded = fold(text)
    us = sum(len(re.findall(pattern, folded)) for pattern in US_MARKERS)
    es = sum(len(re.findall(pattern, folded)) for pattern in ES_MARKERS)
    if us == es:
        return None
    return "US" if us > es else "ES"


# ---------------------------------------------------------------- services
SERVICE_TERMS = {
    "warehousing": r"warehous\w*|almacen\w*|storage|fulfil+ment|\b3pl\b|centro logistico",
    "last_mile": r"last[- ]mile|ultima milla|(?<!return )(?<!returns )(?<!inbound )shipping|\bcarriers?\b|\bcourier\w*|transportistas?|reparto|entregas a|\benvios\b|parcel delivery|home delivery",
    "reverse_logistics": r"returns?\b|devoluci\w+|reverse logistics|logistica inversa|reacondicion\w*|refurbish\w*|restock",
}
NEGATION_CUES = re.compile(
    r"no se requiere|no se necesita|no requerimos|no necesitamos|no necesit\w+|no requiere\w*|not required|no need (?:for|of|to)|do not need|don't need|do not require|don't require|"
    r"not looking for|excluding|excluye\w*|\bsin\b|\bni\b|\bwithout\b|(?:su|nuestro) propio|\bown (?:carrier|fleet|delivery|shipping)|in-house|keeps? (?:its|our|their) own|\bno\s+(?=(?:last|ultima|returns?|devoluci|warehous|almacen))",
)


def blocks(text: str) -> list[str]:
    """The text as readable units: the soft line breaks of a PDF joined back (a negation and the service it negates are often on two lines), bullets kept apart."""
    result: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        current = ""
        for line in paragraph.split("\n"):
            if re.match(r"\s*(?:[-*•]|\d+[.)])\s", line) and current:
                result.append(current)
                current = ""
            current = f"{current} {line.strip()}".strip()
        if current:
            result.append(current)
    return result


def _segments(text: str) -> list[str]:
    """Sentences of the blocks, folded for matching."""
    return [piece for block in blocks(text) for piece in re.split(r"(?<!\d)[.;!?](?!\d)", fold(block)) if piece.strip()]


def detect_services(text: str) -> tuple[list[str], list[str]]:
    """(requested, explicitly excluded). A mention counts unless a negation comes before it in its own sentence ("no se requiere ... ultima milla", "mantenemos
    nuestro propio transportista"): that is how an RFP says a service is NOT wanted, and that must not trigger the department."""
    requested: dict[str, bool] = {}
    excluded: dict[str, bool] = {}
    for segment in _segments(text):
        cue = NEGATION_CUES.search(segment)
        for service, terms in SERVICE_TERMS.items():
            for match in re.finditer(terms, segment):
                if cue and match.start() >= cue.end():
                    excluded[service] = True
                elif cue and cue.start() <= match.start() < cue.end():
                    excluded[service] = True  # the cue is itself the service ("su propio transportista")
                else:
                    requested[service] = True
                    break
    wanted = [service for service in SERVICES if requested.get(service)]
    left_out = [service for service in SERVICES if excluded.get(service) and service not in wanted]
    return wanted, left_out


# ---------------------------------------------------------------- volume, budget, deadline
_COUNT = r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d)?\s?k?)"
VOLUME_PATTERNS = (
    re.compile(rf"{_COUNT}\s*(?:\+\s*)?(?:orders|pedidos|shipments|envios|parcels|packages|paquetes)\s*(?:a|per|each|every|/|al|por|cada)\s*(?:month|mes)\b"),
    re.compile(rf"{_COUNT}\s*(?:orders|pedidos|shipments|envios|parcels|packages|paquetes)\s*(?:monthly|mensuales|mensual)\b"),
    re.compile(rf"(?:monthly|mensual|volumen mensual|monthly volume)[^.\n]{{0,40}}?{_COUNT}\s*(?:orders|pedidos|shipments|envios|parcels|packages|paquetes)"),
)


def detect_volume(text: str) -> int | None:
    """Orders per month, only when the RFP says so. SKUs or "over 10,000 packages" thresholds of someone else are not the client's volume."""
    folded = fold(text)
    for pattern in VOLUME_PATTERNS:
        for match in pattern.finditer(folded):
            if re.search(r"(?:over|more than|above|at least|minimum|mas de|superior a|minimo)\s*$", folded[max(0, match.start() - 14) : match.start()]):
                continue  # a threshold ("carriers handling over 10,000 packages/month"), not the client's volume
            return parse_number(match.group(1))
    return None


def detect_budget(text: str) -> str | None:
    match = re.search(
        r"(?:budget|presupuesto)[^.\n]{0,50}?((?:[$€]|usd\s?|eur\s?)\s?\d[\d.,]*\s?[km]?(?:\s?(?:-|–|to|a|y)\s?(?:[$€]|usd\s?|eur\s?)?\s?\d[\d.,]*\s?[km]?)?(?:\s?(?:usd|eur|euros|dollars|dolares))?)",
        text,
        re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else None


DUE_LABEL = re.compile(r"(?:proposal(?:s)? due(?: date)?|due date|submission deadline|deadline|fecha l[ií]mite|fecha de entrega|propuestas? (?:hasta|antes)|no later than)\W{0,3}", re.IGNORECASE)
GO_LIVE = re.compile(r"up and running|go[- ]live|start (?:operat|working)|begin operat|operativ|puesta en marcha|empezar a operar", re.IGNORECASE)


def detect_deadline(text: str) -> tuple[str | None, str]:
    """(deadline, kind). A labelled due date wins (`proposal_due`); an informal "we want to be running around August 24th" is a `go_live` target, and without a year
    it is kept as written: guessing the year would be inventing a date."""
    for match in DUE_LABEL.finditer(text):
        window = text[match.end() : match.end() + 60]
        dates = find_dates(window)
        if dates and dates[0]["start"] <= 6:
            return dates[0]["iso"] or dates[0]["raw"], "proposal_due"
    for item in find_dates(text):
        context = text[max(0, item["start"] - 140) : item["end"] + 40]
        if GO_LIVE.search(context):
            return item["iso"] or f"around {item['raw']} (year not stated)", "go_live"
        before = text[max(0, item["start"] - 25) : item["start"]].lower()
        if re.search(r"\b(?:by|before|until|around|antes del?|para el)\s*$", before):
            return item["iso"] or f"{item['raw']} (year not stated)", "stated_date"
    return None, "none"


# ---------------------------------------------------------------- client
NOT_A_CLIENT = re.compile(r"^(?:trackflow|n/?a|tbd)$", re.IGNORECASE)
ORG_LABEL = re.compile(r"^\W*(?:issuing organi[sz]ation|organi[sz]ation|company|client|cliente|empresa|issued by|emitido por)\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
CITY_HEADER = re.compile(r"^\s*([A-Z][\w&.' -]{1,40}?)\s+[—–-]\s+[A-Z][\w .,]*(?:USA|CA|NY|TX|Spain|Espa[nñ]a|Madrid|Barcelona|Zaragoza)\b", re.MULTILINE)
SELF_INTRO = re.compile(r"\b(?:we're|we are|i'm with|i am with|i'm from|we represent|somos|soy de|represento a)\s+([A-Z][\w&.' -]{1,40}?)(?:,|\.| a | an | una? | from | de )", re.IGNORECASE)
SIGNATURE = re.compile(r"^\s*(?P<name>[A-ZÁÉÍÓÚÑ][\w'’.-]+(?:\s+[A-ZÁÉÍÓÚÑ][\w'’.-]+){1,2})\s*\n\s*(?P<title>[^\n,]{3,60}?)(?:,\s*(?P<company>[^\n]{2,50}))?\s*$", re.MULTILINE)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def detect_client(text: str) -> str | None:
    for pattern in (ORG_LABEL, CITY_HEADER):
        match = pattern.search(text)
        if match and not NOT_A_CLIENT.match(match.group(1).strip()):
            return match.group(1).strip(" ,")
    match = SELF_INTRO.search(text)
    if match and not NOT_A_CLIENT.match(match.group(1).strip()):
        return match.group(1).strip(" ,")
    contact = detect_contact(text)
    return contact.get("company") if contact else None


def detect_contact(text: str) -> dict:
    """The person who signs the RFP: name, title, company and the email the answer goes to (when present)."""
    contact: dict = {}
    for match in SIGNATURE.finditer(text):
        title = match.group("title").strip()
        if re.search(r"[:@]|\d{3}", title):
            continue
        contact = {"name": match.group("name").strip(), "title": title}
        if match.group("company"):
            contact["company"] = match.group("company").strip(" ,.")
    email = EMAIL.search(text)
    if email:
        contact["email"] = email.group(0)
    return contact


def detect_extras(text: str) -> dict:
    """Facts the departments may need, as written: reference number, SKU count, where the warehouse should be."""
    extras: dict = {}
    reference = re.search(r"(?:RFP\s+)?Reference\s+(?:No\.?|Number)\s*:\s*([\w./-]+)", text, re.IGNORECASE)
    if reference:
        extras["reference_number"] = reference.group(1)
    skus = re.search(r"(?:aproximadamente|approximately|about|around|~)?\s*(\d{1,3}(?:[.,]\d{3})+|\d+)\s*SKUs?\b", text, re.IGNORECASE)
    if skus:
        extras["sku_count"] = parse_number(skus.group(1))
    place = re.search(r"(?:cercano a|cerca de|near|close to|en|in)\s+(Zaragoza|Madrid|Barcelona|Valencia|Los Angeles|LA|California)\b", text)
    if place:
        extras["preferred_location"] = place.group(1)
    issued = re.search(r"Date Issued\s*:\s*(.+)", text, re.IGNORECASE)
    if issued:
        found = find_dates(issued.group(1))
        if found:
            extras["issued_on"] = found[0]["iso"] or found[0]["raw"]
    return extras


# ---------------------------------------------------------------- everything
def grounded(value: str | None, text: str) -> bool:
    """A value the model proposes counts only when it is in the document (accents and case aside)."""
    return bool(value) and fold(str(value)).strip(" .,") in fold(text)


def extract(markdown: str) -> dict:
    """The metadata of one RFP. Fields the document does not state are `None` / empty and listed in `open_questions`."""
    services, excluded = detect_services(markdown)
    deadline, deadline_kind = detect_deadline(markdown)
    country = detect_country(markdown)
    result = {
        "client_name": detect_client(markdown),
        "client_country": country,
        "client_contact": detect_contact(markdown),
        "services_requested": services,
        "services_excluded": excluded,
        "monthly_volume": detect_volume(markdown),
        "deadline": deadline,
        "deadline_kind": deadline_kind,
        "budget_range": detect_budget(markdown),
        "extras": detect_extras(markdown),
        "engine": "rules",
    }
    missing = [key for key in ("client_name", "client_country", "services_requested") if not result[key]]
    if missing:
        result = _complete_with_model(markdown, result, missing)
    return finish(result)


def _complete_with_model(markdown: str, result: dict, missing: list[str]) -> dict:
    """Asks the model only for what the rules could not find, and keeps only what is in the document."""
    answer = llm.ask_json(
        "You read a logistics request sent to TrackFlow and fill in the fields the rules could not find. Fields: client_name (string), client_country ('US' or 'ES', "
        "the country the client operates from), services_requested (list of: warehousing, last_mile, reverse_logistics). Use null for what the document does not state.",
        f"Missing fields: {', '.join(missing)}\n\n{llm.wrap_document(markdown)}",
        required=(),
    )
    if not answer:
        return result
    if not result["client_name"] and isinstance(answer.get("client_name"), str) and grounded(answer["client_name"], markdown):
        result["client_name"], result["engine"] = answer["client_name"].strip(), "mixed"
    if not result["client_country"] and answer.get("client_country") in COUNTRY_CURRENCY:
        result["client_country"], result["engine"] = answer["client_country"], "mixed"
    if not result["services_requested"] and isinstance(answer.get("services_requested"), list):
        wanted = [service for service in SERVICES if service in answer["services_requested"]]
        if wanted:
            result["services_requested"], result["engine"] = wanted, "mixed"
    return result


def finish(result: dict) -> dict:
    """Derived fields: the currency (from the country), the departments (from the services) and what the RFP does not say."""
    result["currency"] = COUNTRY_CURRENCY.get(result.get("client_country") or "")
    result["departments_needed"] = [SERVICE_TO_DEPARTMENT[service] for service in result["services_requested"]]
    questions = []
    if not result["client_name"]:
        questions.append("The client's name is not stated in the document.")
    if not result["client_country"]:
        questions.append("The client's country (United States or Spain) is not clear: confirm it, because it decides the currency.")
    if not result["services_requested"]:
        questions.append("The document does not say which services are requested (warehousing, last mile, returns).")
    if result["monthly_volume"] is None:
        questions.append("The monthly order volume is not stated. Ask the client for orders per month before any pricing.")
    if not result["deadline"]:
        questions.append("No deadline is stated.")
    elif result["deadline_kind"] == "go_live":
        questions.append(f"The only date is a go-live target ({result['deadline']}), not a proposal due date: ask when the proposal is needed.")
    result["open_questions"] = questions
    return result


def department_names(ids: list[str]) -> list[str]:
    return [DEPARTMENTS[department_id]["name"] for department_id in ids]
