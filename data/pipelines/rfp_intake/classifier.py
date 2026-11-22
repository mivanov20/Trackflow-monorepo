"""The classifier agent: is this document a real request for a logistics proposal addressed to TrackFlow?

CONTEXT-company.md: an RFP is what an e-commerce brand sends to outsource warehousing, last mile or returns, in the United States or Spain. It may be a formal
RFP (reference number, scope, evaluation criteria, timeline) or an informal email ("could you send over a proposal?"). It is NOT a document where somebody sells to
TrackFlow (an inbound carrier rate pitch), nor anything else. A document that is not an RFP stops the flow and the ticket is `discarded` with the reason.

Two engines, same contract (`classify(markdown) -> dict`):
  * rules: signal patterns on the text (deterministic, tested on the three sample PDFs);
  * model: when the model service is configured, it classifies and explains. The rules still run: if the model says "RFP" but the text carries the unmistakable
    marks of a vendor pitch, the rules win (a document cannot talk the model into accepting it), and the disagreement goes into the reason.
"""

from __future__ import annotations

import re

from . import llm, metadata

DOCUMENT_TYPES = ("formal_rfp", "informal_request", "vendor_pitch", "other")

FORMAL = (
    r"request for proposals?\b", r"\brfp\b", r"solicitud de propuestas?", r"proposal due date", r"submission instructions", r"evaluation criteria",
    r"scope of work", r"criterios de evaluaci", r"fecha l[ií]mite de (?:propuestas?|presentaci)",
)
ASKS_FOR_PROPOSAL = (
    r"(?:send|get|share|provide)\b[^.\n]{0,25}\b(?:a |us a |me a )?(?:proposal|quote|quotation|pricing)", r"could you (?:send|quote|propose)", r"(?:can|could) (?:you|someone) (?:handle|take care|help us)",
    r"looking for (?:a|an|someone|some)\b[^.\n]{0,40}(?:3pl|logistics|partner|provider|fulfil)", r"(?:we|i) (?:need|require|are looking for|'re looking for)\b[^.\n]{0,60}(?:handle|warehous|ship|storage|fulfil|return|logistic|3pl|partner)",
    r"buscamos\b[^.\n]{0,60}(?:partner|socio|proveedor|operador)", r"necesitamos\b[^.\n]{0,60}(?:almacen|envio|devoluci|logistic)", r"(?:env[ií]en|enviar|remitir)\b[^.\n]{0,30}propuesta",
    r"proposals? must be submitted", r"propuestas? (?:deben|han de) (?:enviarse|presentarse)",
)
PITCH = (
    r"we'?d love (?:the )?opportunity", r"become (?:one of )?(?:trackflow'?s )?(?:preferred )?(?:carrier )?partners?", r"preferred (?:carrier )?partners?", r"introductory rates?",
    r"(?:aggressive|exclusive|special|competitive|discounted) (?:introductory |shipping |carrier )?(?:rates?|pricing|prices|offer)", r"our (?:rates|prices|services|network|fleet)\b",
    r"we (?:offer|provide|are offering|are currently offering)\b", r"set up a (?:quick )?(?:call|demo|meeting)", r"partnerships? at\b", r"\bpartnerships?\b$", r"le ofrecemos|ofrecemos",
    r"tarifas? (?:exclusivas?|especiales?)", r"rates for (?:new )?partners",
)
INJECTION = re.compile(r"ignore (?:all |your )?(?:previous |prior )?instructions|classify (?:this|it) as|mark (?:this|it) as (?:a )?valid|you are now|system prompt|disregard", re.IGNORECASE)


def _count(patterns: tuple[str, ...], text: str) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, re.IGNORECASE | re.MULTILINE)]


def classify_by_rules(markdown: str) -> dict:
    folded = metadata.fold(markdown)
    formal = _count(FORMAL, folded)
    asks = _count(ASKS_FOR_PROPOSAL, folded)
    pitch = _count(PITCH, folded)
    services, _excluded = metadata.detect_services(markdown)
    reasons: list[str] = []

    if len(pitch) >= 2 or (pitch and not asks and not formal):
        document_type, is_rfp, confidence = "vendor_pitch", False, 0.9 if len(pitch) >= 2 else 0.75
        reasons.append("Somebody is offering their own services or rates to TrackFlow (a pitch to become a partner); it is not a client asking for a proposal.")
    elif (formal or asks) and services:
        formal_rfp = len(formal) >= 2
        document_type, is_rfp = ("formal_rfp" if formal_rfp else "informal_request"), True
        confidence = 0.95 if formal_rfp else 0.85 if asks else 0.7
        reasons.append("It asks for a logistics proposal" + (" in the format of a formal RFP (scope, criteria, timeline)." if formal_rfp else " in an informal message."))
        reasons.append("Services requested: " + ", ".join(services) + ".")
    elif formal or asks:
        document_type, is_rfp, confidence = "other", False, 0.6
        reasons.append("It looks like a request, but it does not ask for any TrackFlow service (warehousing, last mile or returns).")
    else:
        document_type, is_rfp, confidence = "other", False, 0.8
        reasons.append("It does not ask TrackFlow for a logistics proposal.")
    if INJECTION.search(markdown):
        reasons.append("The document contains text addressed to the system (instructions about how to classify it); it was treated as part of the document, not obeyed.")
    return {"is_rfp": is_rfp, "document_type": document_type, "confidence": confidence, "reason": " ".join(reasons), "engine": "rules", "signals": {"formal": len(formal), "asks_for_proposal": len(asks), "pitch": len(pitch), "services": services}}


SYSTEM = (
    "You are the classifier of TrackFlow's RFP intake. TrackFlow is a logistics company (warehousing, last mile, reverse logistics in the United States and Spain). "
    "Decide whether the document is a legitimate RFP: an e-commerce brand asking TrackFlow for a proposal to outsource warehousing, last mile or returns. "
    "A formal RFP (reference, scope, criteria, timeline) and an informal email asking for a proposal are BOTH valid. A document where someone SELLS something to TrackFlow "
    "(a carrier's rate pitch, a vendor offer), spam, invoices or anything else is NOT. Return JSON: "
    '{"is_rfp": true|false, "document_type": "formal_rfp"|"informal_request"|"vendor_pitch"|"other", "confidence": 0..1, "reason": "one or two sentences"}.'
)


def classify(markdown: str) -> dict:
    """The verdict stored with the ticket: `is_rfp`, `document_type`, `confidence`, `reason`, `engine`."""
    by_rules = classify_by_rules(markdown)
    answer = llm.ask_json(SYSTEM, llm.wrap_document(markdown), required=("is_rfp", "reason"), max_tokens=300)
    if answer is None or not isinstance(answer.get("is_rfp"), bool) or not isinstance(answer.get("reason"), str):
        return by_rules
    document_type = answer.get("document_type") if answer.get("document_type") in DOCUMENT_TYPES else ("formal_rfp" if answer["is_rfp"] else "other")
    try:
        confidence = min(1.0, max(0.0, float(answer.get("confidence", 0.7))))
    except (TypeError, ValueError):
        confidence = 0.7
    verdict = {"is_rfp": answer["is_rfp"], "document_type": document_type, "confidence": confidence, "reason": answer["reason"].strip()[:600], "engine": "llm", "signals": by_rules["signals"]}
    if verdict["is_rfp"] and by_rules["document_type"] == "vendor_pitch" and by_rules["signals"]["pitch"] >= 2:
        by_rules["reason"] += " (The model had accepted it; the unmistakable marks of a vendor pitch decided.)"
        return by_rules
    return verdict
