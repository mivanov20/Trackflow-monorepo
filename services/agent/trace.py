"""The trace of a run: which nodes ran, in what order, and what each one received and produced.

It is stored in the database (table `agent_trace_steps`), not printed: a trace is queried after the run with `read_trace(run_id)`.
A node writes its row when it ends, successfully or not, so a run that failed half way still has the steps that happened.
Summaries keep the trace small and free of company text: the retrieved chunks are listed by source, section, index and score.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from ..models import AgentTraceStep
from .memory import policy

QUESTION_PREVIEW = 500
ANSWER_PREVIEW = 2000


def summarize(values: dict[str, Any] | None) -> dict[str, Any]:
    """What goes in the trace for a node input / output: plain, bounded, JSON-safe."""
    summary: dict[str, Any] = {}
    for key, value in (values or {}).items():
        if key == "context":
            summary["context"] = [{"source_document": c.get("source_document"), "section": c.get("section"), "chunk_index": c.get("chunk_index"), "score": round(float(c.get("score", 0.0)), 4)} for c in value]
        elif key == "live_data":
            # what each tool was asked, how it ended, and a few fields of the record: never the free text of a ticket
            summary["live_data"] = [{"tool": r["tool"], "status": r["status"], "input": r["input"], "message": r.get("message", ""), "summary": r.get("summary", {})} for r in value]
        elif key == "question":
            summary["question"] = policy.redact(str(value), QUESTION_PREVIEW)  # a question can carry an address or a tracking number: it never reaches the trace as such
        elif key == "answer":
            summary["answer"] = policy.redact(str(value), ANSWER_PREVIEW)
        elif key == "memory_candidate":  # what the model suggested is not logged here (it may be forbidden text): only its kind
            summary["memory_candidate"] = {"kind": value.get("kind")} if value else {}
        elif key == "memory_proposal":
            summary["memory_proposal"] = {k: v for k, v in value.items() if k != "summary"}
        else:
            summary[key] = value
    return summary


class TraceRecorder:
    """Writes one row per node of one run. `run_id` is also the LangGraph thread id, so trace and checkpoints share a key."""

    def __init__(self, engine: Engine, run_id: str, first_step: int = 1):
        self.engine, self.run_id, self.step = engine, run_id, first_step - 1

    def record(self, node: str, input_values: dict, output_values: dict | None, started_at: datetime, duration_ms: int, error: str | None = None) -> None:
        self.step += 1
        row = AgentTraceStep(run_id=self.run_id, step=self.step, node=node, status="error" if error else "ok", started_at=started_at, duration_ms=duration_ms, input=summarize(input_values), output=summarize(output_values), error=error)
        with Session(self.engine) as session:
            session.add(row)
            session.commit()


def traced(name: str, function):
    """Wraps a node so it records its step. The node itself stays a plain `state -> update` function (testable alone)."""

    def wrapper(state, config):
        recorder = ((config or {}).get("configurable") or {}).get("recorder")
        started_at, clock = datetime.now(timezone.utc), time.perf_counter()
        try:
            update = function(state)
        except Exception as error:  # noqa: BLE001 - recorded, then raised again: the run fails, the trace says where
            if recorder:
                recorder.record(name, state, None, started_at, int((time.perf_counter() - clock) * 1000), error=f"{type(error).__name__}: {str(error)[:300]}")
            raise
        if recorder:
            recorder.record(name, state, update, started_at, int((time.perf_counter() - clock) * 1000))
        return update

    wrapper.__name__ = name
    return wrapper


def read_trace(engine: Engine, run_id: str) -> list[dict]:
    """The steps of a run, in the order they ran."""
    with Session(engine) as session:
        rows = session.exec(select(AgentTraceStep).where(AgentTraceStep.run_id == run_id).order_by(AgentTraceStep.step)).all()
    return [{"step": r.step, "node": r.node, "status": r.status, "started_at": r.started_at.isoformat(), "duration_ms": r.duration_ms, "input": r.input, "output": r.output, "error": r.error} for r in rows]


# the node that reads each source, in the words of someone reading a trace
SOURCE_OF_NODE = {"lookup_ticket": "tool:ticket_lookup", "lookup_stock": "tool:stock_lookup", "retrieve_context": "rag"}


def sources_used(steps: list[dict]) -> list[str]:
    """Which sources a run consulted and in what order, from its trace: `tool:ticket_lookup`, `tool:stock_lookup`, `rag`."""
    sources: list[str] = []
    for step in steps:
        source = SOURCE_OF_NODE.get(step["node"])
        if source and source not in sources:
            sources.append(source)
    return sources
