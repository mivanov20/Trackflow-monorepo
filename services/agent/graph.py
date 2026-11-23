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

MEMORY (only when a conversation id is given; see services/agent/memory and docs/agent/memory-design.md). Three nodes join the graph, none of them runs in a turn
that has nothing to do with memory, so such a turn is traced exactly as before:
    resolve_memory      the conversation has a PENDING proposal: the user's message is first classified against it (approve / edit / reject / unrelated / unclear),
                        the decision is logged and, only if approved, written. What is left of the message (another question) continues through the graph.
    acknowledge_memory  the message was only a decision: it answers with what was done.
    propose_memory      after `generate`: the answer came with a `memory_proposal` that passed the gates: it is recorded as PENDING and the question
                        "want me to remember this?" is appended to the answer. Nothing is written.

STATE (no conversation history: one question at a time; what the agent keeps is in the memory store, read through an explicit interface):
    question   what the user asked (trimmed by the first node)
    plan       the planner's decision: steps in order, ticket id, stock query, and why (it goes to the trace)
    live_data  the results of the tools (`ToolResult`: ok / not_found / timeout / unavailable)
    context    the chunks `retrieve` returned (empty when none clears `min_score`)
    answer     the final text, from the model or from a fixed honest message
    outcome    how the run ended: generated | no_context | empty_question | needs_ticket_id | tool_unavailable | memory_decision
    conversation_id / actor   who the memory belongs to and who authorises it (set only for memory-enabled conversations)
    memory_decision, memory_notice, memory_candidate, memory_proposal, memories_used   the memory step's inputs and outputs (see above)
    verified_facts, scope_subtype   from the harness (docs/agent/guardrails-design.md): the verified records of the customer's own orders, and the kind of casual message to answer briefly
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from data.pipelines import rag

from . import streaming, tools
from .guardrails import prompt as secure_prompt
from .memory import decisions as memory_decisions
from .memory import generation as memory_generation
from .memory import policy as memory_policy
from .memory import store as memory_store
from .routing import plan_sources
from .trace import traced

RECEIVE, REJECT, PLAN, CLARIFY = "receive_question", "reject_empty", "plan_sources", "ask_clarification"
LOOKUP_TICKET, LOOKUP_STOCK, RETRIEVE = "lookup_ticket", "lookup_stock", "retrieve_context"
GENERATE, UNAVAILABLE, NO_INFORMATION = "generate", "answer_unavailable", "no_information"
RESOLVE, ACKNOWLEDGE, PROPOSE = "resolve_memory", "acknowledge_memory", "propose_memory"
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
    tools_allowed: bool  # False for a customer session: the live tools (internal tickets, all-client stock) are refused for it. Absent = internal call, no session
    context: list[dict]
    answer: str
    outcome: str
    conversation_id: str
    actor: str
    memory_decision: dict
    memory_notice: str
    memory_candidate: dict
    memory_proposal: dict
    memories_used: list
    verified_facts: list
    scope_subtype: str


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
    if state.get("tools_allowed") is False:  # a customer account: internal tickets are not theirs to read (OWASP A01, the agent must not use a tool its caller may not)
        result = tools.forbidden("ticket_lookup", {"ticket_id": state["plan"]["ticket_id"]})
    else:
        result = tools.lookup_ticket(state["plan"]["ticket_id"])
    return {"live_data": [*(state.get("live_data") or []), result.model_dump(mode="json")]}


def lookup_stock(state: AgentState) -> dict:
    """Reads the stock of a product from the inventory manager (read-only, with a timeout)."""
    if state.get("tools_allowed") is False:  # stock rows carry every B2B client's name and volumes
        result = tools.forbidden("stock_lookup", {"product_query": state["plan"]["stock_query"]})
    else:
        result = tools.lookup_stock(state["plan"]["stock_query"])
    return {"live_data": [*(state.get("live_data") or []), result.model_dump(mode="json")]}


def retrieve_context(state: AgentState) -> dict:
    """Runs `retrieve()` of the RAG pipeline, once. The threshold inside decides how many chunks (maybe none) come back."""
    return {"context": rag.retrieve(state["question"])}


def generate(state: AgentState) -> dict:
    """Writes the answer from the live results and the chunks the previous nodes produced.

    Without a conversation id this is `generate_answer()`. With one, it is the SAME model call with two more things: the memories that are relevant now (read from the
    store, not kept in the prompt) go in, and a `memory_proposal` (the agent's self-evaluation of this interaction) comes out. A proposal only travels in the state;
    nothing is remembered here.
    """
    blocks = [*(state.get("verified_facts") or []), *tools.live_chunks(state.get("live_data") or []), *(state.get("context") or [])]
    subtype = state.get("scope_subtype") or None
    if not state.get("conversation_id"):
        token = rag.active_system_prompt.set(secure_prompt.system_prompt(subtype))  # the agent's secure prompt, for this call only
        sink = streaming.sink_for_node()  # None unless the run was started for streaming (WebSocket chat): then the SAME model call is streamed
        sink_token = rag.token_sink.set(sink) if sink is not None else None
        try:
            return {"answer": rag.generate_answer(state["question"], blocks), "outcome": "generated"}
        finally:
            rag.active_system_prompt.reset(token)
            if sink_token is not None:
                rag.token_sink.reset(sink_token)
    memories = memory_store.get_store().recall(state["question"])
    turn = memory_generation.generate_with_self_evaluation(state["question"], blocks, memories, subtype)
    update: dict = {"answer": turn.answer, "outcome": "generated", "memories_used": [m.id for m in memories]}
    if turn.proposal_raw:
        update["memory_candidate"] = turn.proposal_raw
    return update


def resolve_memory(state: AgentState) -> dict:
    """The conversation has a pending proposal: classify the user's message against it, log the decision, write only what was approved.

    What is left of the message (a question asked in the same breath, or the whole message if the user moved on) continues through the graph as the question.
    """
    store = memory_store.get_store()
    pending = store.pending(state.get("conversation_id"))
    if pending is None:  # resolved by another request in between
        return {}
    message = state["question"]
    decision = memory_decisions.classify(message, pending.summary)
    resolution = store.resolve(pending.id, decision, decided_by=state.get("actor"), message=message)
    return {
        "question": decision.remainder.strip(),
        "memory_notice": resolution.notice,
        "memory_decision": {"proposal_id": pending.id, "label": decision.label, "confidence": round(decision.confidence, 2), "method": decision.method, "outcome": resolution.status, "entry_id": resolution.entry_id},
    }


def acknowledge_memory(state: AgentState) -> dict:
    """The message was only a decision about the proposal: say what was done."""
    return {"answer": state.get("memory_notice") or "Done.", "outcome": "memory_decision"}


def propose_memory(state: AgentState) -> dict:
    """The model offered something to remember. Gates first (the kind of message, a complete proposal, the forbidden-content policy, not already known, nothing else pending);
    if it passes, it is recorded as PENDING and the question is appended to the answer. Nothing is written to memory."""
    store = memory_store.get_store()
    conversation = state["conversation_id"]
    vetted = memory_generation.vet(state.get("memory_candidate"), state["question"], store)
    if vetted.blocked_categories:
        store.log_blocked(conversation, actor=state.get("actor"), origin_message=state["question"], categories=vetted.blocked_categories, kind=(state.get("memory_candidate") or {}).get("kind"))
    if vetted.proposal is None:
        return {"memory_candidate": {}, "memory_proposal": {"dropped": vetted.dropped or "nothing to propose"}}
    try:
        record = store.propose(conversation, vetted.proposal, actor=state.get("actor"), origin_message=state["question"])
    except memory_store.PendingExists:
        return {"memory_candidate": {}, "memory_proposal": {"dropped": "a proposal is already waiting for an answer in this conversation"}}
    return {"answer": state["answer"] + memory_generation.proposal_question(vetted.proposal), "memory_candidate": {}, "memory_proposal": {"id": record.id, "kind": record.kind, "scope_key": record.scope_key, "summary": record.summary}}


def answer_unavailable(state: AgentState) -> dict:
    """Every live lookup failed (or found nothing) and there is nothing else to answer from: an honest fixed message, no model, no made-up value."""
    return {"answer": tools.fallback_message(state.get("live_data") or []), "outcome": "tool_unavailable"}


def no_information(state: AgentState) -> dict:
    """Nothing cleared the threshold: it says so and does not ask the model, so it cannot invent a company fact."""
    return {"answer": NO_INFORMATION_MESSAGE, "outcome": "no_context"}


# ----------------------------------------------------------------------------- edges: decided by what the nodes produced
def after_receive(state: AgentState) -> str:
    if not state.get("question"):
        return "empty"
    if memory_store.get_store().pending(state.get("conversation_id")):  # a proposal is waiting: the user's message is first an answer to it
        return "memory"
    return "plan"


def after_resolve(state: AgentState) -> str:
    return "plan" if state.get("question", "").strip() else "acknowledge"


def after_generate(state: AgentState) -> str:
    return "propose" if state.get("memory_candidate") else "end"


def next_node(state: AgentState, current: str | None = None) -> str:
    """The one routing rule: the next step of the plan, or how to finish when the plan is done."""
    steps = state["plan"]["steps"]
    index = steps.index(current) + 1 if current else 0
    if index < len(steps):
        return STEP_NODE[steps[index]]
    live = state.get("live_data") or []
    if state.get("context") or state.get("verified_facts") or any(result["status"] == "ok" for result in live):
        return GENERATE
    if state.get("conversation_id") and memory_store.get_store().recall(state["question"], touch=False):
        return GENERATE  # nothing else answers, but the agent remembers something relevant that a support agent confirmed
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
        (RESOLVE, resolve_memory), (ACKNOWLEDGE, acknowledge_memory), (PROPOSE, propose_memory),
    )
    for name, function in nodes:
        builder.add_node(name, traced(name, function))
    builder.add_edge(START, RECEIVE)
    builder.add_conditional_edges(RECEIVE, after_receive, {"empty": REJECT, "plan": PLAN, "memory": RESOLVE})
    builder.add_conditional_edges(RESOLVE, after_resolve, {"plan": PLAN, "acknowledge": ACKNOWLEDGE})
    builder.add_conditional_edges(GENERATE, after_generate, {"propose": PROPOSE, "end": END})
    after_plan_targets = {name: name for name in (CLARIFY, LOOKUP_TICKET, LOOKUP_STOCK, RETRIEVE, GENERATE, UNAVAILABLE, NO_INFORMATION)}
    builder.add_conditional_edges(PLAN, after_plan, after_plan_targets)
    later_targets = {name: name for name in (LOOKUP_STOCK, RETRIEVE, GENERATE, UNAVAILABLE, NO_INFORMATION)}
    builder.add_conditional_edges(LOOKUP_TICKET, after_ticket, later_targets)
    builder.add_conditional_edges(LOOKUP_STOCK, after_stock, {name: name for name in (RETRIEVE, GENERATE, UNAVAILABLE, NO_INFORMATION)})
    builder.add_conditional_edges(RETRIEVE, after_retrieve, {name: name for name in (GENERATE, UNAVAILABLE, NO_INFORMATION)})
    for name in (REJECT, CLARIFY, UNAVAILABLE, NO_INFORMATION, ACKNOWLEDGE, PROPOSE):
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
