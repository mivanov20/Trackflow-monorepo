"""`POST /agent/query`: ask the support agent. `GET /agent/runs/{run_id}/trace`: see what it did.

The router has no business logic: it hands the question to the compiled graph (`services/agent`) and returns what the graph produced.
It coexists with `POST /knowledge/query` (the plain RAG endpoint). A failure in any node becomes a clear message plus a reference
(the run id) that finds the trace; the exception, the stack and the service addresses never reach the client.
"""

import logging
from datetime import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field

from .. import agent
from ..agent.guardrails import events as guardrail_events
from ..agent.guardrails.session import SessionContext
from ..agent.trace import sources_used
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
    # Send the same id on every message of a conversation to use memory: the agent may then propose to remember something, and your next message answers it.
    # Without it the agent keeps nothing and proposes nothing.
    conversation_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$")


class AgentAnswer(BaseModel):
    answer: str
    run_id: str
    sources: list[str] = Field(default_factory=list)  # what the agent used, in order: tool:ticket_lookup, tool:stock_lookup, rag
    conversation_id: str | None = None
    outcome: str = "generated"  # generated | no_context | tool_unavailable | memory_decision | blocked_* (a guardrail refused) ...
    guardrail: dict | None = None  # {guard, failure_type, action} when a guardrail blocked the request or replaced the answer
    memory: dict | None = None  # {"notice": what was done with a pending proposal, "pending_proposal": {id, kind, summary} if this answer asks to remember something, "used": ids of memories used}


@router.post("/query", response_model=AgentAnswer)
def ask_agent(body: AgentQuestion, user: User = Depends(get_current_user)) -> AgentAnswer:
    run_id = str(uuid.uuid4())
    try:
        result = agent.run_agent(body.question, run_id=run_id, conversation_id=body.conversation_id, actor=user.id, session=SessionContext(customer_id=user.id, role=getattr(user.role, "value", str(user.role))))
    except Exception as error:  # noqa: BLE001 - a node failed: the trace has the step, the client gets a clear message
        logger.error("agent run %s failed: %s", run_id, type(error).__name__)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{UNAVAILABLE} (reference: {run_id})") from None
    if result.outcome == "empty_question":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=result.answer)
    memory = None
    if result.conversation_id:
        memory = {"notice": result.memory_notice, "pending_proposal": ({k: result.pending_proposal[k] for k in ("id", "kind", "summary")} if result.pending_proposal else None), "used": list(result.memory_used)}
    return AgentAnswer(answer=result.answer, run_id=result.run_id, sources=list(result.sources), conversation_id=result.conversation_id, memory=memory, outcome=result.outcome, guardrail=result.guardrail)


@router.get("/runs/{run_id}/trace")
def read_run_trace(run_id: str = Path(pattern=UUID_PATTERN), user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Which nodes ran, in order, with their input and output summaries, and the checkpoints saved. Admin or manager."""
    if user.role not in TRACE_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an admin or a manager can read agent traces.")
    steps = agent.run_trace(run_id)
    if not steps:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No trace found for that run.")
    return {"run_id": run_id, "sources_used": sources_used(steps), "steps": steps, "checkpoints": agent.checkpoints(run_id)}


# ----------------------------------------------------------------------------- the memory, readable and correctable by the people who own it
def _require_memory_role(user: User) -> None:
    if user.role not in TRACE_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an admin or a manager can read or change the agent's memory.")


@router.get("/memory")
def read_memory(user: User = Depends(get_current_user)) -> dict[str, Any]:
    """What the agent remembers now (active entries), filed by carrier + country or client. Admin or manager."""
    _require_memory_role(user)
    return {"entries": agent.memory_entries()}


@router.get("/memory/audit")
def read_memory_audit(limit: int = 100, conversation_id: str | None = None, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Every memory proposal and what became of it: who decided, when, approved or not. Admin or manager."""
    _require_memory_role(user)
    return {"proposals": agent.memory_audit(limit=max(1, min(limit, 500)), conversation_id=conversation_id)}


@router.delete("/memory/{entry_id}")
def forget_memory(entry_id: str = Path(pattern=UUID_PATTERN), user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Selective forgetting: the entry stops being used at once (and is purged later). The proposal that created it stays in the audit log. Admin or manager."""
    _require_memory_role(user)
    if not agent.forget_memory(entry_id, actor=user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active memory with that id.")
    return {"forgotten": entry_id}


# ----------------------------------------------------------------------------- observability of the guardrails
@router.get("/guardrails/summary")
def guardrail_summary(since: str | None = None, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """How many times each guardrail was triggered (optionally since an ISO timestamp), by guard, type of failure (structural, content, security) and action. Admin or manager."""
    if user.role not in TRACE_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an admin or a manager can read the guardrail summary.")
    moment = None
    if since:
        try:
            moment = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`since` must be an ISO 8601 timestamp, for example 2026-11-22T10:00:00Z.") from None
    return guardrail_events.summary(since=moment)


@router.get("/guardrails/events")
def guardrail_event_log(limit: int = 50, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """The latest guardrail events, newest first (each with its type of failure and a short redacted excerpt). Admin or manager."""
    if user.role not in TRACE_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an admin or a manager can read the guardrail events.")
    return {"events": guardrail_events.recent(limit=max(1, min(limit, 200)))}
