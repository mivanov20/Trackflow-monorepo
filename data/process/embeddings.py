"""`embed()`: text -> vector with the dedicated EMBEDDINGS model.

The same function embeds the chunks at index time (`setup()`) and the question at query time (`retrieve()`): if they used
different preprocessing or models the two vectors would not live in the same space. It never touches the generation model
(`LLM_MODEL`, used only by `generate_answer()`): different API, output shape, latency, cost and failure modes.
"""

from __future__ import annotations

import re

from data.process import model_client
from data.process import rag_config as config


def normalize(text: str) -> str:
    """The only preprocessing: collapse runs of whitespace (hard-wrapped lines, double spaces). Case and punctuation are kept."""
    return re.sub(r"\s+", " ", text).strip()


def embed(text: str) -> list[float]:
    """Vector of one text, from the embeddings model `EMBEDDING_MODEL`."""
    model = config.embedding_model()
    if not model:
        raise model_client.ModelServiceError("The embeddings model is not configured: set EMBEDDING_MODEL.")
    if model == config.generation_model():
        raise model_client.ModelServiceError("EMBEDDING_MODEL and LLM_MODEL must be different models: one embeds, the other generates.")
    cleaned = normalize(text)
    if not cleaned:
        raise ValueError("cannot embed an empty text")
    body = model_client.post("/embeddings", {"model": model, "input": cleaned})
    try:
        vector = body["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError):
        raise model_client.ModelServiceError("The embeddings response has no vector.") from None
    if not vector or not all(isinstance(x, (int, float)) for x in vector):
        raise model_client.ModelServiceError("The embeddings response has no usable vector.")
    return [float(x) for x in vector]
