"""The support agent as a LangGraph graph: state, single-responsibility nodes, conditional edges, compile.

            +--> reject_empty ----------------------------> END        (the question is empty)
 receive_question
            +--> retrieve_context --+--> generate ----------> END      (something cleared the threshold)
                                    +--> no_information ----> END      (nothing did: it says so, no model call)

The RAG logic is not rewritten here: `retrieve_context` calls `data.pipelines.rag.retrieve` and `generate` calls
`data.pipelines.rag.generate_answer` with the context the first node already produced. No node calls the monolithic `query()`,
so retrieval runs once and each step is traced on its own.

STATE. Four fields, because a node needs nothing else to decide what comes next:
    question  what the user asked (trimmed by the first node)
    context   the chunks `retrieve` returned (empty when none clears `min_score`)
    answer    the final text, written by the generation model or by `no_information` / `reject_empty`
    outcome   how the run ended: generated | no_context | empty_question
There is NO conversation history: the agent answers one question at a time and the knowledge base is the only memory it uses
(RAG is a knowledge base the system consults, not memory of the conversation). Part 2 adds tools; if it ever needs the earlier turns
it adds the field then, with its reason.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from data.pipelines import rag

from .trace import traced

RECEIVE, REJECT, RETRIEVE, GENERATE, NO_INFORMATION = "receive_question", "reject_empty", "retrieve_context", "generate", "no_information"
REQUIRED_NODES = (RECEIVE, RETRIEVE, GENERATE)

EMPTY_QUESTION_MESSAGE = "Please type a question about delivery SLA, returns, carrier coverage or storage pricing."
NO_INFORMATION_MESSAGE = (
    "I don't have information about that in the TrackFlow knowledge base (delivery SLA, returns policy, carrier coverage and storage pricing). "
    "Please confirm it with the responsible area before promising anything to the client."
)


class AgentState(TypedDict, total=False):
    question: str
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


def retrieve_context(state: AgentState) -> dict:
    """Runs `retrieve()` of the RAG pipeline, once. The threshold inside decides how many chunks (maybe none) come back."""
    return {"context": rag.retrieve(state["question"])}


def generate(state: AgentState) -> dict:
    """Writes the answer with `generate_answer()` from the context the previous node produced."""
    return {"answer": rag.generate_answer(state["question"], state["context"]), "outcome": "generated"}


def no_information(state: AgentState) -> dict:
    """Nothing cleared the threshold: it says so and does not ask the model, so it cannot invent a company fact."""
    return {"answer": NO_INFORMATION_MESSAGE, "outcome": "no_context"}


# ----------------------------------------------------------------------------- edges: decided by what the nodes produced
def after_receive(state: AgentState) -> str:
    return "empty" if not state.get("question") else "retrieve"


def after_retrieve(state: AgentState) -> str:
    return "context" if state.get("context") else "nothing_found"


def build_graph() -> StateGraph:
    """The graph, not yet compiled."""
    builder = StateGraph(AgentState)
    for name, function in ((RECEIVE, receive_question), (REJECT, reject_empty), (RETRIEVE, retrieve_context), (GENERATE, generate), (NO_INFORMATION, no_information)):
        builder.add_node(name, traced(name, function))
    builder.add_edge(START, RECEIVE)
    builder.add_conditional_edges(RECEIVE, after_receive, {"empty": REJECT, "retrieve": RETRIEVE})
    builder.add_conditional_edges(RETRIEVE, after_retrieve, {"context": GENERATE, "nothing_found": NO_INFORMATION})
    for name in (REJECT, GENERATE, NO_INFORMATION):
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
