"""Runs the compiled agent: one run = one thread with its own checkpoints and its own trace, both keyed by `run_id`.

This is the only thing the endpoint calls. Checkpoints (LangGraph + SQLite, or memory) are saved after every node, so a run can be
inspected with `checkpoints()` and, if a node failed, continued with `resume()` from the last good state (retrieval is not
run again when only generation failed).
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from threading import Lock

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import Engine

from .. import database
from .graph import AgentBuildError, build_graph, compile_graph
from .trace import TraceRecorder, read_trace, sources_used

CHECKPOINT_DB_ENV = "AGENT_CHECKPOINT_DB"  # a file path: checkpoints survive restarts. Unset: kept in memory only.

_lock = Lock()
_graph = None


@dataclass(frozen=True)
class AgentResult:
    run_id: str
    answer: str
    outcome: str  # generated | no_context | empty_question | needs_ticket_id | tool_unavailable
    sources: tuple[str, ...] = ()  # what the run consulted, in order: tool:ticket_lookup, tool:stock_lookup, rag
    conversation_id: str | None = None
    memory_notice: str | None = None  # what was done with a proposal that was pending (saved, not saved...)
    pending_proposal: dict | None = None  # {id, kind, scope_key, summary} of the proposal this turn put to the user, if any
    memory_used: tuple[str, ...] = ()  # ids of the memories that went into the answer


def make_checkpointer(path: str | None = None):
    path = path if path is not None else os.getenv(CHECKPOINT_DB_ENV)
    if not path:
        return MemorySaver()
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))


def get_graph():
    """The compiled graph, built once. `validate_agent()` calls this at startup so a broken graph stops the API from starting."""
    global _graph
    with _lock:
        if _graph is None:
            _graph = compile_graph(build_graph(), make_checkpointer())
        return _graph


def validate_agent() -> None:
    get_graph()


def _config(run_id: str, engine: Engine, first_step: int = 1) -> dict:
    return {"configurable": {"thread_id": run_id, "recorder": TraceRecorder(engine, run_id, first_step)}}


def _result(run_id: str, final: dict, engine: Engine, conversation_id: str | None) -> AgentResult:
    answer, notice = final["answer"], final.get("memory_notice")
    if notice and final["outcome"] != "memory_decision":  # the user answered the proposal AND asked something: what was done first, then the answer
        answer = f"{notice}\n\n{answer}"
    proposal = final.get("memory_proposal") or {}
    return AgentResult(
        run_id=run_id, answer=answer, outcome=final["outcome"], sources=tuple(sources_used(read_trace(engine, run_id))), conversation_id=conversation_id,
        memory_notice=notice, pending_proposal=proposal if proposal.get("id") else None, memory_used=tuple(final.get("memories_used") or ()),
    )


def run_agent(question: str, *, graph=None, engine: Engine | None = None, run_id: str | None = None, conversation_id: str | None = None, actor: str | None = None) -> AgentResult:
    """Runs the graph for one question and returns the answer. A node failure propagates (the endpoint turns it into a clear error).

    With a `conversation_id` the conversation uses memory: a pending proposal is resolved first, relevant memories are read, and the agent may propose a new one.
    `actor` is the signed-in user: it is what the audit log records as the person who authorised (or refused) a memory.
    """
    graph, engine, run_id = graph or get_graph(), engine or database.engine, run_id or str(uuid.uuid4())
    state: dict = {"question": question}
    if conversation_id:
        state.update({"conversation_id": conversation_id, "actor": actor or ""})
    final = graph.invoke(state, _config(run_id, engine))
    return _result(run_id, final, engine, conversation_id)


def resume(run_id: str, *, graph=None, engine: Engine | None = None) -> AgentResult:
    """Continues a run that stopped at a failed node, from its last checkpoint. The nodes that already ran are not run again."""
    graph, engine = graph or get_graph(), engine or database.engine
    done = len(read_trace(engine, run_id))
    final = graph.invoke(None, _config(run_id, engine, first_step=done + 1))
    return _result(run_id, final, engine, final.get("conversation_id"))


def checkpoints(run_id: str, *, graph=None) -> list[dict]:
    """The saved states of a run, oldest first: after which step, what ran last (`writes`) and what runs next."""
    graph = graph or get_graph()
    history = list(graph.get_state_history({"configurable": {"thread_id": run_id}}))
    return [{"step": s.metadata.get("step"), "source": s.metadata.get("source"), "next": list(s.next), "state_keys": sorted(k for k, v in s.values.items() if v not in (None, "", []))} for s in reversed(history)]


def run_trace(run_id: str, *, engine: Engine | None = None) -> list[dict]:
    return read_trace(engine or database.engine, run_id)


__all__ = ["AgentBuildError", "AgentResult", "checkpoints", "get_graph", "make_checkpointer", "resume", "run_agent", "run_trace", "validate_agent"]
