"""`setup()`: reads the source documents, chunks them, embeds the chunks and loads them into the Qdrant collection.

Idempotent by DETERMINISTIC POINT IDS (not by clearing): each chunk's id is a uuid5 of `company/source_document/chunk_index`, so
running `setup()` again upserts the same points over themselves instead of duplicating them, and points whose chunk no
longer exists (a document got shorter) are deleted. The collection is only recreated when the vector size or the distance no
longer matches (the embeddings model changed), because vectors of different models cannot be mixed.
"""

from __future__ import annotations

import logging
from typing import Callable

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointIdsList, PointStruct, VectorParams

from data.process import embeddings, rag_chunking
from data.process import rag_config as config

logger = logging.getLogger("trackflow.rag")
DISTANCE = Distance.COSINE


def get_client() -> QdrantClient:
    return QdrantClient(url=config.qdrant_url(), timeout=10)


def _ensure_collection(client: QdrantClient, name: str, size: int) -> str:
    """Creates the collection, or recreates it when its vectors no longer fit. Returns what it did."""
    if client.collection_exists(name):
        params = client.get_collection(name).config.params.vectors
        if getattr(params, "size", None) == size and getattr(params, "distance", None) == DISTANCE:
            return "reused"
        client.delete_collection(name)
        action = "recreated"
    else:
        action = "created"
    client.create_collection(name, vectors_config=VectorParams(size=size, distance=DISTANCE))
    return action


def setup(client: QdrantClient | None = None, *, directory=None, embed_fn: Callable[[str], list[float]] = embeddings.embed) -> dict:
    """Indexes `docs/company-knowledge-base/` into the `trackflow_knowledge` collection. Safe to run any number of times."""
    client = client or get_client()
    chunks = rag_chunking.load_chunks(directory)
    vectors = [embed_fn(chunk.embedding_text) for chunk in chunks]  # all embedded before touching Qdrant: a model failure changes nothing
    action = _ensure_collection(client, config.COLLECTION, len(vectors[0]))
    points = [PointStruct(id=chunk.point_id, vector=vector, payload=chunk.payload()) for chunk, vector in zip(chunks, vectors)]
    client.upsert(config.COLLECTION, points=points, wait=True)

    keep = {chunk.point_id for chunk in chunks}
    stored = [str(point.id) for point in client.scroll(config.COLLECTION, limit=10_000, with_payload=False, with_vectors=False)[0]]
    stale = [point_id for point_id in stored if point_id not in keep]
    if stale:
        client.delete(config.COLLECTION, points_selector=PointIdsList(points=stale), wait=True)

    per_document: dict[str, int] = {}
    for chunk in chunks:
        per_document[chunk.source_document] = per_document.get(chunk.source_document, 0) + 1
    summary = {"collection": config.COLLECTION, "collection_action": action, "chunks": len(chunks), "stale_deleted": len(stale), "vector_size": len(vectors[0]), "distance": DISTANCE.value, "chunks_per_document": per_document}
    logger.info("rag setup: %s", summary)
    return summary
