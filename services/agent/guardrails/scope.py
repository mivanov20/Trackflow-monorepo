"""Content guard: is this message about TrackFlow, harmless chat, or someone trying to use the agent as a personal assistant?

The decision table of the ticket (SEC-114) and of CONTEXT-company.md:

| The message is... | Category | What the harness does |
|---|---|---|
| About tracking a shipment, return policies / SLAs (by country), delivery incidents, tickets, stock, carriers, reports | `domain` | the agent answers with its tools and the knowledge base |
| Small talk, a trivia question, a general logistics concept ("what is reverse logistics?") | `casual` | a brief answer, then a redirect to what TrackFlow does (the model is told to; the output guard makes sure it happens) |
| A task that has nothing to do with shipments, returns or incidents (essay, homework, code, advice, therapy...) | `personal_task` | refused, with a redirect to the support purpose. No part of the task is done |

Personal-task patterns win over domain words: "write me a poem about my lost parcel" is still a poem. Domain words win over small-talk patterns. A message
with no domain word and no recognisable pattern is treated as trivia if it is short, and as a personal task if it is a long request (the shape of "write
my essay"). The detector is a heuristic, not a model: it is deterministic, testable and fast, and `tests/pipelines/test_agent_guardrails.py` pins it with fixtures.
This module also detects the attempt to mix return policies of two countries (CONTEXT case 4).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

DOMAIN = re.compile(
    r"\b(?:track\w*|orders?|parcels?|packages?|shipments?|shipping|ship(?:ped)?|deliver\w*|returns?|returned|refund\w*|exchange\w*|sla|incidents?|tickets?|lost|damaged|missing|wrong address|carriers?|ups|fedex|dhl|mrw|seur|"
    r"customs|labels?|pick ?ups?|couriers?|warehouses?|stock|inventory|sku|products?|reports?|clients?|brands?|polic(?:y|ies)|delay\w*|strikes?|storage|express|b2b|b2c|customers?|status|eta|cosmetics|rural|routes?|coverage|trackflow|"
    r"seguimiento|pedidos?|paquetes?|envios?|devoluci\w*|reembolso\w*|incidencias?|entregas?|retrasos?|transportista\w*|politica\w*|extraviad\w*)\b"
)
_PERSONAL = [
    re.compile(r"\b(?:write|compose|draft|create|generate|make|give me|produce|prepare|come up with|craft|pen)\b[^.?!]{0,40}\b(?:essays?|poems?|poetry|stories|story|songs?|lyrics|speech|sonnet|haiku|novel|fiction|screenplay|code|script|program|function|algorithm|app|website|web ?page|html|css|sql|python|javascript|java|c\+\+|rust|cover letter|resume|cv|thesis|dissertation|term paper|book report|research paper|report on|article on|blog post|business plan|workout|diet plan|meal plan|recipe|itinerary|travel plan|wedding|birthday|love letter|joke book)\b"),
    re.compile(r"\b(?:homework|home work|assignment|exam|quiz|thesis|dissertation|term paper|essay|coursework|school project)\b"),
    re.compile(r"\b(?:help me with|do|solve|finish|complete|answer|check|grade|revise)\b[^.?!]{0,25}\b(?:my )?(?:math|maths|physics|chemistry|biology|history|algebra|calculus|statistics|programming|coding|science|english|literature|geography)\b[^.?!]{0,25}\b(?:homework|assignment|problem|exercise|test|exam|questions?|class|course)\b"),
    re.compile(r"\b(?:be my|act as my|you are my|become my|play my|be a)\b[^.?!]{0,15}\b(?:therapist|psychologist|counsel+or|doctor|lawyer|attorney|girlfriend|boyfriend|friend|coach|tutor|teacher|mentor|life coach|financial advisor|nutritionist|personal trainer|priest|astrologer|fortune teller)\b"),
    re.compile(r"\b(?:advice|help|tips|guidance|opinion)\b[^.?!]{0,20}\b(?:relationship|marriage|divorce|dating|breakup|health|medical|mental|depress\w*|anxiety|love life|career|job interview|investment|stocks to buy|crypto|taxes|legal|lawsuit|diet|weight loss|parenting|family problem)\b|\bi(?: am|'m| feel| have been feeling) (?:so |very |really )?(?:sad|depressed|anxious|lonely|stressed|suicidal|hopeless|heartbroken)\b"),
    re.compile(r"\b(?:for|in) my (?:class|course|exam|school|university|college|thesis|degree|professor|teacher|lecture)\b|\b(?:translate|rewrite|proofread|paraphrase|summari[sz]e)\b[^.?!]{0,30}\b(?:my (?:essay|paper|thesis|novel|poem|story|assignment|cv|resume)|this (?:essay|paper|poem|story|novel|chapter|article|book))\b"),
    re.compile(r"\b(?:escribe(?:me)?|redacta(?:me)?|hazme|dame|crea(?:me)?|genera(?:me)?|componme|preparame)\b[^.?!]{0,40}\b(?:ensayo|poema|poesia|cuento|historia|cancion|letra|discurso|codigo|programa|funcion|script|tarea|deberes|trabajo (?:de|para)|tesis|curriculum|carta de amor|receta|rutina de ejercicio|plan de dieta)\b|\b(?:mi tarea|mis deberes|mi tesis|mi examen)\b|\bsé mi (?:terapeuta|psicologo|amigo|novio|novia|medico|abogado)\b|\bsé mi\b[^.?!]{0,10}\b(?:terapeuta|psicologo)\b"),
]
_GENERAL_LOGISTICS = re.compile(
    r"\b(?:what(?:'s| is| are)|explain|define|meaning of|tell me about|how does|how do)\b[^.?!]{0,40}\b(?:reverse logistics|last[- ]mile|fulfil?lment|3pl|supply chain|freight|incoterms?|drop ?shipping|cross[- ]dock\w*|logistics|customs clearance|bill of lading|lead time|backorder|cold chain|palleti[sz]\w*|consolidation|carrier|courier)\b"
)
_CASUAL = [
    re.compile(r"^\W*(?:hi|hello|hey|hiya|howdy|yo|good (?:morning|afternoon|evening|night)|hola|buenas|buenos dias|buenas tardes|buenas noches|thanks|thank you|thx|cheers|gracias|bye|goodbye|see you|adios)\b"),
    re.compile(r"\bhow are you\b|\bhow(?:'s| is) it going\b|\bwhat(?:'s| is) up\b|\bwho are you\b|\bwhat(?:'s| is) your name\b|\bwhat can you do\b|\bcomo estas\b|\bque tal\b"),
    re.compile(r"\bwhat time is it\b|\bwhat(?:'s| is) the (?:time|date|weather|temperature)\b|\bweather\b|\btime in\b|\bhora en\b|\bque hora\b"),
    re.compile(r"\b(?:tell me a (?:joke|fun fact|riddle)|joke|funny|riddle|fun fact)\b|\bwho (?:won|is|was|invented|wrote|discovered)\b|\bcapital of\b|\bhow (?:old|tall|far|big|long) (?:is|are|was)\b|\bwhen (?:was|did|is)\b[^.?!]{0,40}\b(?:born|invented|founded|happen|war|discovered|built)\b"),
]
_SHORT_TRIVIA_WORDS = 14

_COUNTRY_WORDS = {
    "US": r"united states|u\.s\.a?\.?|usa|america\w*|us|los angeles|california|la",
    "ES": r"spain|spanish|espa(?:n|ñ)a|zaragoza|madrid|barcelona|aragon|espanol",
}
_COUNTRY_NAMES = {"US": "the United States", "ES": "Spain"}
_APPLY = re.compile(r"\b(?:apply|use|follow|treat|honou?r|go with|switch to|give me|take|enforce|grant|change|swap|substitute|replace|override|pretend|consider)\b")
_POLICY = r"(?:return|refund|exchange|delivery|shipping|sla|warranty|replacement)?\W*(?:polic(?:y|ies)|rules?|terms|conditions|window|deadline|sla)"


@dataclass(frozen=True)
class Scope:
    category: str  # domain | casual | personal_task
    subtype: str = ""  # greeting | trivia | general_logistics | ... for casual; the matched family for personal_task
    reason: str = ""


@dataclass(frozen=True)
class PolicyMix:
    requested: str  # the country whose policy the user asked for: US | ES
    actual: str | None  # the country of the order, when known


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").lower()
    return "".join(c for c in text if not unicodedata.combining(c))


def classify_scope(text: str) -> Scope:
    plain = _plain(text)
    for pattern in _PERSONAL:
        if pattern.search(plain):
            return Scope("personal_task", "personal_assistant_use", "a task unrelated to shipments, returns or incidents")
    if _GENERAL_LOGISTICS.search(plain):
        return Scope("casual", "general_logistics", "a general logistics concept")
    if DOMAIN.search(plain):
        return Scope("domain", "", "mentions tracking, returns, incidents or another TrackFlow topic")
    for pattern in _CASUAL:
        if pattern.search(plain):
            return Scope("casual", "small_talk_or_trivia", "small talk or a general question")
    if len(plain.split()) <= _SHORT_TRIVIA_WORDS:
        return Scope("casual", "small_talk_or_trivia", "a short message with no TrackFlow topic")
    return Scope("personal_task", "long_unrelated_request", "a long request with no TrackFlow topic")


def country_in(text: str) -> set[str]:
    plain = _plain(text)
    return {code for code, words in _COUNTRY_WORDS.items() if re.search(rf"(?<![\w'])(?:{words})(?![\w])", plain)}


def detect_policy_mix(text: str, order_country: str | None = None) -> PolicyMix | None:
    """The user asks to apply the return policy (or SLA) of one country to an order of another ("apply Spain's return policy to my order in Los Angeles").

    The order's real country decides which policy applies; asking for another one is refused. `order_country` is the country of the order from the verified registry.
    """
    plain = _plain(text)
    if not _APPLY.search(plain):
        return None
    requested = None
    for code, words in _COUNTRY_WORDS.items():
        if re.search(rf"(?<![\w'])(?:{words})(?:'s|s')?\W+(?:\w+\W+){{0,2}}{_POLICY}", plain) or re.search(rf"{_POLICY}\W+(?:\w+\W+){{0,2}}(?:of|from|in|for)\W+(?:the\W+)?(?:{words})\b", plain):
            requested = code
            break
    if requested is None:
        return None
    mentioned_for_order = None
    before = re.search(rf"\b(?:my|the|this|that|our)\W+({'|'.join(_COUNTRY_WORDS.values())})\W+(?:order|parcel|package|shipment|purchase)\b", plain)  # "my Zaragoza order"
    if before:
        mentioned_for_order = next((code for code, words in _COUNTRY_WORDS.items() if re.fullmatch(words, before.group(1))), None)
    match = re.search(rf"\b(?:my |the )?(?:order|parcel|package|shipment|purchase|return|case)\b[^.?!]{{0,25}}\b(?:in|from|to|shipped to|delivered to|sent to|at)\W+(?:the\W+)?({'|'.join(_COUNTRY_WORDS.values())})\b", plain)
    if match and mentioned_for_order is None:
        mentioned_for_order = next((code for code, words in _COUNTRY_WORDS.items() if re.fullmatch(words, match.group(1))), None)
    actual = order_country or mentioned_for_order
    if actual is None:
        return PolicyMix(requested, None) if re.search(r"\bto (?:my|the|this|that|our)\b", plain) else None  # "apply Spain's policy to my order", with no order to compare
    return PolicyMix(requested, actual) if requested != actual else None


def country_name(code: str | None) -> str:
    return _COUNTRY_NAMES.get(code or "", "the country where the order was shipped")
