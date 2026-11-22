"""Settings of the TrackFlow knowledge base (RAG). Values come from CONTEXT-company.md (milestone 7) or the environment.

Nothing here is a secret: the key of the model service is read from `LLM_API_KEY` at call time and never logged.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# --- CONTEXT-company.md ---
COMPANY = "trackflow"
LANGUAGE = "en"
COLLECTION = "trackflow_knowledge"  # the exact name CONTEXT requires in setup()
KNOWLEDGE_BASE_DIR = ROOT / "docs" / "company-knowledge-base"
# file -> the `source_document` value of the payload (CONTEXT section 3)
SOURCE_DOCUMENTS = {
    "trackflow-sla-delivery.en.md": "sla-delivery",
    "trackflow-returns-policy.en.md": "returns-policy",
    "trackflow-carrier-coverage.en.md": "carrier-coverage",
    "trackflow-storage-pricing.en.md": "storage-pricing",
}
MIN_CHUNKS_PER_DOCUMENT = 3  # CONTEXT section 5

# The documents have no subheadings (one `#` title each), so the `section` of a chunk is the title plus a short subtitle that names
# the idea the chunk holds. Keyed by (source_document, chunk_index); a chunk without an entry gets a label derived from its text.
SECTION_TITLES = {
    ("sla-delivery", 0): "Service types and delivery times",
    ("sla-delivery", 1): "Contractual SLA and compensation",
    ("sla-delivery", 2): "High-demand dates (no SLA guarantee)",
    ("returns-policy", 0): "Standard return process",
    ("returns-policy", 1): "Standard return window",
    ("returns-policy", 2): "Return costs",
    ("returns-policy", 3): "International returns (manual handling)",
    ("returns-policy", 4): "What TrackFlow does not do (reconditioning and resale)",
    ("carrier-coverage", 0): "United States carriers (Los Angeles)",
    ("carrier-coverage", 1): "Spain carriers (Zaragoza)",
    ("carrier-coverage", 2): "Carrier selection and exceptions",
    ("storage-pricing", 0): "Standard storage rates and long-term fee",
    ("storage-pricing", 1): "Inventory aging report",
    ("storage-pricing", 2): "Preferential rates (Miguel Torres approval)",
}

# --- retrieval ---
TOP_K = 5
# Chunks scoring below this cosine similarity are dropped, even if that leaves fewer than k (or none). The value depends on the
# embeddings model: `scripts/evaluate_rag.py` measures the scores of the right chunks against off-topic questions and
# recommends one; set `RAG_MIN_SCORE` to override. See docs/rag/rag-design.md.
DEFAULT_MIN_SCORE = 0.35


def min_score() -> float:
    return float(os.getenv("RAG_MIN_SCORE", DEFAULT_MIN_SCORE))


# --- models: two different jobs, two different model ids (the models the academy gives students access to, such as DeepSeek or a GPT model, through its gateway; an OpenAI-compatible HTTP API) ---
def llm_api_url() -> str:
    return os.getenv("LLM_API_URL", "").rstrip("/")


def llm_api_key() -> str:
    return os.getenv("LLM_API_KEY", "")


def embedding_model() -> str:
    return os.getenv("EMBEDDING_MODEL", "")


def generation_model() -> str:
    return os.getenv("LLM_MODEL", "")


REQUEST_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))


def qdrant_url() -> str:
    return os.getenv("QDRANT_URL", "http://localhost:6333")
