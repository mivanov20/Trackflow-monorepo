"""The support agent as a LangGraph graph: state, single-responsibility nodes, conditional edges, compile.

 receive_question --empty--> reject_empty ------------------------------------------------------> END
        |
        v
  plan_sources  --no ticket number--> ask_clarification -------------------------------------------> END
        |   decides, from the question, which sources are needed and in which order:
        |   tools first (live data), then the RAG (stable documents)
        v
  lookup_ticket (incident manager) --+
  lookup_stock  (inventory manager) --+--> retrieve_context (RAG) --+--> generate ------------------> END
                                                                    +--> answer_unavailable --------> END   a tool failed and nothing else can answer
                                                                    +--> no_information ------------> END   nothing found, nothing failed

Every node after `plan_sources` routes with ONE rule (`next_node`): run the next step of the plan; when the plan is done, `generate` if there is
something to answer from (a live result or a retrieved chunk), `answer_unavailable` if only failed lookups exist, `no_information` otherwise.
A failed tool is therefore not an error that breaks the run: the plan continues (a question that also needs the RAG still gets that part), the
failure travels to the model as a block saying the value is UNKNOWN, and when nothing else can answer, a fixed honest message is returned
without calling the model.

The RAG logic is not rewritten: `retrieve_context` calls `rag.retrieve`, `generate` calls `rag.generate_answer` with the retrieved chunks plus the
live results (as blocks with their own source: `incident-manager`, `inventory-manager`). No node calls the monolithic `query()`.

STATE (no conversation history: one question at a time, the knowledge base and the live services are the only memory it uses):
    question   what the user asked (trimmed by the first node)
    plan       the planner's decision: steps in order, ticket id, stock query, and why (it goes to the trace)
    live_data  the results of the tools (`ToolResult`: ok / not_found / timeout / unavailable)
    context    the chunks `retrieve` returned (empty when none clears `min_score`)
    answer     the final text, from the model or from a fixed honest message
    outcome    how the run ended: generated | no_context | empty_question | needs_ticket_id | tool_unavailable
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from data.pipelines import rag

from . import tools
from .routing import plan_sources
from .trace import traced

RECEIVE, REJECT, PLAN, CLARIFY = "receive_question", "reject_empty", "plan_sources", "ask_clarification"
LOOKUP_TICKET, LOOKUP_STOCK, RETRIEVE = "lookup_ticket", "lookup_stock", "retrieve_context"
GENERATE, UNAVAILABLE, NO_INFORMATION = "generate", "answer_unavailable", "no_information"
REQUIRED_NODES = (RECEIVE, PLAN, LOOKUP_TICKET, RETRIEVE, GENERATE)
STEP_NODE = {"ticket": LOOKUP_TICKET, "stock": LOOKUP_STOCK, "rag": RETRIEVE}

EMPTY_QUESTION_MESSAGE = "Please type a question about delivery SLA, returns, carrier coverage, storage pricing, a ticket or a product's stock."
CLARIFY_MESSAGE = "Which ticket do you mean? Please give me the ticket number (for example: \"status of ticket 482\") and I'll check it in the incident manager."
NO_INFORMATION_MESSAGE = (
    "I don't have information about that in the TrackFlow knowledge base (delivery SLA, returns policy, carrier coverage and storage pricing). "
    "Please confirm it with the responsible area before promising anything to the client."
)


class AgentState(TypedDict, total=False):
    question: str
    plan: dict
    live_data: list[dict]
    context: list[dict]
    answer: str
    outcome: str


class AgentBuildError(RuntimeError):
    """The graph is not valid. Raised when it is built (at startup), never while serving a question."""


# ----------------------------------------------------------------------------- nodes: one responsibility each
def receive_question(state: AgentState) -> dict:
    """Takes the question in and trims it. It decides nothing: the edge after it does."""
    return {"question": (state.get("question") or "").strip()}


def reject_empty(state: AgentState) -> dict:
    return {"answer": EMPTY_QUESTION_MESSAGE, "outcome": "empty_question"}


def plan_sources_node(state: AgentState) -> dict:
    """Decides which sources the question needs (see `routing.plan_sources`). It reads nothing and answers nothing."""
    return {"plan": plan_sources(state["question"])}


def ask_clarification(state: AgentState) -> dict:
    return {"answer": CLARIFY_MESSAGE, "outcome": "needs_ticket_id"}


def lookup_ticket(state: AgentState) -> dict:
    """Reads one ticket from the incident manager (read-only, with a timeout). A failure is a result, not an exception."""
    result = tools.lookup_ticket(state["plan"]["ticket_id"])
    return {"live_data": [*(state.get("live_data") or []), result.model_dump(mode="json")]}


def lookup_stock(state: AgentState) -> dict:
    """Reads the stock of a product from the inventory manager (read-only, with a timeout)."""
    result = tools.lookup_stock(state["plan"]["stock_query"])
    return {"live_data": [*(state.get("live_data") or []), result.model_dump(mode="json")]}


def retrieve_context(state: AgentState) -> dict:
    """Runs `retrieve()` of the RAG pipeline, once. The threshold inside decides how many chunks (maybe none) come back."""
    return {"context": rag.retrieve(state["question"])}


def generate(state: AgentState) -> dict:
    """Writes the answer with `generate_answer()` from the live results and the chunks the previous nodes produced."""
    blocks = [*tools.live_chunks(state.get("live_data") or []), *(state.get("context") or [])]
    return {"answer": rag.generate_answer(state["question"], blocks), "outcome": "generated"}


def answer_unavailable(state: AgentState) -> dict:
    """Every live lookup failed (or found nothing) and there is nothing else to answer from: an honest fixed message, no model, no made-up value."""
    return {"answer": tools.fallback_message(state.get("live_data") or []), "outcome": "tool_unavailable"}


def no_information(state: AgentState) -> dict:
    """Nothing cleared the threshold: it says so and does not ask the model, so it cannot invent a company fact."""
    return {"answer": NO_INFORMATION_MESSAGE, "outcome": "no_context"}


# ----------------------------------------------------------------------------- edges: decided by what the nodes produced
def after_receive(state: AgentState) -> str:
    return "empty" if not state.get("question") else "plan"


def next_node(state: AgentState, current: str | None = None) -> str:
    """The one routing rule: the next step of the plan, or how to finish when the plan is done."""
    steps = state["plan"]["steps"]
    index = steps.index(current) + 1 if current else 0
    if index < len(steps):
        return STEP_NODE[steps[index]]
    live = state.get("live_data") or []
    if state.get("context") or any(result["status"] == "ok" for result in live):
        return GENERATE
    return UNAVAILABLE if live else NO_INFORMATION


def after_plan(state: AgentState) -> str:
    return CLARIFY if state["plan"].get("clarify") else next_node(state)


def after_ticket(state: AgentState) -> str:
    return next_node(state, "ticket")


def after_stock(state: AgentState) -> str:
    return next_node(state, "stock")


def after_retrieve(state: AgentState) -> str:
    return next_node(state, "rag")


def build_graph() -> StateGraph:
    """The graph, not yet compiled."""
    builder = StateGraph(AgentState)
    nodes = (
        (RECEIVE, receive_question), (REJECT, reject_empty), (PLAN, plan_sources_node), (CLARIFY, ask_clarification), (LOOKUP_TICKET, lookup_ticket),
        (LOOKUP_STOCK, lookup_stock), (RETRIEVE, retrieve_context), (GENERATE, generate), (UNAVAILABLE, answer_unavailable), (NO_INFORMATION, no_information),
    )
    for name, function in nodes:
        builder.add_node(name, traced(name, function))
    builder.add_edge(START, RECEIVE)
    builder.add_conditional_edges(RECEIVE, after_receive, {"empty": REJECT, "plan": PLAN})
    after_plan_targets = {name: name for name in (CLARIFY, LOOKUP_TICKET, LOOKUP_STOCK, RETRIEVE, GENERATE, UNAVAILABLE, NO_INFORMATION)}
    builder.add_conditional_edges(PLAN, after_plan, after_plan_targets)
    later_targets = {name: name for name in (LOOKUP_STOCK, RETRIEVE, GENERATE, UNAVAILABLE, NO_INFORMATION)}
    builder.add_conditional_edges(LOOKUP_TICKET, after_ticket, later_targets)
    builder.add_conditional_edges(LOOKUP_STOCK, after_stock, {name: name for name in (RETRIEVE, GENERATE, UNAVAILABLE, NO_INFORMATION)})
    builder.add_conditional_edges(RETRIEVE, after_retrieve, {name: name for name in (GENERATE, UNAVAILABLE, NO_INFORMATION)})
    for name in (REJECT, CLARIFY, GENERATE, UNAVAILABLE, NO_INFORMATION):
        builder.add_edge(name, END)
    return builder


def verify_structure(compiled) -> None:
    """Structural checks on the compiled graph, so a mistake fails when the agent is built and not on a user's question."""
    drawn = compiled.get_graph()
    nodes = {n for n in drawn.nodes if n not in ("__start__", "__end__")}
    missing = [n for n in REQUIRED_NODES if n not in nodes]
    if missing:
        raise AgentBuildError(f"The agent graph is missing required node(s): {', '.join(missing)}.")
    forward: dict[str, set[str]] = {}
    for edge in drawn.edges:
        forward.setdefault(edge.source, set()).add(edge.target)

    def reach(start: str, graph: dict[str, set[str]]) -> set[str]:
        seen, pending = {start}, [start]
        while pending:
            for nxt in graph.get(pending.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    pending.append(nxt)
        return seen

    unreachable = sorted(nodes - reach("__start__", forward))
    if unreachable:
        raise AgentBuildError(f"The agent graph has node(s) that nothing leads to: {', '.join(unreachable)}.")
    backward: dict[str, set[str]] = {}
    for source, targets in forward.items():
        for target in targets:
            backward.setdefault(target, set()).add(source)
    dead_ends = sorted(nodes - reach("__end__", backward))
    if dead_ends:
        raise AgentBuildError(f"The agent graph has node(s) that cannot reach the end: {', '.join(dead_ends)}.")


def compile_graph(builder: StateGraph, checkpointer):
    """Compiles (LangGraph validates edges and entry point) and then checks the structure. Any failure is an AgentBuildError."""
    # LangGraph treats a node with no outgoing edge as an implicit end; here every way out must be written down.
    has_exit = {source for source, _ in builder.edges} | set(builder.branches)
    silent_ends = sorted(set(builder.nodes) - has_exit)
    if silent_ends:
        raise AgentBuildError(f"The agent graph has node(s) with no outgoing edge: {', '.join(silent_ends)} (connect them to the next node or to END).")
    try:
        compiled = builder.compile(checkpointer=checkpointer)
    except Exception as error:  # noqa: BLE001 - LangGraph raises ValueError / KeyError / InvalidUpdateError depending on the mistake
        raise AgentBuildError(f"The agent graph is not valid: {error}") from error
    verify_structure(compiled)
    return compiled
