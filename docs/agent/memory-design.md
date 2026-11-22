# Agent memory and self-improvement (milestone 8)

The support agent used to start every conversation from zero: a carrier rule corrected on Monday had to be corrected again on Thursday. It now has a small, **user-approved, audited** memory. Valentina Cruz's 15 support agents (Los Angeles and Zaragoza) can correct the agent once and the correction survives, without the memory growing without limit, editing itself behind their back or storing what it must not.

It is the same agent and the same graph: one extra output field in the answer step, three small nodes that run only when memory is involved. The company RAG stays a **read-only tool**; the memory is a separate store.

```
 user message ──► receive_question ──┬─ a proposal is pending? ──► resolve_memory ──► (decision logged; approved ⇒ written)
                                     │                                  │  what is left of the message (another question) continues ▼
                                     └──────────────────────────────────┴──► plan_sources ► (tools / RAG) ► generate ──► propose_memory? ──► answer
                                                                                                           ▲ ONE model call:    └ gates, then "want me to remember this?" appended;
                                                                                                             answer + memory_proposal   recorded as PENDING; nothing written
```

Code: `services/agent/memory/` (`policy.py`, `schema.py`, `store.py`, `decisions.py`, `generation.py`), the nodes in `services/agent/graph.py`, the endpoints in `services/routers/agent.py`. Evidence of the flow: [memory-evidence.md](memory-evidence.md).

## 1. Memory architecture, and why

**Chosen: two SQL tables in the application database** (`agent_memory_entries`: what is remembered; `agent_memory_proposals`: the audit log). Not a RAG collection, not Redis, not a vector store, not a knowledge graph, not fine-tuning.

What TrackFlow's agent needs to remember is a short list of **standing facts filed under an exact key**: a carrier rule for a country, a recurring incident of a country, a client's report preference. That decides the architecture:

| Option | Why not (here) |
|---|---|
| Context window / system prompt | Memory must survive between conversations and between support agents; appending everything to the prompt grows without limit. The prompt receives only the handful of notes relevant *now*, read from the store. |
| Redis / key-value | Gives expiry and speed, but nothing here needs sub-millisecond reads of a few dozen rows, and it cannot make a memory write atomic with its audit record. We would still need a second store for the audit trail. |
| Vector database | Retrieval by cosine similarity blurs exactly what must stay exact: "SEUR in Zaragoza" and "SEUR in Los Angeles" are different rules. The entries are found by the carrier, client and country the question names. (The RAG milestone already uses a vector store for documents; this agent's memory is deliberately not mixed into it.) |
| Knowledge graph | For explicit relationships (dependencies, hierarchies). These facts have none. |
| Fine-tuning | Expensive, slow to update, and it cannot selectively forget. |

SQL fits because the requirements are relational: file by key, consolidate by scope, expire and purge, query who approved what and when, and write an entry **in the same transaction** as the decision that authorised it. The application database already exists, is backed up and access controlled.

**Never written to company RAG collections.** The memory package imports nothing from Qdrant or `data.process.knowledge_base`, never names `trackflow_knowledge`, and its tables are named `agent_memory_*` (tests check all three). Mixing episodic, user-approved facts into curated documents would break the retrieval evals and the poisoning controls.

**An explicit read/write interface, not the system prompt.** `MemoryStore`: `recall(question)` (read), `propose(...)` (records a *pending* proposal), `resolve(...)` (the only way an entry is written), `consolidate()`, `forget(id)`, `audit()`. The system prompt is a constant; the notes that apply to a question are read from the store on every call (at most 5, however large the memory is: tested with 300 entries) and placed in the user message with their approval date.

## 2. What is memorable, and what must NEVER be remembered

From `CONTEXT-company.md`:

| Memorable (the three kinds) | Filed by |
|---|---|
| **`carrier_rule`**: a corrected carrier assignment or coverage rule ("SEUR no longer covers that rural area of Zaragoza") | carrier + country |
| **`incident_context`**: context of a known, **recurring** incident, so the same alert is not re-escalated ("the Los Angeles delays this week are the port strike", reported at least twice) | country + topic; lives 14 days |
| **`client_preference`**: a recurring B2B client's standing preference for its monthly report ("returns breakdown first") | client |

| Never remembered | Enforced by (`policy.py`) |
|---|---|
| Exact **end-customer (B2C) addresses** | `address`: street + number, apartment / floor, ZIP or postcode with its city (English and Spanish formats) |
| Exact addresses and access details of **B2B client sites** (the context asks for both client types) | the same `address` check, plus `warehouse_route_or_access` |
| **Internal warehouse routes** and access details | `warehouse_route_or_access`: aisles, racks, bays, dock doors, gate / alarm / access codes, badges, key boxes, pick paths |
| Data of a **single, non-repeating package incident** | `single_package_incident`: tracking and order numbers, "this package", "one customer complained"; and an incident proposal needs `occurrences ≥ 2` |
| **Active commercial negotiations** (CRM territory) | `commercial_negotiation`: negotiation, contract terms / renewal, discounts, quotes, rebates |
| (extra) emails and phone numbers | `personal_contact` |

The policy is deliberately conservative: it prefers to refuse a legitimate note (the user can restate it without the detail) over keeping a forbidden one. It runs **three times**: on the model's proposal before the user is even asked, on a user's edit of it, and again on the exact text about to be written. Text kept in the **audit log** is redacted with the same patterns, and a proposal blocked by the policy is logged **by category only, never with the text**. The trace never stores what the model suggested, only its kind. Tests cover 17 forbidden examples (B2C, B2B site, routes, single package, negotiation, contact), 8 allowed ones, and an end-to-end check that none of the forbidden examples ever becomes a question to the user.

## 3. Self-evaluation and the proposal (one call)

After the answer, the **same model call** returns an optional `memory_proposal`. No second call, no second agent. The prompt states an explicit criterion: propose **only** if the user, in this interaction, (1) corrected a carrier rule, (2) gave context about a known recurring incident, or (3) stated a standing report preference of a recurring B2B client, **and** it is a standing fact, not about this one conversation. Almost every interaction is `null`; "when in doubt, propose nothing".

Three examples that **should** produce a proposal (from the context) and three that **must not**:

| Should propose | Why |
|---|---|
| "Actually SEUR no longer covers that rural area of Zaragoza, we've had to use the local carrier since last month." | a corrected carrier rule (carrier + country) |
| "Those delays reported in Los Angeles incidents this week are from the port strike, not something on our end, that's the third ticket about it already." | context of a known, recurring incident (3 occurrences) |
| "The cosmetics client always wants their monthly report with the returns breakdown first, before shipment volume." | a standing report preference of a B2B client |

| Must NOT propose | Why |
|---|---|
| "Where's the package with tracking XJ4471?" | a one-off tracking query, not a pattern (and it carries a tracking number) |
| "Great, that's resolved." | a closing remark |
| "Translate this into English for the client." | a single-use task |

The server does not trust the model's judgement blindly (`generation.vet`). Before a proposal becomes a question it must pass: the message is not one of those non-memorable kinds (a closing, a single-use task, a one-off tracking query: **dropped even if the model proposes one**, tested), the proposal is valid and complete for its kind (an incident must be recurring), the forbidden-content policy, it is not already remembered, and no other proposal is pending. If the model's reply is not valid JSON, the whole text is just the answer: a malformed format can never create a proposal.

The proposal reaches the user **inside the same answer**, as a question at the end ("Want me to remember this for next time? > …  Reply yes to save it, no to skip it, or tell me how to change it."). At this step **nothing is written**: the proposal is recorded as `pending`.

## 4. The user's decision: classified, logged, never assumed

When a proposal is pending, the user's next message is first classified against it (`decisions.py`) into one label with a confidence:

| Label | Meaning | Outcome |
|---|---|---|
| `approve` | clearly says to remember it as proposed | written (after the policy check) |
| `edit` | wants it remembered but changed ("yes, but say since March") | **the user's version** is written (after the policy check) |
| `reject` | clearly says not to | discarded; the rejection is logged |
| `unrelated` | moved on to another topic or question | **discarded by default**; the message is answered normally |
| `unclear` | cannot be determined ("maybe", "ok", "sounds good", "yes but...", silence) | **discarded by default** |

Two stages. **Strict rules** decide the clear cases offline and deterministically: a message that *starts* with an unambiguous answer and carries nothing that changes its meaning ("yes", "no thanks", "Yes, remember it. And what is the SLA for express?"). They refuse anything with a contradiction or modification ("yes but...", "no, wait, yes", "yes? I don't know") and do **not** count a bare "ok", "sounds good" or "great" as approval, because after an answer those usually acknowledge the answer, not the proposal. For what the rules cannot read (an edit, a mixed answer) **one structured model call** returns the label, a confidence, the edited text and the rest of the message; its output is validated (known label, confidence, an edit must carry text, the "rest of the message" must really come from the message so the model cannot invent a question). A confident `approve` / `edit` needs confidence ≥ 0.8. If there is no model, it fails, or it answers something invalid, the result is a **discard**. A test lists messages a naive `"yes" in message` check would approve and shows they are not.

- **One pending proposal at a time**, enforced twice: by the store (a second `propose` raises) and by the database (a partial unique index allows one `pending` row per conversation). A second proposal can only be made after the first is closed; the user's next message always closes it first.
- **Silence or a change of topic discards** (`discarded_topic_change`, `discarded_unclear`), never approves.
- **The conversation continues normally**: if the message also asks something ("Yes, remember it. And what is the SLA for express?"), the decision is applied and the rest of the message goes on through the graph to a normal answer, prefixed by what was done.

## 5. The audit log

Every proposal is a row of `agent_memory_proposals`, **approved or not**: the conversation, who it was shown to (`actor`), the redacted message that led to it, the proposal, its status (`approved`, `edited`, `rejected`, `discarded_topic_change`, `discarded_unclear`, `rejected_by_policy`, `blocked_by_policy`), when it was proposed and resolved, the label, confidence and method (rules or model) of the classification, **who decided** (`decided_by`, the signed-in user), the redacted message that decided it, what was finally written and the entry it created. An entry carries the ids of the proposals that created it. The log is never purged by the cleanup. It is readable at `GET /agent/memory/audit` (admin or manager).

## 6. Consolidation and cleanup policy

Memory must not grow without limit. `MemoryStore.consolidate()` runs after every write and can run on a schedule (`python scripts/consolidate_agent_memory.py`):

| Rule | Value | Why |
|---|---|---|
| **Consolidate by carrier + country** (or client) | at most **5** active entries per scope; beyond that the oldest are merged into one consolidated entry (≤ 600 characters), keeping the ids of what it was made from | the context's suggestion: assignment rules must not end up as dozens of loose entries; the 8 carriers over two countries are at most 16 scopes |
| **Deduplicate** | an approved fact ≥ 80% similar to an active one of the same scope *confirms* it (confirmations + 1) instead of adding one; the agent also does not propose what it already knows | the same correction made three times in a week (the ticket) must not become three entries |
| **Expire incident context** | **14 days** | a strike or closure passes; keeping it re-introduces stale alerts. |
| **Expire what nobody needs** | carrier rules and client preferences unused for **180 days** | coverage changes; a rule nobody has recalled in six months is more likely stale than useful |
| **Global ceiling** | **200** active entries; the least useful (fewest confirmations and uses, oldest) go first | a hard bound whatever the usage |
| **Purge** | superseded / expired / forgotten entries are deleted **30 days** after they die | kept for a month for traceability, then removed. The audit log is never purged |
| **Selective forgetting** | `DELETE /agent/memory/{id}` (admin or manager) | an entry stops being used at once; the proposal that created it stays on record |

## 7. Endpoints

`POST /agent/query` accepts an optional `conversation_id` (8–64 characters, `A-Za-z0-9_-`): **send the same id on every message of a conversation to use memory**; without it the agent keeps nothing and proposes nothing. The response adds `conversation_id` and `memory` (`notice`: what was done with a pending proposal; `pending_proposal`: the one this answer asks about; `used`: ids of the memories in the prompt). `GET /agent/memory`, `GET /agent/memory/audit`, `DELETE /agent/memory/{id}` for admin or manager.

## 8. Limits, honestly

- **Governance.** As the milestone brief says, core memory in a real company changes through tickets reviewed by a dedicated team. Here the path is proposal → user confirmation → write, in the agent flow. The authorising user is whoever is signed in; there is no second reviewer.
- **The model's judgement is not measured here.** No live model was available, so how well a real model separates memorable from non-memorable interactions was **not** evaluated; tests script the model and verify everything around it (the gates catch the clearest wrong proposals regardless). The same goes for the model classifier of edits. [memory-evidence.md](memory-evidence.md) states the same.
- **The policy is pattern based.** It is deliberately conservative and covers English and Spanish address formats, but a new way of writing an address or a code could slip through, and it will sometimes refuse a harmless note. It is a backstop, not a guarantee; the three checks, the redacted audit log and the user's confirmation are the layers around it.
- **Memory is shared by the whole CX team**: an approved rule applies to every support agent's conversations, which is the point; a wrong one can be forgotten by a manager.
- **Recall is lexical**: notes are found by the carrier, client and country a question names plus shared words. A question that asks about a rule without naming any of them may miss it. That is the price of not using similarity search for exact rules.
