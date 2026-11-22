# Support agent, part 2: live tools and routing

Part 1 ([agent-design.md](agent-design.md)) turned the RAG into a compiled, checkpointed, traced graph. The agent still knew one thing: the knowledge base. Support also runs on data that changes all the time ("what's the status of ticket 482?", "do we have stock of X?"), and that lives in the incident manager and the inventory manager. Those answers are **not** indexed into the vector store: an indexed ticket is stale the moment someone changes its status. The agent now has two tools that read the real services at question time, and it decides by itself when to use them. (Since the MCP milestone the tools reach those services only through the TrackFlow MCP server, see the section at the end; the contracts, timeout and recovery paths below are unchanged.)

```
 receive_question --empty--> reject_empty ------------------------------------------------------> END
        v
  plan_sources --ticket question without a number--> ask_clarification ---------------------------> END
        v      (decides the sources from the question; tools first, then the RAG)
  lookup_ticket (incident manager) -> lookup_stock (inventory manager) -> retrieve_context (RAG)
        |  each step is optional: the plan says which ones run
        +--> generate ------------------ something to answer from (a live result or a chunk) ----> END
        +--> answer_unavailable --------- only failed lookups: a fixed honest message, no model --> END
        +--> no_information ------------- nothing found, nothing failed --------------------------> END
```

State grows from four fields to six: `plan` (what the planner decided) and `live_data` (what the tools returned). Each is read by a later node, and there is still no conversation history.

## The tools (`services/agent/tools.py`)

| | `lookup_ticket` | `lookup_stock` (stretch) |
|---|---|---|
| Reads (through the MCP server) | the incident manager (`get_ticket`) | the inventory manager (`query_inventory`, operation `search`) |
| Input contract | `TicketLookupInput{ticket_id: int > 0}` | `StockLookupInput{product_query: 2-120 chars}` |
| Output contract | `ToolResult` whose `data` is the incident API's own `IncidentRead` (id, title, description, category, status, origin, branch, created_at, updated_at) | `ToolResult` whose `data` is a list of the inventory API's own `SKURead` (name, sku, client, category, warehouse, `current_stock`) |
| Statuses | `ok`, `not_found`, `timeout`, `unavailable` | the same |

- **Real data, through the MCP server.** The tools call the MCP tools `get_ticket` and `query_inventory` of `mcps/trackflow_tools`, which read the real incident and inventory APIs over HTTP. There is no parallel dataset and no in-process database read left in the agent.
- **Auth.** Every call carries an OAuth access token (client-credentials grant) with only the scopes `tickets:read inventory:read`; credentials come from the environment (`AGENT_MCP_URL`, `AGENT_OAUTH_ISSUER`, `AGENT_OAUTH_CLIENT_ID`, `AGENT_OAUTH_CLIENT_SECRET`).
- **Read-only.** The tools only call the read tools. A test fails if the agent's code mentions `create_ticket` or `update_ticket_status`, and a test with the real server shows that the agent's own credential is refused those tools (`[INSUFFICIENT_SCOPE]`) and any inventory write (`[INVENTORY_READ_ONLY]`).
- **Timeout: 4 seconds** (`TOOL_TIMEOUT_SECONDS`, overridable with `AGENT_TOOL_TIMEOUT_SECONDS`). The read runs in a worker thread and the call gives up after the timeout, so a service that does not answer cannot hang the graph (tested with a 2 s read and a 0.2 s timeout: the call returns in under a second). The MCP call itself is bounded by an explicit timeout a quarter of a second shorter, so it is cancelled cleanly first.
- **A failure is a result, not an exception.** A timeout, any error of the service, or a ticket that does not exist comes back as a `ToolResult` with its status. The text of the exception (which can carry an address or a credential) is not passed on.
- **One responsibility each.** Two functions, two input contracts, two reads, two graph nodes. A test fails if the ticket tool touches `SKU` or the stock tool touches `Incident`.

## Routing: the planner (`services/agent/routing.py`)

`plan_sources(question)` decides which sources the question needs, from its content and never from an instruction of the user:

| Question | Plan |
|---|---|
| "What's the status of ticket 482?" | `ticket` |
| "Do we have stock of Blue Hoodie?" | `stock` |
| "What's the standard return window?" | `rag` |
| "Ticket 482 is about a damaged parcel, who pays the return shipping?" | `ticket`, then `rag` |
| "What is the status of the ticket?" (no number) | ask for the number |
| "What's the weather in Zaragoza?" | `rag` (its threshold says "I don't have information") |

It is a set of explicit rules, not a model call, so the same question always takes the same path, the plan and its reason are written to the trace, and it is tested without a model. The price is that unusual wording can be missed: a question with no ticket word, no ticket number and no stock phrase falls back to the RAG, which answers honestly that it has no information. Replacing it with a model-based planner means replacing one function that returns the same plan.

## Recovery paths: no tool failure breaks the run

After every step one rule decides where to go (`next_node`): the next step of the plan; when the plan is done, `generate` if there is something to answer from, `answer_unavailable` if only failed lookups exist, `no_information` otherwise.

- **Only tool, and it failed or found nothing:** `answer_unavailable` returns a fixed honest message ("I couldn't confirm ticket 482's status right now...", "I couldn't find a ticket 99999...") **without calling a model**, so no status or number can be invented.
- **A tool failed but the question also needs the RAG:** the plan continues, the RAG part is answered, and the failure travels to the model as a block that says the value is UNKNOWN and must not be guessed.
- **Live results reach `generate_answer` as context blocks** with their own source (`incident-manager`, `inventory-manager`), so generation, citations and the prompt rules are the same as for the RAG. The prompt has a rule that live values are quoted exactly and that a failed lookup is reported as "could not confirm".

## Trace

The trace shows the plan (with its reason), each tool step (what it was asked, how it ended, and a summary: ticket id, status, category, origin, branch; stock matches; never the free text of the ticket) and the RAG step, in order. `sources_used(trace)` turns that into the list the response carries: `POST /agent/query` returns `sources` (for example `["tool:ticket_lookup", "rag"]`) and the trace endpoint returns `sources_used`.

## Routing evals (`tests/pipelines/test_agent_evals.py`)

7. A live ticket question runs `lookup_ticket` and **not** the RAG (retrieval is never called); the generation step receives the real record.
8. A policy question runs the RAG and **not** a tool (a tool call fails the test).
9. A question that needs both runs the tool first, then the RAG, and the generation step gets the live record and the policy chunk.
10. Fallback: with the incident service failing, the path is `lookup_ticket`, then `answer_unavailable`; no model is called and the answer says it could not confirm and contains no status.
11. A timeout and a missing ticket take the same honest path.
12. A failed tool plus a RAG question still answers the RAG part and tells the model the live value is unknown.
13. A ticket question with no number asks for it and reads nothing.
14. A stock question runs `lookup_stock` and the record reaches generation.

The tools and the planner have their own tests in `tests/pipelines/test_agent_tools.py` (contracts, real rows, read-only, timeout, failures, a table of questions). Run everything with:

```powershell
python -m pytest tests/pipelines/test_agent_evals.py tests/pipelines/test_agent_tools.py
```

## Limits

- The planner is rule-based: it reads English questions about tickets (a number is required) and stock (a product name is extracted from a few phrasings). It is predictable and testable, not a language model.
- The stock tool matches by substring of the product name, SKU or client (done by the MCP tool), and returns at most 5 products.
- The live grounding evals that need real models are still skipped without model settings. The routing evals do not need a model.

---

# The agent as an MCP client

The agent no longer reads the Incidents Manager or the inventory itself. Its two tools call the TrackFlow MCP server (`mcps/trackflow_tools`, documented in its README) as a client, with `langchain-mcp-adapters`, and that server reads the real services. The node that read the incident data in-process was replaced; **the old code is deleted, not kept as a second path**, and tests check that the agent package has no models, service layer or direct HTTP of the managers and that its only door is the MCP client.

```
 agent (graph) -- lookup_ticket / lookup_stock nodes --> tools.py --> mcp_client.py
       --OAuth client-credentials token (scopes: tickets:read inventory:read)--> MCP server --> incident / inventory API
```

- **Routing is unchanged.** The planner, the edges and the recovery paths are the same functions; only what is behind `_read_ticket` and `_read_stock` changed. All the routing evals pass unchanged, and a new integration test (`tests/mcps/test_agent_mcp_integration.py`) runs them against the real chain.
- **Least privilege.** The agent's OAuth client is registered with `tickets:read inventory:read`. Even if its code called `create_ticket`, the server would answer `[INSUFFICIENT_SCOPE]` (tested).
- **Failures stay results.** The MCP server down, the OAuth provider refusing the credentials, a missing configuration or a slow server each become an `unavailable` or `timeout` tool result with a code (`MCP_UNAVAILABLE`, `AUTH_FAILED`, `NOT_CONFIGURED`, `MCP_TIMEOUT`), and the run ends with the same honest message as before. With the server down the agent reads nothing: a test checks that no request reaches the incident API by any other road.
- **Sync graph, async client.** `langchain-mcp-adapters` is async; the graph's nodes are sync and run the call in the worker thread the timeout already used, with `asyncio.run` and an explicit `asyncio.wait_for`.
- **Settings:** `AGENT_MCP_URL`, `AGENT_OAUTH_ISSUER`, `AGENT_OAUTH_CLIENT_ID`, `AGENT_OAUTH_CLIENT_SECRET`, `AGENT_MCP_SCOPES` (`.env.example`, passed on by `docker-compose.yml`).
