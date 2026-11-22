"""Runs the compiled agent: one run = one thread with its own checkpoints and its own trace, both keyed by `run_id`.

This is the only thing the endpoint calls. Checkpoints (LangGraph + SQLite, or memory) are saved after every node, so a run can be
inspected with `checkpoints()` and, if a node failed, continued with `resume()` from the last good state (retrieval is not
run again when only generation failed).
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from threading import Lock

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import Engine

from .. import database
from .graph import AgentBuildError, build_graph, compile_graph
from .guardrails import events as guard_events
from .guardrails import harness
from .guardrails.session import SessionContext
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
    guardrail: dict | None = None  # {guard, failure_type, action} when a guardrail blocked the request or replaced the answer


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


def _guard_step(engine: Engine, run_id: str, name: str, question: str, output: dict, first_step: int | None = None) -> None:
    """A guardrail that acted leaves a step in the trace of the run, next to the nodes of the graph (a run the harness stopped has only this step)."""
    start = datetime.now(timezone.utc)
    recorder = TraceRecorder(engine, run_id, first_step or (len(read_trace(engine, run_id)) + 1))
    recorder.record(name, {"question": question}, output, start, 0)


def run_agent(question: str, *, graph=None, engine: Engine | None = None, run_id: str | None = None, conversation_id: str | None = None, actor: str | None = None, session: SessionContext | None = None, guardrails: bool = True) -> AgentResult:
    """Runs one question through the harness and the graph and returns the answer. A node failure propagates (the endpoint turns it into a clear error).

    With a `conversation_id` the conversation uses memory: a pending proposal is resolved first, relevant memories are read, and the agent may propose a new one.
    `actor` is the signed-in user: it is what the audit log records as the person who authorised (or refused) a memory.
    `session` is who is asking (the authenticated account and its role): with it, every order or tracking number in the message is checked against the customer's own orders.
    `guardrails=False` runs the bare graph (used by the older unit tests); the API always runs the harness.
    """
    graph, engine, run_id = graph or get_graph(), engine or database.engine, run_id or str(uuid.uuid4())
    context = guard_events.Context(conversation_id=conversation_id, actor=actor, run_id=run_id, engine=engine)
    pre = harness.pre_check(question, session=session, conversation_id=conversation_id, actor=actor, run_id=run_id, engine=engine) if guardrails else None
    if pre is not None and pre.blocked:  # the model is never reached; even the fixed refusal goes through the output guard
        post = harness.post_check(pre.reply, pre.outcome, pre, question=question, session=session, conversation_id=conversation_id, actor=actor, run_id=run_id, engine=engine, trusted=True)
        _guard_step(engine, run_id, "input_guard", question, {"guard": pre.guard, "failure_type": pre.failure_type, "action": "blocked", "outcome": pre.outcome}, first_step=1)
        return AgentResult(run_id=run_id, answer=post.answer, outcome=post.outcome or pre.outcome, conversation_id=conversation_id, guardrail=pre.summary)
    state: dict = {"question": question}
    if conversation_id:
        state.update({"conversation_id": conversation_id, "actor": actor or ""})
    if pre is not None:
        state.update({"verified_facts": pre.verified_facts, "scope_subtype": pre.scope_subtype if pre.scope == "casual" else ""})
    with guard_events.bind(context):
        final = graph.invoke(state, _config(run_id, engine))
    result = _result(run_id, final, engine, conversation_id)
    if pre is None:
        return result
    post = harness.post_check(result.answer, result.outcome, pre, question=question, session=session, conversation_id=conversation_id, actor=actor, run_id=run_id, engine=engine)
    if post.answer == result.answer and not post.outcome:
        return result
    _guard_step(engine, run_id, "output_guard", question, {"guard": post.guard, "failure_type": post.failure_type, "actions": post.actions, "replaced": bool(post.outcome)})
    from dataclasses import replace

    return replace(result, answer=post.answer, outcome=post.outcome or result.outcome, guardrail={"guard": post.guard, "failure_type": post.failure_type, "action": "replaced"} if post.outcome else result.guardrail)


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
