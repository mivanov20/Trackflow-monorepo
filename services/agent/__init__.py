"""The TrackFlow support agent: the RAG pipeline as an explicit, compiled, checkpointed and traced LangGraph graph."""

from .graph import AgentBuildError
from .runner import AgentResult, checkpoints, get_graph, resume, run_agent, run_trace, validate_agent

__all__ = ["AgentBuildError", "AgentResult", "checkpoints", "get_graph", "resume", "run_agent", "run_trace", "validate_agent"]


def memory_entries() -> list[dict]:
    from .memory.store import get_store

    return get_store().entries("active")


def memory_audit(limit: int = 100, conversation_id: str | None = None) -> list[dict]:
    from .memory.store import get_store

    return get_store().audit(limit, conversation_id)


def forget_memory(entry_id: str, *, actor: str | None = None) -> bool:
    from .memory.store import get_store

    return get_store().forget(entry_id, actor=actor)
