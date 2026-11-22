# RFP approval and document completion, part 3 (Milestone 9)

Part 2 hands over, for every active department, a draft and its evaluation. Nobody signs a pricing proposal without a human from each department giving the green light, so Part 3 puts **a real
human approval point** in front of each section, lets the departments decide **independently and in parallel**, settles disagreements with the **fixed arbiter of the CONTEXT**, and **generates the final
document by itself** once every active department has approved. Every step is traceable.

CONTEXT-company.md decides who approves: the **named owner of each active department** (Ana Whitfield, Carlos Vega, Sofía Ramos), each one only their own section. There is no approval ladder; the only
other person in the flow is Miguel Torres, the fixed arbiter of section 7, who acts through the arbitration node (below), not as a second approver of every section.

## Where each piece lives

| Piece | Location |
|---|---|
| Department branch graph (interrupt) and coordinator graph (arbitration, final document) | `data/pipelines/rfp_intake/approval_graph.py` |
| Resume entry point, checkpointer, locks, thread ids | `approval_runner.py` (`Workflow.start`, `Workflow.submit`, `Workflow.settle`) |
| Decision validation, limits | `approval.py` |
| Arbitration (triggers, arbiters, resolution rules) | `arbitration.py` |
| Revision after a decision (edited draft evaluated, or redrafted) | `revision.py` |
| Final document | `final_document.py`; table `rfp_final_documents` |
| Persistence of decisions and statuses | `approval_store.py` (+ columns on `rfp_department_sections`, `rfp_tickets`) |
| Trace of every node | `trace.py`; table `rfp_trace_events` |
| HTTP (same API) | `services/routers/rfp.py` |
| Reproducible end to end path | `e2e.py`, `scripts/rfp_e2e.py`, `tests/pipelines/test_rfp_e2e.py` |

## The flow

```
 Part 2 handoff (ready_for_part_3) ──► Workflow.start ──► one branch run per department, each paused at its approval gate; ticket: waiting_for_approval

   branch  rfp-{ticket}:{department}   (own checkpoint, own thread)
     open_gate ─► human_approval ══ INTERRUPT ══ (resume = Command(resume=decision), only on THIS thread)
        ▲              │ invalid decision: asks again
        │              ▼
        │       record_decision ─ approve ──────────────────────► finish_approved ► END
        │              ├─ request changes / reject, rounds left ─► revise ─┐ (owner's edit evaluated, or the department's generator redrafts)
        └──────────────┘                                                    │
                       └─ the limit of rounds is reached ───────► finish_rejected ► END

   when no branch is waiting any more:  coordinator  rfp-{ticket}
     collect ─► arbitrate ─ no conflict ─► finalize ─► final document stored, ticket: done
                   ├─ conflicts, rounds left ─► the arbiter's rule rewrites sections and reopens exactly those branches
                   └─ a rejected section, or conflicts after the limit ─► blocked (ticket: needs_human_review)
```

### The pause is per department

Each department is its own graph run with its own checkpoint (`thread_id = rfp-{ticket_id}:{department_id}`), so **approving B while A is still interrupted** neither waits for A nor touches it: the test
checks that A's checkpoint id is unchanged, that B's branch ended, that the ticket still waits and that no document exists. Two owners deciding at the same time (two threads) do not block each other
either; only `settle`, the step that needs every department, is serialized per ticket. The coordinator runs on `rfp-{ticket_id}`. A ticket's threads never overlap another ticket's (tested with two tickets).

### Checkpoint and resume

`human_approval` calls `interrupt(payload)`; the run stops there and its state is in the checkpoint (SQLite file `RFP_CHECKPOINT_DB`, default `services/data/rfp-checkpoints.sqlite`, `/data/...` in Docker;
`:memory:` for tests). `Workflow.submit` is the **explicit resume entry point**: it validates the decision, then `Command(resume=...)` on that department's thread. Nothing before the interrupt runs again
(tested through the trace: no gate reopened, nothing regenerated, approvals not started twice). A pause survives a restart (tested with a second `Workflow` on the same file). If the checkpoint is lost
(a restored database, a new file) the gate is rebuilt from the section in the database and the decision still goes through. Everything in `human_approval` before `interrupt()` is free of side effects,
because LangGraph re-runs a node from its start on resume.

### The human response is validated before it reaches the graph

`approval.validate_decision`: `decision` is `approve`, `request_changes` or `reject`; the approver must be the department's named owner; a comment (5 to 2,000 characters) is required to reject or request
changes; an edited draft only travels with `request_changes` (it is evaluated again, then the owner approves it); `commitments` accept one structured key per department (warehouse: `capacity_orders_per_month`,
last mile: `assumed_monthly_volume`), a positive whole number. The API calls it first (422 with the reason, 409 if the section is not waiting, 404 for an unknown ticket or department) and the branch calls it
again, so a bad value that bypasses the API only makes the gate ask again. An owner cannot approve a section that still breaks a guideline the arbitration does not cover (GL-2, GL-4, GL-5): the
final document could never satisfy it, so they request changes with an edited draft instead. Guardrails resolve the clear cases; the interrupt is for the judgement.

Who may register a decision: the owners are named people and the backoffice accounts are staff, so a decision is registered by an `admin` or `manager` account (`RFP_APPROVER_ROLES`) on behalf of the owner;
the signed-in account is stored as `decided_by`, the owner as `approver`.

## Limits (in code, configurable)

| Limit | Default | What happens at the limit |
|---|---|---|
| `RFP_MAX_APPROVAL_ROUNDS` (`approval.max_rounds()`) per section: decisions that send it back for a new draft | 3 | the section is `rejected`, the ticket goes to `needs_human_review` (`approval_state = blocked`), no final document |
| `RFP_MAX_ARBITRATION_ROUNDS` (`approval.max_arbitration_rounds()`) per ticket: times the arbiter sends sections back | 2 | the conflicting sections are `rejected` ("unresolved after the iteration limit"), the ticket is blocked |
| `RFP_MAX_DRAFT_ITERATIONS` of Part 2, reused when a section is redrafted | 3 | the draft ships with its failing evaluation (the owner sees `needs_attention`) |

## Arbitration (CONTEXT section 7): a node, deterministic, fixed arbiters

`arbitrate` is a node of the coordinator. `arbitration.detect` reads **structured state**, never free text opinions, and `arbitration.resolve` applies the rule of the CONTEXT. No agent negotiates and no model votes.

| Trigger id | Fires when | Arbiter | Resolution applied |
|---|---|---|---|
| `volume-vs-capacity` | the warehouse's committed capacity (`commitments.capacity_orders_per_month`, stated by its owner) is below the monthly volume last mile assumes (its commitment, or the RFP's volume) | Miguel Torres | the volume is capped to the capacity; last mile is rewritten with the cap (volume, tiers and cost "revised downward") and goes back to its owner |
| `returns-sla-breach` | any section promises returns processing in under 48 hours (rule GL-3) | Sofía Ramos rejects her own section; Miguel Torres for any other section that still embeds it | `reject` / `request_changes` forced on every such section, rewritten without the promise; no final document until compliant |
| `currency-mismatch` | a section quotes a currency that is not the country's (US: USD, Spain: EUR), or sections quote different ones | Miguel Torres | the offending sections are rewritten to the country currency and go back to their owners; rejected if unresolved after the limit |

The decisions are traced under the arbiter's name (`arbiter:Miguel Torres`), stored in the section's approval history, and listed in the final document under "Decisions of the arbiter".
An owner can approve text that the arbitration then sends back (GL-1 and GL-3): the arbitration is the guard that makes the CONTEXT's rules hold for what was *approved*, not only for what was drafted.

## The final document

When no branch waits and the arbitration finds nothing, `finalize` runs one last guard (every guideline of CONTEXT section 5 on the approved text), builds the document and stores it **together with `done`
in one transaction** (`rfp_final_documents`: `ticket_id`, `sections`, `currency`, `generated_at`, plus the Markdown). It contains the approved sections in order, who approved each and when, and the
arbiter's decisions. It is read at `GET /rfp/tickets/{id}/document` (JSON) or `?format=markdown` (a download), and in the backoffice. Items still "to be confirmed" in an approved section stay as the owners approved them:
an owner who wants a price in the document edits the section before approving.

## Statuses

`under_evaluation` / `needs_human_review` (from Part 2) → **`waiting_for_approval`** while any approval is pending (also while a section is redrafted or an arbitration is settling) → **`done`** when the document is
stored. A blocked ticket goes to `needs_human_review` (an existing status) with `approval_state = blocked` and the reason in `error_message`. No other status is invented; `approval_state`
(`not_started`, `queued`, `waiting`, `blocked`, `complete`) says where Part 3 is.

## Traceability

Every node execution of the three parts writes an event to `rfp_trace_events`: **agent, node, a summary of its input, a summary of its output, the department and the time**, with a `seq` that orders them.
`GET /rfp/tickets/{id}/trace[?part=3]` and the "Trace" panel of the backoffice show it; `python scripts/rfp_e2e.py --trace` prints it. Each real change of the ticket's status is also an event, so the status
timeline is read from the same trace (and tested: `analyzing → intake_complete → drafting → under_evaluation → waiting_for_approval → done`, never skipping or going back). The trace is not the checkpoint: the
checkpoint makes a run resumable, the trace tells people what happened.

## The end-to-end review: what it found and what was fixed

The complete path was run (the sample PDFs, uploaded through the UI and through `scripts/rfp_e2e.py` / `tests/pipelines/test_rfp_e2e.py`) looking for jumps and mismatches between parts:

- The same client, country, currency and departments appear in the ticket, the Part 1 handoff, the Part 3 handoff, the sections and the final document (asserted); the document is exactly what Part 2 drafted and the owners approved.
- Between the end of Part 2 and the opening of the gates the ticket rested in `under_evaluation` without any signal, so a page polling at that instant would stop updating: `approval_state = queued` now covers it, and the page keeps polling while a ticket waits.
- The ticket status was only a column, so a jump could not be proved either way: status changes are now trace events and the timeline is asserted.
- In the trace the ticket became `done` before the final-document event: the order is fixed (the last event of a run is the status change).
- A simulated run that reused a ticket id inherited the paused/arbitration state of an earlier run through a persistent checkpoint: simulations now use their own in-memory checkpointer.
- "Who is waiting" is read from the database, and "can it resume" from the checkpoint: with a lost checkpoint the coordinator could have run before every owner had decided (a test covers it).

## Tests

```powershell
python -m pytest tests/pipelines/test_rfp_approval.py   # interrupt and resume, parallel branches, limits, arbitration, validation, final document
python -m pytest tests/pipelines/test_rfp_e2e.py        # Parts 1 to 3 with simulated approvals: timeline, consistency, trace
python -m pytest services/tests/test_rfp_approval_api.py
node --test uis/backoffice/tests/rfp.test.cjs
python scripts/rfp_e2e.py --trace                       # the same path from the command line
```

## Limits, honestly

- **No live model was used.** Generators and evaluators ran on their rules in every run; the approvals, the arbitration and the final document do not use a model at all.
- **Checkpoints are in SQLite**, a file on the API's volume. It is enough for one API process; several API processes or hosts would need a shared checkpointer (Postgres) and a shared lock instead of the in-process ones.
- **The locks are in-process**: two API processes could resume the same department at the same time. The database state is guarded by the "section must be pending" check, but only one process is supported.
- **The approver is a named owner registered by a staff account**; there is no login per owner. Tying each owner to an account (`RFP_APPROVER_ROLES` is the hook) is the next step if the company wants it.
- **Volume-vs-capacity compares orders per month**, from structured commitments an owner states with a decision. A capacity written only in prose, or in pallets, is not compared (it cannot be converted to orders without a rule the CONTEXT does not give).
- **A blocked ticket has no reopen action yet**: it stays `needs_human_review` with the reason, for a person to resolve outside the flow.
- The `needs_human_review`, form and trace panels of the UI were checked in the browser on the paths described in the pull request; the other paths are covered by the API and pipeline tests.
