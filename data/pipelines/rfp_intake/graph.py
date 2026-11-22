"""The `rfp_intake` LangGraph: a graph of its own, not part of the CX agent's.

    START -> convert -> classify --not an RFP--> discard -------------------------------------------> END
                           |
                           v  (a real RFP)
                        analyze  (metadata + readability)
                           |
                           v
                      orchestrate  --one Send per department the scope needs-->  warehouse_worker --+
                           |                                                     lastmile_worker  --+--> synthesize --> END
                           +--no department found-------------------------------> reverse_worker   --+      (also reached directly)

 * orchestrator-worker-synthesizer: `orchestrate` decides the departments and writes the subtasks, the workers run IN PARALLEL (LangGraph `Send`), each with only its own
   subtask, and `synthesize` runs once all of them are done. A department that the RFP does not need never runs: ModaViva (warehousing + returns) never starts `lastmile_worker`.
 * `convert` happens before any agent reads: the PDF becomes Markdown first (MarkItDown).
 * The graph knows nothing about HTTP or the database: `runner.run_intake` streams it and persists what each node reports.

STATE
    pdf_path / ticket_id / rfp_id   the input
    markdown                        the converted document (what every agent reads)
    classification                  the classifier's verdict
    metadata / readability          the RFP's fields and processing-cost metrics
    subtasks                        what the orchestrator handed to the workers
    sections                        one per worker (the reducer appends: parallel workers do not overwrite each other)
    synthesis / handoff             the Sales-facing result and the contract with Part 2
    outcome                         discarded | intake_complete
    steps                           the progress log (the reducer appends)
"""

from __future__ import annotations

import operator
import time
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from . import classifier, convert, metadata, orchestrator, reading_cost, synthesizer, workers
from .config import DEPARTMENT_IDS, DISCARDED, INTAKE_COMPLETE


class IntakeState(TypedDict, total=False):
    ticket_id: str
    rfp_id: str
    pdf_path: str
    markdown: str
    classification: dict
    metadata: dict
    readability: dict
    subtasks: list
    sections: Annotated[list, operator.add]
    synthesis: dict
    handoff: dict
    outcome: str
    steps: Annotated[list, operator.add]


WORKER_NODES = {department_id: f"{department_id}_worker" for department_id in DEPARTMENT_IDS}


def _step(name: str, detail: str, started: float, engine: str | None = None) -> list[dict]:
    return [{"step": name, "detail": detail, "engine": engine, "seconds": round(time.monotonic() - started, 2)}]


# ------------------------------------------------------------------------------------------------ nodes
def convert_node(state: IntakeState) -> dict:
    started = time.monotonic()
    markdown = convert.pdf_to_markdown(state["pdf_path"])
    return {"markdown": markdown, "steps": _step("convert", f"PDF converted to Markdown ({len(markdown)} characters)", started)}


def classify_node(state: IntakeState) -> dict:
    started = time.monotonic()
    verdict = classifier.classify(state["markdown"])
    label = "valid RFP" if verdict["is_rfp"] else "not an RFP"
    return {"classification": verdict, "steps": _step("classify", f"{label}: {verdict['document_type']}", started, verdict["engine"])}


def after_classify(state: IntakeState) -> str:
    return "analyze" if state["classification"]["is_rfp"] else "discard"


def discard_node(state: IntakeState) -> dict:
    started = time.monotonic()
    return {"outcome": DISCARDED, "steps": _step("discard", state["classification"]["reason"], started)}


def analyze_node(state: IntakeState) -> dict:
    started = time.monotonic()
    meta = metadata.extract(state["markdown"])
    cost = reading_cost.analyze(state["markdown"])
    detail = f"{meta['client_name'] or 'client not found'}, {meta['client_country'] or 'country unknown'}; {len(meta['departments_needed'])} department(s); cost band {cost['cost_band']}"
    return {"metadata": meta, "readability": cost, "steps": _step("analyze", detail, started, meta["engine"])}


def orchestrate_node(state: IntakeState) -> dict:
    started = time.monotonic()
    subtasks = orchestrator.plan(state["markdown"], state["metadata"])
    engine = "llm" if any(task.get("focus_engine") == "llm" for task in subtasks) else "rules"
    names = ", ".join(task["department"] for task in subtasks) or "none"
    return {"subtasks": subtasks, "steps": _step("orchestrate", f"departments: {names}", started, engine)}


def dispatch(state: IntakeState) -> list[Send] | str:
    """One `Send` per subtask (the workers run in parallel, each seeing only its own); with no department, straight to the synthesizer."""
    sends = [Send(WORKER_NODES[task["department_id"]], {"subtask": task}) for task in state["subtasks"]]
    return sends or "synthesize"


def make_worker_node(department_id: str):
    def worker_node(payload: dict) -> dict:
        started = time.monotonic()
        section = workers.run_worker(payload["subtask"])
        return {"sections": [section], "steps": _step(f"worker:{department_id}", section["key_aspects"]["summary"], started, section["engine"])}

    worker_node.__name__ = WORKER_NODES[department_id]
    return worker_node


def synthesize_node(state: IntakeState) -> dict:
    started = time.monotonic()
    order = {department_id: index for index, department_id in enumerate(DEPARTMENT_IDS)}
    sections = sorted(state.get("sections", []), key=lambda section: order[section["department_id"]])  # parallel workers finish in any order
    meta = {**state["metadata"]}
    if not sections:
        meta["open_questions"] = [*meta["open_questions"], "The scope of the request is not clear enough to involve any department: Sales must ask the client what services it wants."]
    synthesis = synthesizer.synthesize(meta, sections, state["readability"])
    handoff = synthesizer.build_handoff(state["ticket_id"], state["rfp_id"], meta, state["readability"], sections, synthesis)
    return {
        "synthesis": {**synthesis, "sections": sections, "open_questions": meta["open_questions"]},  # `sections` here is the sorted copy the runner stores
        "handoff": handoff,
        "outcome": INTAKE_COMPLETE,
        "steps": _step("synthesize", f"{len(sections)} section(s) consolidated; handoff ready for Part 2", started, synthesis["engine"]),
    }


# ------------------------------------------------------------------------------------------------ graph
def build_graph() -> StateGraph:
    graph = StateGraph(IntakeState)
    graph.add_node("convert", convert_node)
    graph.add_node("classify", classify_node)
    graph.add_node("discard", discard_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("orchestrate", orchestrate_node)
    for department_id, name in WORKER_NODES.items():
        graph.add_node(name, make_worker_node(department_id))
    graph.add_node("synthesize", synthesize_node)

    graph.add_edge(START, "convert")
    graph.add_edge("convert", "classify")
    graph.add_conditional_edges("classify", after_classify, {"analyze": "analyze", "discard": "discard"})
    graph.add_edge("discard", END)
    graph.add_edge("analyze", "orchestrate")
    graph.add_conditional_edges("orchestrate", dispatch, [*WORKER_NODES.values(), "synthesize"])
    for name in WORKER_NODES.values():
        graph.add_edge(name, "synthesize")
    graph.add_edge("synthesize", END)
    return graph


def compile_graph():
    return build_graph().compile()
