"""Evals and tests of the support agent (LangGraph). Run all of them with:  python -m pytest tests/pipelines/test_agent_evals.py

The evals check the TRACE of a run (which nodes ran, in what order, what each produced), read back from the database after the run,
never the console and never a fresh live execution to inspect. One eval also asserts the answer stays grounded in the knowledge
base of CONTEXT-company.md. The existing RAG tests (tests/pipelines/test_rag.py) are not replaced: they must still pass.
"""

import ast
import hashlib
import inspect
import math
import os
import re
import sqlite3
from pathlib import Path

import pytest

# the services package reads these when it is imported
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "test-only-secret-with-at-least-32-characters")

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from qdrant_client import QdrantClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from data.pipelines import rag
from data.process import knowledge_base, model_client
from services.agent import graph as agent_graph
from services.agent import runner
from services.agent.trace import read_trace
from services.agent.graph import AgentBuildError, AgentState

GENERATE_CHUNK = {"source_document": "returns-policy", "section": "Returns Policy > Standard return window", "chunk_index": 1, "text": "Standard return window: 30 days from delivery, unless the client brand has configured a different window in their contract.", "score": 0.91}


# ------------------------------------------------------------------ helpers
@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def agent():
    return agent_graph.compile_graph(agent_graph.build_graph(), MemorySaver())


class Pipeline:
    """Stands in for the RAG pipeline and counts how many times each step was called."""

    def __init__(self, monkeypatch, context=None, answer="Standard returns are accepted within 30 days from delivery.\nSource: returns-policy"):
        self.calls = {"retrieve": 0, "generate": 0}
        self.generated_with = None
        self.fail_generation_times = 0
        self.context = [GENERATE_CHUNK] if context is None else context
        self.answer = answer
        monkeypatch.setattr(rag, "retrieve", self._retrieve)
        monkeypatch.setattr(rag, "generate_answer", self._generate)

    def _retrieve(self, question, **kwargs):
        self.calls["retrieve"] += 1
        return self.context

    def _generate(self, question, context):
        self.calls["generate"] += 1
        self.generated_with = (question, context)
        if self.fail_generation_times:
            self.fail_generation_times -= 1
            raise model_client.ModelServiceError("model service down")
        return self.answer


def nodes_of(steps):
    return [s["node"] for s in steps]


def fake_embed(text: str) -> list[float]:
    vector = [0.0] * 64
    for word in re.findall(r"[a-z0-9%]+", text.lower()):
        vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1.0
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


# ================================================================== EVALS (criteria on the trace)
def test_eval_1_retrieve_runs_before_generate_and_hands_it_its_context(monkeypatch, agent, engine):
    pipeline = Pipeline(monkeypatch)
    result = runner.run_agent("What's the standard return window?", graph=agent, engine=engine, run_id="eval-1")
    steps = runner.run_trace("eval-1", engine=engine)
    assert nodes_of(steps) == ["receive_question", "plan_sources", "retrieve_context", "generate"]  # the order
    assert nodes_of(steps).index("retrieve_context") < nodes_of(steps).index("generate")
    assert nodes_of(steps).count("retrieve_context") == 1 and pipeline.calls == {"retrieve": 1, "generate": 1}  # retrieval ran once
    retrieved, generate_input = steps[2]["output"]["context"], steps[3]["input"]["context"]
    assert retrieved == generate_input  # the generation step got exactly what retrieval produced
    assert result.outcome == "generated" and all(s["status"] == "ok" for s in steps)


def test_eval_2_an_empty_question_never_reaches_retrieval(monkeypatch, agent, engine):
    pipeline = Pipeline(monkeypatch)
    result = runner.run_agent("   \n ", graph=agent, engine=engine, run_id="eval-2")
    steps = runner.run_trace("eval-2", engine=engine)
    assert nodes_of(steps) == ["receive_question", "reject_empty"]
    assert "retrieve_context" not in nodes_of(steps) and "generate" not in nodes_of(steps)
    assert pipeline.calls == {"retrieve": 0, "generate": 0}
    assert result.outcome == "empty_question" and "question" in result.answer.lower()


def test_eval_3_nothing_above_the_threshold_routes_to_an_honest_answer_without_the_model(monkeypatch, agent, engine):
    pipeline = Pipeline(monkeypatch, context=[])
    result = runner.run_agent("What is the weather in Zaragoza?", graph=agent, engine=engine, run_id="eval-3")
    steps = runner.run_trace("eval-3", engine=engine)
    assert nodes_of(steps) == ["receive_question", "plan_sources", "retrieve_context", "no_information"]
    assert "generate" not in nodes_of(steps) and pipeline.calls["generate"] == 0  # no model call on empty context
    assert steps[2]["output"]["context"] == []
    assert result.outcome == "no_context" and "don't have information" in result.answer


def test_eval_4_the_answer_stays_grounded_in_the_knowledge_base_of_the_context(monkeypatch, agent, engine):
    """A known policy question must reach generation with the chunk that holds the fact (30 days), found by the REAL retrieve() over the
    REAL chunks of docs/company-knowledge-base/ (an in-memory Qdrant and a toy embedder: this checks the wiring of the graph, the
    quality of a real embeddings model is measured by scripts/evaluate_rag.py). The model is a faithful stub that can only repeat what
    its prompt contains, so the 30 days can only appear in the answer if the graph delivered the right chunk."""
    qdrant = QdrantClient(":memory:")
    knowledge_base.setup(qdrant, embed_fn=fake_embed)
    real_retrieve = rag.retrieve
    monkeypatch.setattr(rag, "retrieve", lambda question, **kw: real_retrieve(question, k=3, min_score=0.1, client=qdrant, embed_fn=fake_embed))
    prompts = []

    def faithful_model(path, payload):
        prompt = payload["messages"][1]["content"]
        prompts.append(prompt)
        fact = re.search(r"Standard return window: [^\n]*", prompt)
        return {"choices": [{"message": {"content": (fact.group(0) if fact else "I don't have that information.") + "\nSource: returns-policy"}}]}

    monkeypatch.setattr(model_client, "post", faithful_model)
    monkeypatch.setenv("LLM_MODEL", "chat-model")
    result = runner.run_agent("What's the standard return window? How many days after delivery?", graph=agent, engine=engine, run_id="eval-4")
    steps = runner.run_trace("eval-4", engine=engine)
    retrieved = [(c["source_document"], c["chunk_index"]) for c in steps[2]["output"]["context"]]
    assert ("returns-policy", 1) in retrieved  # the chunk that holds the fact was retrieved and traced
    assert "returns-policy" in prompts[0] and "30 days from delivery" in prompts[0]  # ... and given to the generation step
    assert "30 days" in result.answer and result.outcome == "generated"


@pytest.mark.skipif(not (os.getenv("LLM_API_URL") and os.getenv("LLM_API_KEY") and os.getenv("EMBEDDING_MODEL") and os.getenv("LLM_MODEL")), reason="needs the real models (LLM_API_URL, LLM_API_KEY, EMBEDDING_MODEL, LLM_MODEL) and an indexed Qdrant")
@pytest.mark.parametrize(
    "question, must_contain, must_not_contain",
    [
        ("What's the standard return window?", ["30 days"], []),
        ("Can I offer a client a storage discount?", ["Miguel Torres"], []),
        ("Which carrier best covers rural Aragón?", ["SEUR"], []),
        ("Can we guarantee delivery times during Black Friday?", ["not guarantee"], ["we guarantee"]),
        ("Are international returns automatic?", ["manual"], ["automatic process"]),
    ],
)
def test_eval_5_live_grounding_against_the_real_models(agent, engine, question, must_contain, must_not_contain):
    result = runner.run_agent(question, graph=agent, engine=engine)
    steps = runner.run_trace(result.run_id, engine=engine)
    assert nodes_of(steps)[:3] == ["receive_question", "plan_sources", "retrieve_context"]
    answer = result.answer.lower()
    assert all(text.lower() in answer for text in must_contain), result.answer
    assert not any(text.lower() in answer for text in must_not_contain), result.answer


def test_eval_6_a_failure_is_in_the_trace_and_the_run_resumes_from_its_checkpoint(monkeypatch, agent, engine):
    pipeline = Pipeline(monkeypatch)
    pipeline.fail_generation_times = 1
    with pytest.raises(model_client.ModelServiceError):
        runner.run_agent("What's the standard return window?", graph=agent, engine=engine, run_id="eval-6")
    steps = runner.run_trace("eval-6", engine=engine)
    assert nodes_of(steps) == ["receive_question", "plan_sources", "retrieve_context", "generate"]
    assert [s["status"] for s in steps] == ["ok", "ok", "ok", "error"] and "ModelServiceError" in steps[3]["error"]
    resumed = runner.resume("eval-6", graph=agent, engine=engine)
    steps = runner.run_trace("eval-6", engine=engine)
    assert nodes_of(steps) == ["receive_question", "plan_sources", "retrieve_context", "generate", "generate"]  # only generation ran again
    assert pipeline.calls == {"retrieve": 1, "generate": 2}  # retrieval did NOT run again
    assert resumed.outcome == "generated" and steps[-1]["status"] == "ok"


# ================================================================== the state is minimal and explicit
def test_the_state_is_explicit_and_carries_no_conversation_history():
    # part 1 had four fields; part 2 added `plan` and `live_data`; the memory milestone adds the ones of its own step (tests/pipelines/test_agent_memory.py): each one is read by a later node
    memory_fields = {"conversation_id", "actor", "memory_decision", "memory_notice", "memory_candidate", "memory_proposal", "memories_used"}
    assert set(AgentState.__annotations__) == {"question", "plan", "live_data", "context", "answer", "outcome"} | memory_fields
    assert not {"messages", "history", "conversation"} & set(AgentState.__annotations__)


def test_each_node_has_one_responsibility_and_works_alone():
    assert agent_graph.receive_question({"question": "  hi  "}) == {"question": "hi"}
    assert agent_graph.reject_empty({})["outcome"] == "empty_question"
    assert agent_graph.no_information({})["outcome"] == "no_context" and "don't have information" in agent_graph.no_information({})["answer"]
    assert agent_graph.after_receive({"question": ""}) == "empty" and agent_graph.after_receive({"question": "q"}) == "plan"
    rag_only = {"plan": {"steps": ["rag"]}}
    assert agent_graph.after_retrieve({**rag_only, "context": []}) == "no_information" and agent_graph.after_retrieve({**rag_only, "context": [GENERATE_CHUNK]}) == "generate"


# ================================================================== edges depend on what the nodes produced
def test_the_edges_after_receive_and_after_retrieve_are_conditional(agent):
    edges = agent.get_graph().edges
    conditional = {(e.source, e.target) for e in edges if e.conditional}
    assert {("receive_question", "reject_empty"), ("receive_question", "plan_sources"), ("plan_sources", "lookup_ticket"), ("plan_sources", "retrieve_context"), ("plan_sources", "ask_clarification"), ("lookup_ticket", "retrieve_context"), ("lookup_ticket", "answer_unavailable"), ("retrieve_context", "generate"), ("retrieve_context", "no_information")} <= conditional
    assert not any(e.source == "receive_question" and not e.conditional for e in edges)  # no fixed straight line out of receive


def test_the_same_graph_takes_different_paths_for_different_inputs(monkeypatch, agent, engine):
    paths = set()
    for context, question in (([GENERATE_CHUNK], "q"), ([], "q"), ([GENERATE_CHUNK], "")):
        Pipeline(monkeypatch, context=context)
        result = runner.run_agent(question, graph=agent, engine=engine)
        paths.add(tuple(nodes_of(runner.run_trace(result.run_id, engine=engine))))
    assert len(paths) == 3


# ================================================================== compile: structural mistakes fail clearly
def _complete():
    return agent_graph.build_graph()


def _with_orphan():
    builder = _complete()
    builder.add_node("orphan", lambda state: {})
    builder.add_edge("orphan", END)
    return builder


def _with_silent_end():
    builder = StateGraph(AgentState)
    for name in (agent_graph.RECEIVE, agent_graph.RETRIEVE, agent_graph.GENERATE):
        builder.add_node(name, lambda state: {})
    builder.add_edge(START, agent_graph.RECEIVE)
    builder.add_edge(agent_graph.RECEIVE, agent_graph.RETRIEVE)
    builder.add_edge(agent_graph.RETRIEVE, agent_graph.GENERATE)  # generate leads nowhere
    return builder


def _with_edge_to_unknown_node():
    builder = _complete()
    builder.add_edge(agent_graph.GENERATE, "nowhere")
    return builder


def _without_the_required_nodes():
    builder = StateGraph(AgentState)
    builder.add_node(agent_graph.RECEIVE, lambda state: {})
    builder.add_edge(START, agent_graph.RECEIVE)
    builder.add_edge(agent_graph.RECEIVE, END)
    return builder


def _routing_to_a_missing_node():
    builder = _complete()
    builder.add_conditional_edges(agent_graph.GENERATE, lambda state: "x", {"x": "nowhere"})
    return builder


def _without_entry_point():
    builder = StateGraph(AgentState)
    builder.add_node(agent_graph.RECEIVE, lambda state: {})
    builder.add_edge(agent_graph.RECEIVE, END)
    return builder


@pytest.mark.parametrize(
    "make, message",
    [
        (_with_orphan, "nothing leads to: orphan"),
        (_with_silent_end, "no outgoing edge: generate"),
        (_with_edge_to_unknown_node, "unknown node"),
        (_without_the_required_nodes, "missing required node"),
        (_routing_to_a_missing_node, "unknown target"),
        (_without_entry_point, "not valid"),
    ],
)
def test_a_structural_mistake_fails_at_build_time_with_a_clear_error(make, message):
    with pytest.raises(AgentBuildError, match=message):
        agent_graph.compile_graph(make(), MemorySaver())


def test_the_real_graph_compiles_and_the_api_compiles_it_at_startup():
    assert agent_graph.compile_graph(agent_graph.build_graph(), MemorySaver()) is not None
    from services import main

    assert "validate_agent()" in inspect.getsource(main.lifespan)  # compiled before the first request, not on it


# ================================================================== checkpointing
def test_a_checkpoint_is_saved_after_every_node(monkeypatch, agent, engine):
    Pipeline(monkeypatch)
    runner.run_agent("What's the standard return window?", graph=agent, engine=engine, run_id="cp-1")
    saved = runner.checkpoints("cp-1", graph=agent)
    assert [c["next"] for c in saved] == [["__start__"], ["receive_question"], ["plan_sources"], ["retrieve_context"], ["generate"], []]
    assert "plan" in saved[3]["state_keys"] and "context" in saved[4]["state_keys"] and "answer" in saved[-1]["state_keys"]


def test_checkpoints_persist_in_a_sqlite_file_and_survive_a_restart(monkeypatch, engine, tmp_path):
    Pipeline(monkeypatch)
    path = tmp_path / "agent-checkpoints.sqlite"
    first = agent_graph.compile_graph(agent_graph.build_graph(), runner.make_checkpointer(str(path)))
    runner.run_agent("What's the standard return window?", graph=first, engine=engine, run_id="cp-2")
    second = agent_graph.compile_graph(agent_graph.build_graph(), runner.make_checkpointer(str(path)))  # a new process would do this
    state = second.get_state({"configurable": {"thread_id": "cp-2"}})
    assert state.values["outcome"] == "generated" and state.values["context"][0]["chunk_index"] == 1
    assert sqlite3.connect(path).execute("select count(*) from checkpoints").fetchone()[0] >= 4


# ================================================================== the trace is queryable after the run
def test_the_trace_is_stored_and_can_be_read_after_the_run_with_inputs_and_outputs(monkeypatch, agent, engine):
    Pipeline(monkeypatch)
    runner.run_agent("What's the standard return window?", graph=agent, engine=engine, run_id="tr-1")
    steps = read_trace(engine, "tr-1")  # a fresh read from the database: nothing is kept in memory
    assert [s["step"] for s in steps] == [1, 2, 3, 4] and all(s["duration_ms"] >= 0 and s["started_at"] for s in steps)
    assert steps[0]["input"]["question"] == "What's the standard return window?" and steps[0]["output"] == {"question": "What's the standard return window?"}
    assert steps[1]["output"]["plan"]["steps"] == ["rag"] and steps[1]["output"]["plan"]["reason"]  # the plan, and why, are in the trace
    assert steps[2]["output"]["context"][0] == {"source_document": "returns-policy", "section": "Returns Policy > Standard return window", "chunk_index": 1, "score": 0.91}
    assert steps[3]["output"]["outcome"] == "generated" and "30 days" in steps[3]["output"]["answer"]
    assert "Standard return window: 30 days" not in str(steps[2])  # chunk text stays out of the trace
    assert read_trace(engine, "another-run") == []


def test_two_runs_have_separate_traces(monkeypatch, agent, engine):
    Pipeline(monkeypatch)
    runner.run_agent("q one", graph=agent, engine=engine, run_id="a")
    runner.run_agent("q two", graph=agent, engine=engine, run_id="b")
    assert read_trace(engine, "a")[0]["input"]["question"] == "q one" and read_trace(engine, "b")[0]["input"]["question"] == "q two"


# ================================================================== the nodes reuse the RAG pipeline and never wrap query()
def _calls_in(function) -> set[str]:
    return {f"{node.func.value.id}.{node.func.attr}" for node in ast.walk(ast.parse(inspect.getsource(function))) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)}


def test_retrieve_and_generate_are_separate_nodes_that_call_the_pipeline_and_none_wraps_query():
    assert "rag.retrieve" in _calls_in(agent_graph.retrieve_context) and "rag.generate_answer" not in _calls_in(agent_graph.retrieve_context)
    assert "rag.generate_answer" in _calls_in(agent_graph.generate) and "rag.retrieve" not in _calls_in(agent_graph.generate)
    source = Path(agent_graph.__file__).read_text(encoding="utf-8")
    assert "rag.query" not in source and "import query" not in source  # no node re-wraps the monolithic query()
    assert not any(token in source for token in ("query_points", "chat/completions", "embed("))  # the pipeline is reused, not rewritten


def test_the_generation_node_passes_the_context_of_the_retrieve_node_unchanged(monkeypatch, agent, engine):
    pipeline = Pipeline(monkeypatch)
    runner.run_agent("What's the standard return window?", graph=agent, engine=engine)
    assert pipeline.generated_with == ("What's the standard return window?", [GENERATE_CHUNK])


# ================================================================== PART 2: routing between the RAG and the live tools (criteria on the trace)
FAKE_TICKET = {"id": 482, "title": "Parcel damaged in transit", "description": "The customer reports a crushed box.", "category": "delivery_failure", "status": "in_progress", "origin": "customer", "branch": "central", "created_at": "2026-11-20T09:00:00Z", "updated_at": "2026-11-21T10:30:00Z"}
FAKE_PRODUCT = {"id": 1, "name": "Blue Hoodie", "sku": "HOOD-BLU-M", "client_name": "Fashion Co", "category": "fashion", "warehouse": "ZGZ", "current_stock": 42}


def install_fake_mcp(monkeypatch):
    """The MCP server as the agent sees it: the same structured answers, no network. (tests/mcps/test_agent_mcp_integration.py runs the real chain.)"""
    from services.agent import mcp_client

    def call_tool(name, arguments, **kwargs):
        if name == "get_ticket":
            if arguments["ticket_id"] == 482:
                return dict(FAKE_TICKET)
            raise mcp_client.McpError("NOT_FOUND", "not found")
        if name == "query_inventory":
            found = [FAKE_PRODUCT] if arguments["product_query"].lower() in "blue hoodie hood-blu-m fashion co" else []
            return {"operation": "search", "count": len(found), "products": found}
        raise AssertionError(f"the agent must not call {name}")

    monkeypatch.setattr(mcp_client, "call_tool", call_tool)


@pytest.fixture
def services(engine, monkeypatch):
    """The live systems, reached through a fake MCP server; the trace is stored in the test database."""
    install_fake_mcp(monkeypatch)
    return engine


def tools_must_not_run(monkeypatch):
    from services.agent import tools

    monkeypatch.setattr(tools, "lookup_ticket", lambda *a, **k: pytest.fail("a policy question must not call a tool"))
    monkeypatch.setattr(tools, "lookup_stock", lambda *a, **k: pytest.fail("a policy question must not call a tool"))


def test_eval_7_a_live_ticket_question_is_resolved_with_the_tool_and_not_the_rag(monkeypatch, agent, services):
    pipeline = Pipeline(monkeypatch)
    result = runner.run_agent("What's the status of ticket 482?", graph=agent, engine=services, run_id="eval-7")
    steps = runner.run_trace("eval-7", engine=services)
    assert nodes_of(steps) == ["receive_question", "plan_sources", "lookup_ticket", "generate"]
    assert "retrieve_context" not in nodes_of(steps) and pipeline.calls["retrieve"] == 0  # the RAG was never consulted
    assert steps[1]["output"]["plan"]["steps"] == ["ticket"] and steps[1]["output"]["plan"]["ticket_id"] == 482
    lookup = steps[2]["output"]["live_data"][0]
    assert lookup["tool"] == "ticket_lookup" and lookup["status"] == "ok" and lookup["summary"]["status"] == "in_progress"
    live_block = pipeline.generated_with[1][0]  # what the generation step was given: the live record, with its own source
    assert live_block["source_document"] == "incident-manager" and "status in_progress" in live_block["text"]
    assert result.sources == ("tool:ticket_lookup",) and result.outcome == "generated"


def test_eval_8_a_policy_question_is_resolved_with_the_rag_and_not_a_tool(monkeypatch, agent, services):
    pipeline = Pipeline(monkeypatch)
    tools_must_not_run(monkeypatch)
    result = runner.run_agent("What's the standard return window?", graph=agent, engine=services, run_id="eval-8")
    steps = runner.run_trace("eval-8", engine=services)
    assert nodes_of(steps) == ["receive_question", "plan_sources", "retrieve_context", "generate"]
    assert not {"lookup_ticket", "lookup_stock"} & set(nodes_of(steps))  # no tool ran
    assert steps[1]["output"]["plan"]["steps"] == ["rag"] and pipeline.calls["retrieve"] == 1
    assert result.sources == ("rag",)


def test_eval_9_a_question_that_needs_both_uses_the_tool_first_and_then_the_rag(monkeypatch, agent, services):
    pipeline = Pipeline(monkeypatch)
    result = runner.run_agent("Ticket 482 is about a damaged parcel, who pays the return shipping?", graph=agent, engine=services, run_id="eval-9")
    steps = runner.run_trace("eval-9", engine=services)
    assert nodes_of(steps) == ["receive_question", "plan_sources", "lookup_ticket", "retrieve_context", "generate"]  # the order is in the trace
    assert result.sources == ("tool:ticket_lookup", "rag")
    blocks = pipeline.generated_with[1]
    assert [b["source_document"] for b in blocks] == ["incident-manager", "returns-policy"]  # the live record and the policy, given together


def test_eval_10_when_the_incident_service_fails_the_agent_says_it_could_not_confirm_and_makes_nothing_up(monkeypatch, agent, services):
    from services.agent import tools

    pipeline = Pipeline(monkeypatch)
    monkeypatch.setattr(tools, "_read_ticket", lambda ticket_id: (_ for _ in ()).throw(ConnectionError("incident manager down")))
    result = runner.run_agent("What's the status of ticket 482?", graph=agent, engine=services, run_id="eval-10")
    steps = runner.run_trace("eval-10", engine=services)
    assert nodes_of(steps) == ["receive_question", "plan_sources", "lookup_ticket", "answer_unavailable"]  # the recovery path, not a crash
    assert steps[2]["status"] == "ok" and steps[2]["output"]["live_data"][0]["status"] == "unavailable"  # the failure is data, the run goes on
    assert "couldn't confirm ticket 482's status right now" in result.answer and result.outcome == "tool_unavailable"
    assert pipeline.calls == {"retrieve": 0, "generate": 0}  # no model was asked to answer from nothing
    assert not any(status in result.answer for status in ("in_progress", "resolved", "open", "discarded"))  # no status invented


def test_eval_11_a_timeout_and_a_missing_ticket_take_the_same_honest_path(monkeypatch, agent, services):
    from services.agent import tools

    Pipeline(monkeypatch)
    monkeypatch.setattr(tools, "_read_ticket", lambda ticket_id: (_ for _ in ()).throw(tools.ToolTimeout("no answer within 4 seconds")))
    timeout = runner.run_agent("What's the status of ticket 482?", graph=agent, engine=services, run_id="eval-11a")
    assert runner.run_trace("eval-11a", engine=services)[2]["output"]["live_data"][0]["status"] == "timeout" and "couldn't confirm" in timeout.answer
    monkeypatch.undo()
    Pipeline(monkeypatch)
    install_fake_mcp(monkeypatch)
    missing = runner.run_agent("What's the status of ticket 99999?", graph=agent, engine=services, run_id="eval-11b")
    steps = runner.run_trace("eval-11b", engine=services)
    assert nodes_of(steps)[-1] == "answer_unavailable" and steps[2]["output"]["live_data"][0]["status"] == "not_found"
    assert "couldn't find a ticket 99999" in missing.answer and missing.outcome == "tool_unavailable"


def test_eval_12_if_the_tool_fails_but_the_question_also_needs_the_rag_the_rag_part_is_still_answered(monkeypatch, agent, services):
    from services.agent import tools

    pipeline = Pipeline(monkeypatch)
    monkeypatch.setattr(tools, "_read_ticket", lambda ticket_id: (_ for _ in ()).throw(ConnectionError("down")))
    result = runner.run_agent("Ticket 482 is about a damaged parcel, who pays the return shipping?", graph=agent, engine=services, run_id="eval-12")
    steps = runner.run_trace("eval-12", engine=services)
    assert nodes_of(steps) == ["receive_question", "plan_sources", "lookup_ticket", "retrieve_context", "generate"]
    failure_block, policy_block = pipeline.generated_with[1]
    assert "UNKNOWN" in failure_block["text"] and "could not be completed" in failure_block["text"] and "in_progress" not in failure_block["text"]
    assert policy_block["source_document"] == "returns-policy" and result.outcome == "generated"


def test_eval_13_a_ticket_question_without_a_number_asks_for_it_and_reads_nothing(monkeypatch, agent, services):
    pipeline = Pipeline(monkeypatch)
    tools_must_not_run(monkeypatch)
    result = runner.run_agent("Any update on my ticket?", graph=agent, engine=services, run_id="eval-13")
    steps = runner.run_trace("eval-13", engine=services)
    assert nodes_of(steps) == ["receive_question", "plan_sources", "ask_clarification"]
    assert pipeline.calls == {"retrieve": 0, "generate": 0} and result.outcome == "needs_ticket_id" and "ticket number" in result.answer
    assert result.sources == ()


def test_eval_14_a_stock_question_is_resolved_with_the_inventory_tool(monkeypatch, agent, services):
    pipeline = Pipeline(monkeypatch)
    result = runner.run_agent("Do we have stock of Blue Hoodie?", graph=agent, engine=services, run_id="eval-14")
    steps = runner.run_trace("eval-14", engine=services)
    assert nodes_of(steps) == ["receive_question", "plan_sources", "lookup_stock", "generate"] and pipeline.calls["retrieve"] == 0
    assert steps[2]["output"]["live_data"][0]["summary"] == {"matches": [{"sku": "HOOD-BLU-M", "warehouse": "ZGZ", "current_stock": 42}]}
    block = pipeline.generated_with[1][0]
    assert block["source_document"] == "inventory-manager" and "42 units in stock" in block["text"]
    assert result.sources == ("tool:stock_lookup",)


def test_the_trace_tells_which_sources_were_used_and_in_what_order(monkeypatch, agent, services):
    from services.agent.trace import sources_used

    Pipeline(monkeypatch)
    expected = {
        "What's the status of ticket 482?": ["tool:ticket_lookup"],
        "What's the standard return window?": ["rag"],
        "Ticket 482 is about a damaged parcel, who pays the return shipping?": ["tool:ticket_lookup", "rag"],
        "stock of Blue Hoodie and what's the storage price?": ["tool:stock_lookup", "rag"],
    }
    for question, sources in expected.items():
        result = runner.run_agent(question, graph=agent, engine=services)
        assert sources_used(runner.run_trace(result.run_id, engine=services)) == sources == list(result.sources)
    assert sources_used([]) == []
    assert sources_used([{"node": "receive_question"}, {"node": "retrieve_context"}, {"node": "retrieve_context"}]) == ["rag"]  # each source once


def test_the_tool_nodes_call_only_their_own_tool_and_never_the_rag():
    assert "tools.lookup_ticket" in _calls_in(agent_graph.lookup_ticket) and "rag.retrieve" not in _calls_in(agent_graph.lookup_ticket)
    assert "tools.lookup_stock" in _calls_in(agent_graph.lookup_stock) and "rag.retrieve" not in _calls_in(agent_graph.lookup_stock)
    assert "tools.lookup_stock" not in _calls_in(agent_graph.lookup_ticket) and "tools.lookup_ticket" not in _calls_in(agent_graph.lookup_stock)
    assert "rag.generate_answer" not in _calls_in(agent_graph.lookup_ticket)
