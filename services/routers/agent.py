"""`POST /agent/query`: ask the support agent. `GET /agent/runs/{run_id}/trace`: see what it did.

The router has no business logic: it hands the question to the compiled graph (`services/agent`) and returns what the graph produced.
It coexists with `POST /knowledge/query` (the plain RAG endpoint). A failure in any node becomes a clear message plus a reference
(the run id) that finds the trace; the exception, the stack and the service addresses never reach the client.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field

from .. import agent
from ..accounts import User
from ..auth import get_current_user

logger = logging.getLogger("trackflow.agent")
router = APIRouter(prefix="/agent", tags=["agent"])

TRACE_ROLES = ("admin", "manager")
UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
UNAVAILABLE = "The support agent could not answer right now. Please try again in a moment."


class AgentQuestion(BaseModel):
    # No `min_length`: an empty question is the graph's decision (it routes to `reject_empty`), not the schema's.
    question: str = Field(max_length=1000)


class AgentAnswer(BaseModel):
    answer: str
    run_id: str


@router.post("/query", response_model=AgentAnswer)
def ask_agent(body: AgentQuestion, _user: User = Depends(get_current_user)) -> AgentAnswer:
    run_id = str(uuid.uuid4())
    try:
        result = agent.run_agent(body.question, run_id=run_id)
    except Exception as error:  # noqa: BLE001 - a node failed: the trace has the step, the client gets a clear message
        logger.error("agent run %s failed: %s", run_id, type(error).__name__)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{UNAVAILABLE} (reference: {run_id})") from None
    if result.outcome == "empty_question":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=result.answer)
    return AgentAnswer(answer=result.answer, run_id=result.run_id)


@router.get("/runs/{run_id}/trace")
def read_run_trace(run_id: str = Path(pattern=UUID_PATTERN), user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Which nodes ran, in order, with their input and output summaries, and the checkpoints saved. Admin or manager."""
    if user.role not in TRACE_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an admin or a manager can read agent traces.")
    steps = agent.run_trace(run_id)
    if not steps:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No trace found for that run.")
    return {"run_id": run_id, "steps": steps, "checkpoints": agent.checkpoints(run_id)}
