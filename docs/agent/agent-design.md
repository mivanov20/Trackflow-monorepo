# Support agent with LangGraph (part 1)

The RAG pipeline of milestone 7 (`retrieve`, `generate_answer`, `query`) now runs as an explicit graph: state, nodes, conditional edges, a compile step, a checkpoint after every node and a trace of every run. The RAG logic is **reused, not rewritten**: the nodes call `data.pipelines.rag.retrieve` and `data.pipelines.rag.generate_answer`.

```
                 +--> reject_empty ------------------------------> END     the question is empty
 START -> receive_question
                 +--> retrieve_context --+--> generate -----------> END     something cleared min_score
                                         +--> no_information -----> END     nothing did: honest answer, no model call
```

Code: `services/agent/graph.py` (state, nodes, edges, compile), `services/agent/trace.py` (trace), `services/agent/runner.py` (run, resume, checkpoints), `services/routers/agent.py` (endpoint).

## State: four fields, no history

| Field | Meaning |
|---|---|
| `question` | what the user asked (trimmed by the first node) |
| `context` | the chunks `retrieve` returned (empty when none clears the threshold) |
| `answer` | the final text, from the generation model or from `no_information` / `reject_empty` |
| `outcome` | `generated` / `no_context` / `empty_question` |

A node needs nothing else to decide the next step. There is **no conversation history**: the agent answers one question at a time, and the knowledge base is the only memory it uses (RAG is a knowledge base the system consults, not memory of the conversation). Adding a `messages` field is a decision for part 2 with its own reason; a test fails if one appears by accident.

## Nodes (one responsibility each)

| Node | Does |
|---|---|
| `receive_question` | takes the question in and trims it; decides nothing |
| `retrieve_context` | calls `rag.retrieve(question)`, once |
| `generate` | calls `rag.generate_answer(question, context)` with the context the previous node produced |
| `no_information` | answers "I don't have information about that...", **without calling the model**, so it cannot invent a company fact |
| `reject_empty` | answers with a clear request for a question |

No node calls `rag.query()`: that would run retrieval again and hide the step that the graph exists to show. A test reads the source of the nodes and fails if one does. `query()` stays as it is for the plain endpoint `POST /knowledge/query`, which coexists.

## Edges

Both decisions are conditional edges that read what the previous node produced: after `receive_question` (empty question -> `reject_empty`, otherwise `retrieve_context`) and after `retrieve_context` (context -> `generate`, nothing above `min_score` -> `no_information`). There is no fixed line from the first node to the last; the same graph takes three different paths for three different inputs (tested).

## Compile, before any execution

`compile_graph()` compiles the graph (LangGraph checks entry point, edges and routing targets) and then adds structural checks: required nodes present, every node reachable from the start, every node able to reach `END`, and **no node with a silent end** (LangGraph treats a node with no outgoing edge as an implicit end; here every way out must be written). Any failure is an `AgentBuildError` with a clear message. The API compiles the graph in its startup (`validate_agent()` in the lifespan), so a structural mistake stops the service from starting instead of failing on a user's question. Six kinds of mistake are tested (orphan node, node with no exit, edge to an unknown node, missing required nodes, routing to a missing node, no entry point).

## Checkpointing

The graph is compiled with a checkpointer and every run is a thread (`thread_id` = `run_id`): a checkpoint is saved after **every node** (`__start__`, `receive_question`, `retrieve_context`, `generate`, end; tested). With `AGENT_CHECKPOINT_DB` set (the compose file sets `/data/agent-checkpoints.sqlite`) they are written to a SQLite file and survive a restart (tested by opening the file with a second graph); with it unset they live in memory. `runner.checkpoints(run_id)` lists them and `runner.resume(run_id)` continues a run that stopped at a failed node from its last checkpoint: if only `generate` failed, retrieval is **not** run again (tested: 1 retrieval, 2 generations).

## Trace

Every node writes a row to `agent_trace_steps` (SQL in `services/sql/agent_trace_steps.sql`; the API creates the table) when it ends, successfully or not: run id, step number, node, `ok` / `error`, start time, duration, input and output summaries, error. It is **queryable after the run**: `runner.run_trace(run_id)` or `GET /agent/runs/{run_id}/trace` (admin or manager) returns the ordered steps and the checkpoints. Summaries keep company text out: the retrieved chunks appear as source, section, index and score, never their text. A failed run keeps the steps that happened and the failing one with its error, and the `503` the client receives carries the run id as a reference to find it.

Not LangSmith: no account was assumed, so the trace is our own structured table. It is a few lines to also send the same events to LangSmith later.

## Evals (`tests/pipelines/test_agent_evals.py`)

One command: `python -m pytest tests/pipelines/test_agent_evals.py`. The evals assert on the trace read back from the database:

1. a normal question runs `receive_question`, `retrieve_context`, `generate` in that order, retrieval once, and the generation step receives exactly the chunks retrieval produced;
2. an empty question runs only `receive_question`, `reject_empty`, and neither retrieval nor generation;
3. nothing above the threshold runs `receive_question`, `retrieve_context`, `no_information`, with no model call, and says so;
4. **grounding**: the real `retrieve()` over the real chunks of `docs/company-knowledge-base/` finds the return-window chunk for a policy question, the trace shows it, and the generation step receives it; the answer contains the 30 days (the model is a faithful stub that can only repeat its prompt, so the fact can only appear if the graph delivered the right chunk; the quality of a real embeddings model is measured by `scripts/evaluate_rag.py`);
5. **live grounding** (skipped unless the real models and an indexed Qdrant are configured): five policy questions against the real models assert the facts of the context (30 days, Miguel Torres, SEUR, no SLA on Black Friday, international returns manual);
6. a failed run is in the trace with its error and resumes from its checkpoint.

The existing RAG tests (`tests/pipelines/test_rag.py`) are untouched and must still pass.

## Endpoint

`POST /agent/query` `{ "question": "..." }` -> `{ "answer": "...", "run_id": "..." }`. It only invokes the graph (a test fails if the router mentions retrieval, Qdrant, embeddings or the generation function). An empty question is a `422` decided by the graph; a failure in any node is a `503` with a generic message and the run id, never a stack trace, a model service address or a key.

## Limits

- The live grounding evals (item 5) have not been run in this repository's tests because no real model was available; item 4 proves the wiring, not the model.
- The trace is stored in the application database and is as durable as it; there is no retention policy yet.
