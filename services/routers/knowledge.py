"""`POST /knowledge/query`: ask the TrackFlow knowledge base a question in natural language.

This router only receives the request and returns the answer: retrieval and generation live in `data/pipelines/rag.py`, which is
imported, not copied. The response is `{"answer": "..."}`, written by the generation model; the chunks the search found and
their similarity scores are never sent to the client (they are logged on the server by the pipeline).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from data.pipelines import rag

from ..accounts import User
from ..agent.guardrails import events, injection, messages
from ..agent.guardrails.sanitize import InputRejected, sanitize
from ..auth import get_current_user
from ..ratelimit import limit_model_calls

logger = logging.getLogger("trackflow.knowledge")
router = APIRouter(prefix="/knowledge", tags=["knowledge"])

UNAVAILABLE = "The knowledge assistant is temporarily unavailable. Please try again in a moment."


class KnowledgeQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=1000)

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("The question cannot be empty.")
        return value


class KnowledgeAnswer(BaseModel):
    answer: str


@router.post("/query", response_model=KnowledgeAnswer)
def ask_knowledge_base(body: KnowledgeQuestion, user: User = Depends(limit_model_calls)) -> KnowledgeAnswer:
    """Answers a question about delivery SLA, returns, carrier coverage or storage pricing, from the indexed documents."""
    try:
        cleaned = sanitize(body.question)
    except InputRejected as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from None
    if injection.detect(cleaned.text):  # this endpoint reaches the model without the agent, so it gets the same input guard
        events.record("instruction_change", "security", "blocked", "knowledge endpoint", text=cleaned.text, context=events.Context(actor=user.id))
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=messages.INSTRUCTION_CHANGE)
    try:
        answer = rag.query(cleaned.text)
    except Exception as error:  # noqa: BLE001 - the vector database, the embeddings model or the generation model failed
        logger.error("knowledge query failed: %s", type(error).__name__)  # never the question, the key or the service address
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=UNAVAILABLE) from None
    return KnowledgeAnswer(answer=answer)
