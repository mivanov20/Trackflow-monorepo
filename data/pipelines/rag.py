"""Question answering over the TrackFlow knowledge base: `retrieve()` + `generate_answer()`, composed by `query()`.

Each step is its own function so any piece can be swapped without touching the others (and the LangGraph agent of a later
milestone can call `retrieve()` and `generate_answer()` as separate steps):

    embed()            data/process/embeddings.py     text -> vector (embeddings model)
    setup()            data/process/knowledge_base.py documents -> chunks -> vectors -> Qdrant
    retrieve()         here                           question -> the chunks that clear `min_score`
    generate_answer()  here                           question + chunks -> the answer (generation model)
    query()            here                           retrieve() + generate_answer(): the only entry point for consumers

The answer is ALWAYS written by the generation model from the retrieved context. Search results (chunks, scores) are never
returned to a caller; they are only logged on the server.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Callable

from qdrant_client import QdrantClient

from data.process import embeddings, knowledge_base, model_client
from data.process import rag_config as config

logger = logging.getLogger("trackflow.rag")

# --- the voice and audience of CONTEXT-company.md (milestone 7) and its business constraints ---
COMMERCIAL_INTRO = """You are TrackFlow's commercial assistant. Your reader is a TrackFlow account manager or business developer who is on a call, or about to answer an email, with a prospect or a current client brand. Answer the way a good TrackFlow salesperson would on that call: clear, professional, confident, with exact data, and never promising terms that do not exist in the standard agreements."""

# The answer rules are shared: another agent with another identity (the support agent) reuses them unchanged.
ANSWER_RULES = """Rules:
1. Use ONLY the context below. It is the only source of company facts you have. If the context does not answer the question, say plainly that the knowledge base has no information about it and that the account manager should confirm it with the responsible area. Never guess, never fill the gap with general knowledge about logistics.
2. Copy numbers exactly: percentages, prices, currencies, days, hours, volumes and names must match the context word for word. Do not round, convert or extrapolate.
3. Delivery SLA: never promise or guarantee a delivery SLA or delivery time for the declared high-demand dates (Black Friday, Christmas, January Sales in Spain). Say it is not guaranteed on those dates, that times can extend, and that the client must be warned in advance.
4. International returns are never "automatic": always say they require manual handling by the Returns team.
5. Storage discounts: never offer or imply a discount. A preferential rate is only a possibility to negotiate and always requires Miguel Torres's approval. Carrier exceptions require the approval of Carlos Vega.
6. If the question asks for a condition, discount or exception that the context does not document, answer that it requires approval and do not invent a condition.
7. Context blocks whose Source is incident-manager or inventory-manager are LIVE data, read from that system a moment ago: give their values exactly and say they are current as of now. If a live block says its lookup could not be completed, say you could not confirm it right now; never guess a ticket status or a stock number.
8. Answer in English, in a short paragraph (or a short list when the question asks for several items), written to be said to a client. End with one line "Source:" naming the document(s) and section(s) you used, taken from the context headers. If you used no context, do not write a Source line.
"""

SYSTEM_PROMPT = COMMERCIAL_INTRO + "\n\n" + ANSWER_RULES

# Two seams the agent's harness uses (set from services/agent/guardrails; the RAG endpoint is hardened by the same hooks when they are set):
#  * the context filter: every retrieved or tool-provided text passes through it before it is placed in a prompt (isolation of untrusted content);
#  * the active system prompt: the agent sets its secure prompt for the duration of a call; without it, `SYSTEM_PROMPT` above is used.
active_system_prompt: ContextVar[str | None] = ContextVar("rag_active_system_prompt", default=None)
_context_filter: Callable[[str, str], str] | None = None


def set_context_filter(function: Callable[[str, str], str] | None) -> None:
    global _context_filter
    _context_filter = function


NO_CONTEXT_NOTE = "There is NO relevant information in the knowledge base for this question. Follow rule 1: say that you do not have that information and who should confirm it. Do not answer the question itself."


# ----------------------------------------------------------------------------- retrieve
def retrieve(query: str, *, k: int = config.TOP_K, min_score: float | None = None, client: QdrantClient | None = None, embed_fn: Callable[[str], list[float]] = embeddings.embed) -> list[dict]:
    """Embeds the question, finds the `k` nearest chunks and returns only those scoring at least `min_score`.

    Returns plain dicts (the payload plus `score`), never Qdrant objects. It can return fewer than `k` chunks, or none: forcing
    a bad match into the prompt is worse than admitting that nothing was found. `min_score=None` uses `RAG_MIN_SCORE`.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    threshold = config.min_score() if min_score is None else min_score
    vector = embed_fn(query)
    client = client or knowledge_base.get_client()
    hits = client.query_points(config.COLLECTION, query=vector, limit=k, with_payload=True).points
    return [{**(hit.payload or {}), "score": float(hit.score)} for hit in hits if hit.score >= threshold]


# ----------------------------------------------------------------------------- generate_answer
def build_messages(question: str, context: list[dict]) -> list[dict]:
    """Prompt assembly: the rules, the retrieved chunks with their source headers, and the question."""
    if context:
        def text_of(chunk: dict) -> str:
            return _context_filter(chunk["text"], chunk["source_document"]) if _context_filter else chunk["text"]

        blocks = [f"[{index}] Source: {chunk['source_document']} | Section: {chunk['section']}\n{text_of(chunk)}" for index, chunk in enumerate(context, start=1)]
        context_text = "\n\n".join(blocks)
    else:
        context_text = NO_CONTEXT_NOTE
    user = f"Context:\n{context_text}\n\nQuestion: {question}"
    return [{"role": "system", "content": active_system_prompt.get() or SYSTEM_PROMPT}, {"role": "user", "content": user}]


# Who wants the answer AS IT IS GENERATED (the chat over WebSocket). An object with `on_token(text)` and, optionally, `on_open(close)`; none by default, and then nothing changes.
token_sink: ContextVar = ContextVar("rag_token_sink", default=None)


def _stream_answer(model: str, messages: list[dict], sink) -> str:
    """The same model call, streamed: every piece of text goes to the sink as it arrives. If the sink raises (the generation was cancelled) the connection to the model is closed
    at once and the exception goes on: no further text is requested."""
    pieces: list[str] = []
    iterator = model_client.stream_chat({"model": model, "messages": messages, "temperature": 0, "max_tokens": 500}, on_open=getattr(sink, "on_open", None))
    try:
        for text in iterator:
            sink.on_token(text)
            pieces.append(text)
    finally:
        iterator.close()
    answer = "".join(pieces).strip()
    if not answer:
        raise model_client.ModelServiceError("The generation model returned an empty answer.")
    return answer


def generate_answer(question: str, context: list[dict]) -> str:
    """Asks the GENERATION model (a chat model, never the embeddings model) to answer from `context`. With no context it still
    answers, honestly: that the knowledge base has no information."""
    model = config.generation_model()
    if not model:
        raise model_client.ModelServiceError("The generation model is not configured: set LLM_MODEL.")
    sink = token_sink.get()
    if sink is not None:
        return _stream_answer(model, build_messages(question, context), sink)
    body = model_client.post("/chat/completions", {"model": model, "messages": build_messages(question, context), "temperature": 0, "max_tokens": 500})
    try:
        answer = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise model_client.ModelServiceError("The generation response has no answer.") from None
    if not isinstance(answer, str) or not answer.strip():
        raise model_client.ModelServiceError("The generation model returned an empty answer.")
    return answer.strip()


# ----------------------------------------------------------------------------- query
def query(question: str) -> str:
    """The only function consumers call: `retrieve()` then `generate_answer()`. Returns the model's answer."""
    question = question.strip()
    if not question:
        raise ValueError("the question is empty")
    context = retrieve(question)
    logger.info("rag query: %d chunk(s) above the threshold %s", len(context), [(c["source_document"], c["chunk_index"], round(c["score"], 3)) for c in context])
    return generate_answer(question, context)
