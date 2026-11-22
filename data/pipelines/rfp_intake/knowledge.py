"""The generators' access to TrackFlow's knowledge base (optional improvement of Part 2): real policies and figures instead of improvised ones.

`lookup(query)` returns the chunks of `docs/company-knowledge-base/` that answer a query. With the semantic knowledge base up (Qdrant + embeddings, the RAG of Milestone 7) and
`RFP_KB_MODE` not `local`, it uses `rag.retrieve`; in every other case, or if that fails, it falls back to a lexical search over the same documents, so drafting does not depend on
a vector database being up. `facts(country)` turns the relevant chunks into the few figures a proposal may state, each with its source document. A figure that is not in the knowledge
base is NOT a fact: the generators leave it as an item to confirm with the owner.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import llm
from .config import ROOT

logger = logging.getLogger("trackflow.rfp_intake")

KB_DIR = ROOT / "docs" / "company-knowledge-base"
STOP = {"the", "a", "of", "and", "to", "in", "for", "is", "on", "by", "per", "or", "an", "what", "how", "does", "do"}


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9%]+", text.lower()) if word not in STOP and len(word) > 1}


@lru_cache(maxsize=1)
def _local_chunks() -> tuple[dict, ...]:
    """Paragraphs of the English documents, each with its source name (the same names the RAG uses: `sla-delivery`, `storage-pricing`...)."""
    chunks = []
    for path in sorted(KB_DIR.glob("*.en.md")):
        source = re.sub(r"^trackflow-|\.en\.md$", "", path.name)
        for paragraph in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")):
            text = re.sub(r"\s+", " ", paragraph).strip()
            if text and not text.startswith("# "):
                chunks.append({"source_document": source, "text": text})
    return tuple(chunks)


def lookup_local(query: str, k: int = 4) -> list[dict]:
    wanted = _tokens(query)
    scored = []
    for chunk in _local_chunks():
        overlap = len(wanted & _tokens(chunk["text"] + " " + chunk["source_document"].replace("-", " ")))
        if overlap:
            scored.append((overlap, chunk))
    scored.sort(key=lambda item: -item[0])
    return [{**chunk, "score": float(score)} for score, chunk in scored[:k]]


def semantic_enabled() -> bool:
    return os.getenv("RFP_KB_MODE", "auto").lower() != "local" and llm.configured()


def lookup(query: str, k: int = 4) -> list[dict]:
    if semantic_enabled():
        try:
            from data.pipelines import rag

            hits = rag.retrieve(query, k=k)
            if hits:
                return hits
        except Exception as error:  # noqa: BLE001 - the vector database or the embeddings model is down: the local documents answer
            logger.warning("rfp drafting: semantic knowledge base unavailable (%s); using the local documents", type(error).__name__)
    return lookup_local(query, k)


@dataclass
class Facts:
    """What the knowledge base says about the client's country. Every value is `None` when the documents do not state it."""

    country: str | None = None
    sla_percent: float | None = None
    sla_period: str | None = None
    sla_compensation_percent: float | None = None
    storage_rate: float | None = None
    storage_currency: str | None = None
    storage_location: str | None = None
    storage_grace_days: int | None = None
    long_term_days: int | None = None
    long_term_surcharge_percent: float | None = None
    return_window_days: int | None = None
    carriers: list[str] = field(default_factory=list)
    carrier_notes: dict[str, str] = field(default_factory=dict)
    standard_shipping: str | None = None
    express_shipping: str | None = None
    high_demand_notice: bool = False
    sources: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)

    def numbers(self) -> set[str]:
        """Every number of the retrieved text: figures a draft may repeat without having invented them."""
        found = set()
        for text in self.texts:
            found.update(re.sub(r"[.,\s]", "", number) for number in re.findall(r"\d[\d.,]*", text))
        return found


STORAGE_LOCATION = {"US": "Los Angeles", "ES": "Zaragoza"}
STORAGE_CURRENCY = {"US": "USD", "ES": "EUR"}


def facts(country: str | None, search=lookup) -> Facts:
    """The figures a proposal for a client of `country` may state. `search` is injectable so tests can simulate a knowledge base without the document."""
    result = Facts(country=country)
    queries = ["delivery SLA on-time deliveries committed", "storage rate cubic meter grace period long-term inventory", "return window days from delivery", "carriers coverage by country"]
    seen: set[str] = set()
    for query in queries:
        for chunk in search(query):
            text = chunk["text"]
            if text in seen:
                continue
            seen.add(text)
            result.texts.append(text)
            if chunk["source_document"] not in result.sources:
                result.sources.append(chunk["source_document"])
    blob = "\n".join(result.texts)
    if match := re.search(r"(\d{1,3})%\s+on-time deliveries(?:\s+per\s+(month))?", blob):
        result.sla_percent, result.sla_period = float(match.group(1)), match.group(2) or "month"
    if match := re.search(r"entitled to a (\d{1,2})% compensation", blob):
        result.sla_compensation_percent = float(match.group(1))
    currency = STORAGE_CURRENCY.get(country or "")
    place = STORAGE_LOCATION.get(country or "")
    if currency and place and (match := re.search(rf"(\d+(?:\.\d+)?)\s+{currency}\s+per\s+cubic\s+meter\s+per\s+month\s+\({place}\)", blob)):
        result.storage_rate, result.storage_currency, result.storage_location = float(match.group(1)), currency, place
    if match := re.search(r"first (\d+) days of any new product", blob):
        result.storage_grace_days = int(match.group(1))
    if match := re.search(r"more than (\d+) days of no movement", blob):
        result.long_term_days = int(match.group(1))
    if match := re.search(r"(\d+)% over the standard rate", blob):
        result.long_term_surcharge_percent = float(match.group(1))
    if match := re.search(r"return window:\s*(\d+) days from delivery", blob, re.IGNORECASE):
        result.return_window_days = int(match.group(1))
    result.high_demand_notice = bool(re.search(r"high-demand dates", blob))
    if match := re.search(r"Standard shipping:\s*([^.]*?)\s*(?:\.|- Express)", blob):
        result.standard_shipping = match.group(1)
    if match := re.search(r"Express shipping:\s*([^.]*?(?:\([^)]*\))?)\s*(?:\.|- International)", blob):
        result.express_shipping = match.group(1)
    result.carriers, result.carrier_notes = _carriers(blob, country)
    return result


def _carriers(blob: str, country: str | None) -> tuple[list[str], dict[str, str]]:
    """The carriers the knowledge base lists for the country, with what each one is good for (no rates: the documents have none, and none may be quoted)."""
    marker = {"US": r"United States \(Los Angeles\):\s*([^-]*?)\.", "ES": r"Spain \(Zaragoza\):\s*(.*?)(?:\.\s*-|\.\s+Carrier selection|$)"}.get(country or "")
    if not marker:
        return [], {}
    match = re.search(marker, blob, re.DOTALL)
    if not match:
        return [], {}
    names = [name for name in re.findall(r"UPS|FedEx|DHL|MRW|SEUR", match.group(1))]
    notes = {}
    for name in dict.fromkeys(names):
        note = re.search(rf"{name}:\s*([^.]*)\.", blob)
        if note:
            notes[name] = note.group(1).strip()
    return list(dict.fromkeys(names)), notes
