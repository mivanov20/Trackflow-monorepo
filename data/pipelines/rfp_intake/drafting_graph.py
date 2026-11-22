"""The `rfp_drafting` LangGraph (Part 2): from the Part 1 handoff to one evaluated draft per department, handed to Part 3.

OUTER GRAPH (assignment orchestrator -> department-scoped workers in parallel -> synthesizer)

    START -> assign --one Send per department of the handoff--> warehouse_section --+
                                                                lastmile_section  --+--> finalize --> END
                                                                reverse_section   --+

Each `*_section` node runs ONE department's generator-evaluator loop (the sub-graph below) from start to finish, so a department that is slow, retries or exhausts its
iterations never blocks the others: they are parallel branches, each with its own loop. `finalize` builds the Part 3 handoff from whatever each section ended with.

DEPARTMENT LOOP (sub-graph, one run per department)

    START -> generate --Send x3--> evaluate_readability --+
                ^                  evaluate_relevance   --+--> decide --pass--> END
                |                  evaluate_compliance  --+        |
                |                                                  +--failed, iterations left--> generate  (with feedback_for_generator)
                +--------------------------------------------------+--failed, limit reached----> END       (needs_human_review: the LAST draft and ITS evaluation are kept)

 * The three evaluators run in parallel (LangGraph `Send`) and `decide` runs once all three are in.
 * `max_iterations` bounds the loop: attempt N is the last one. A section that never passes ends `needs_human_review`; it is never dropped and never discards the ticket.
 * The generators read the HANDOFF of Part 1 (key_aspects), never the PDF.
 * Progress goes out through a `reporter` (config["configurable"]["reporter"]): the runner persists drafting / under_evaluation as it happens.
"""

from __future__ import annotations

import operator
import time
from dataclasses import asdict
from typing import Annotated, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from . import evaluators, generators
from .config import DEPARTMENT_IDS, DEPARTMENTS, GUIDELINES, HANDOFF_SCHEMA_VERSION
from .knowledge import Facts

DEFAULT_MAX_ITERATIONS = 3
PASSED = "passed"
NEEDS_HUMAN_REVIEW = "needs_human_review"


class NullReporter:
    """Reporter that does nothing: the graph works (and is tested) without a database."""

    def generating(self, department_id: str, iteration: int) -> None: ...
    def drafted(self, department_id: str, iteration: int, content: str, engine: str) -> None: ...
    def evaluated(self, department_id: str, iteration: int, result: dict, outcome: str | None) -> None: ...


def reporter_of(config: RunnableConfig) -> NullReporter:
    return (config or {}).get("configurable", {}).get("reporter") or NullReporter()


# ------------------------------------------------------------------------------------------------ the Part 1 handoff -> briefs
def build_briefs(handoff: dict) -> list[dict]:
    """One brief per department of the handoff, with ONLY that department's key aspects and the shared client data. Raises ValueError if the handoff is not usable."""
    if not isinstance(handoff, dict) or not handoff.get("ticket_id") or not handoff.get("workstreams"):
        raise ValueError("The intake handoff is missing or has no department workstreams.")
    if handoff.get("schema_version") != HANDOFF_SCHEMA_VERSION:
        raise ValueError(f"Unsupported intake handoff version: {handoff.get('schema_version')}")
    client = handoff["client"]
    summary = (handoff.get("summary") or "").split("\n\n")[0]
    briefs = []
    for stream in handoff["workstreams"]:
        department_id = stream["department_id"]
        spec = DEPARTMENTS[department_id]
        briefs.append(
            {
                "department_id": department_id,
                "department": spec["name"],
                "owner": spec["owner"],
                "contributes": spec["contributes"],
                "client": {"name": client.get("name"), "country": client.get("country"), "currency": client.get("currency")},
                "monthly_volume": handoff.get("monthly_volume"),
                "deadline": handoff.get("deadline"),
                "services_requested": handoff.get("services_requested", []),
                "departments_needed": handoff.get("departments_needed", []),
                "summary": summary,
                "key_aspects": stream["key_aspects"],
            }
        )
    return sorted(briefs, key=lambda brief: DEPARTMENT_IDS.index(brief["department_id"]))


# ------------------------------------------------------------------------------------------------ department loop
class SectionState(TypedDict, total=False):
    brief: dict
    facts: dict
    ticket_id: str
    iteration: int
    max_iterations: int
    feedback: dict | None
    draft: dict
    evals: Annotated[list, operator.add]
    result: dict
    outcome: str
    history: Annotated[list, operator.add]
    attempts: Annotated[list, operator.add]


def _facts(state: SectionState) -> Facts:
    return Facts(**state["facts"])


def generate_node(state: SectionState, config: RunnableConfig) -> dict:
    brief, iteration = state["brief"], state["iteration"]
    reporter_of(config).generating(brief["department_id"], iteration)
    draft = generators.generate(brief, _facts(state), state.get("feedback"), state.get("draft", {}).get("content"))
    reporter_of(config).drafted(brief["department_id"], iteration, draft.content, draft.engine)
    return {"draft": {"content": draft.content, "engine": draft.engine, "derived_numbers": draft.derived_numbers, "iteration": iteration}}


def fan_out_evaluators(state: SectionState) -> list[Send]:
    payload = {"brief": state["brief"], "facts": state["facts"], "draft": state["draft"], "iteration": state["iteration"]}
    return [Send("evaluate_readability", payload), Send("evaluate_relevance", payload), Send("evaluate_compliance", payload)]


def evaluate_readability_node(payload: dict) -> dict:
    return {"evals": [{"iteration": payload["iteration"], "kind": "readability", "result": evaluators.evaluate_readability(payload["draft"]["content"])}]}


def evaluate_relevance_node(payload: dict) -> dict:
    result = evaluators.evaluate_relevance(payload["draft"]["content"], payload["brief"], Facts(**payload["facts"]), payload["draft"]["derived_numbers"])
    return {"evals": [{"iteration": payload["iteration"], "kind": "relevance", "result": result}]}


def evaluate_compliance_node(payload: dict) -> dict:
    return {"evals": [{"iteration": payload["iteration"], "kind": "compliance", "result": evaluators.evaluate_compliance(payload["draft"]["content"], payload["brief"], Facts(**payload["facts"]))}]}


def decide_node(state: SectionState, config: RunnableConfig) -> dict:
    brief, iteration = state["brief"], state["iteration"]
    department_id = brief["department_id"]
    current = {entry["kind"]: entry["result"] for entry in state["evals"] if entry["iteration"] == iteration}
    result = evaluators.consolidate(department_id, f"{state['ticket_id']}:{department_id}", iteration, current["readability"], current["relevance"], current["compliance"])
    attempt = {"iteration": iteration, "draft": state["draft"]["content"], "engine": state["draft"]["engine"], "evaluation_result": result}
    if result["overall_pass"]:
        outcome = PASSED
    elif iteration >= state["max_iterations"]:
        outcome = NEEDS_HUMAN_REVIEW  # the loop is bounded: keep this draft and this evaluation, hand it to a human
    else:
        outcome = "retry"
    reporter_of(config).evaluated(department_id, iteration, result, None if outcome == "retry" else outcome)
    update: dict = {"result": result, "outcome": outcome, "attempts": [attempt]}
    if outcome == "retry":
        update.update({"iteration": iteration + 1, "feedback": {"text": result["feedback_for_generator"], "fixes": result["fixes"]}})
    return update


def after_decide(state: SectionState) -> str:
    return "generate" if state["outcome"] == "retry" else END


def build_section_graph() -> StateGraph:
    graph = StateGraph(SectionState)
    graph.add_node("generate", generate_node)
    graph.add_node("evaluate_readability", evaluate_readability_node)
    graph.add_node("evaluate_relevance", evaluate_relevance_node)
    graph.add_node("evaluate_compliance", evaluate_compliance_node)
    graph.add_node("decide", decide_node)
    graph.add_edge(START, "generate")
    graph.add_conditional_edges("generate", fan_out_evaluators, ["evaluate_readability", "evaluate_relevance", "evaluate_compliance"])
    for name in ("evaluate_readability", "evaluate_relevance", "evaluate_compliance"):
        graph.add_edge(name, "decide")
    graph.add_conditional_edges("decide", after_decide, {"generate": "generate", END: END})
    return graph


_SECTION_GRAPH = None


def section_graph():
    global _SECTION_GRAPH
    if _SECTION_GRAPH is None:
        _SECTION_GRAPH = build_section_graph().compile()
    return _SECTION_GRAPH


# ------------------------------------------------------------------------------------------------ outer graph
def merge(left: dict, right: dict) -> dict:
    return {**left, **right}


class DraftingState(TypedDict, total=False):
    ticket_id: str
    handoff: dict
    briefs: list
    facts: dict
    max_iterations: int
    sections: Annotated[dict, merge]
    part3_handoff: dict
    steps: Annotated[list, operator.add]


def assign_node(state: DraftingState) -> dict:
    started = time.monotonic()
    briefs = build_briefs(state["handoff"])
    detail = "departments: " + ", ".join(brief["department"] for brief in briefs)
    return {"briefs": briefs, "steps": [{"step": "assign", "detail": detail, "seconds": round(time.monotonic() - started, 2)}]}


SECTION_NODES = {department_id: f"{department_id}_section" for department_id in DEPARTMENT_IDS}


def dispatch(state: DraftingState) -> list[Send]:
    return [Send(SECTION_NODES[brief["department_id"]], {"brief": brief, "facts": state["facts"], "ticket_id": state["ticket_id"], "max_iterations": state["max_iterations"]}) for brief in state["briefs"]]


def make_section_node(department_id: str):
    def section_node(payload: dict, config: RunnableConfig) -> dict:
        started = time.monotonic()
        final = section_graph().invoke({**payload, "iteration": 1, "feedback": None, "evals": [], "attempts": []}, config)
        last = final["attempts"][-1]
        section = {
            "section_id": f"{payload['ticket_id']}:{department_id}",
            "department_id": department_id,
            "department": DEPARTMENTS[department_id]["name"],
            "owner": DEPARTMENTS[department_id]["owner"],
            "content": last["draft"],
            "engine": last["engine"],
            "status": final["outcome"],
            "iterations": last["iteration"],
            "evaluation_result": last["evaluation_result"],
            "history": [{"iteration": a["iteration"], "overall_pass": a["evaluation_result"]["overall_pass"], "feedback_for_generator": a["evaluation_result"]["feedback_for_generator"]} for a in final["attempts"][:-1]],
        }
        step = {"step": f"section:{department_id}", "detail": f"{final['outcome']} after {last['iteration']} attempt(s)", "seconds": round(time.monotonic() - started, 2)}
        return {"sections": {department_id: section}, "steps": [step]}

    section_node.__name__ = SECTION_NODES[department_id]
    return section_node


def finalize_node(state: DraftingState) -> dict:
    started = time.monotonic()
    handoff = state["handoff"]
    sections = [state["sections"][department_id] for department_id in DEPARTMENT_IDS if department_id in state["sections"]]
    review = [section["department_id"] for section in sections if section["status"] == NEEDS_HUMAN_REVIEW]
    part3 = {
        "schema_version": 1,
        "ticket_id": state["ticket_id"],
        "rfp_id": handoff.get("rfp_id"),
        "status": NEEDS_HUMAN_REVIEW if review else "under_evaluation",
        "needs_human_review": review,
        "client": handoff["client"],
        "arbiter": handoff.get("arbiter"),
        "guidelines": list(GUIDELINES),
        "max_iterations": state["max_iterations"],
        "sections": [
            {
                "section_id": section["section_id"],
                "department_id": section["department_id"],
                "department": section["department"],
                "owner": section["owner"],
                "content": section["content"],
                "section_status": section["status"],
                "iterations": section["iterations"],
                "evaluation_result": section["evaluation_result"],
                "approval_status": "pending",
            }
            for section in sections
        ],
    }
    return {"part3_handoff": part3, "steps": [{"step": "finalize", "detail": f"{len(sections)} section(s), {len(review)} need human review", "seconds": round(time.monotonic() - started, 2)}]}


def build_graph() -> StateGraph:
    graph = StateGraph(DraftingState)
    graph.add_node("assign", assign_node)
    for department_id, name in SECTION_NODES.items():
        graph.add_node(name, make_section_node(department_id))
    graph.add_node("finalize", finalize_node)
    graph.add_edge(START, "assign")
    graph.add_conditional_edges("assign", dispatch, list(SECTION_NODES.values()))
    for name in SECTION_NODES.values():
        graph.add_edge(name, "finalize")
    graph.add_edge("finalize", END)
    return graph


def compile_graph():
    return build_graph().compile()


def facts_state(country: str | None) -> dict:
    from .knowledge import facts

    return asdict(facts(country))
