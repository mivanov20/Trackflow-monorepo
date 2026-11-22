# RFP proposal drafting: generation and evaluation, part 2 (Milestone 9)

> Part 3 (human approval per department, arbitration, final document) continues from the handoff described here: [approval-design.md](approval-design.md).

Part 1 ends with a ticket in `intake_complete` and a handoff that says what each active department must answer. Part 2 turns that into **a first draft of each department's section
of the pricing proposal, reviewed automatically before a person sees it**. It uses the guidelines of `CONTEXT-company.md` section 5 as tests, not as opinions.

The course's reference diagram (assignment orchestrator maps workstreams to departments, a synthesizer produces one assignment ticket per department with an approval status) is
followed with TrackFlow's departments, which the CONTEXT fixes (`warehouse`, `lastmile`, `reverse`) instead of the generic Sales / Legal / Operations of the picture: `assign` is the assignment
orchestrator, each `*_section` is a department-scoped worker, and `finalize` is the synthesizer that emits one item per department (`section_id`, content, evaluation, `approval_status: pending`) for Part 3.

## Where each piece lives

| Piece | Location |
|---|---|
| Graph and loop | `data/pipelines/rfp_intake/drafting_graph.py` (dedicated `rfp_drafting` graph; not the CX agent's, not the intake's) |
| Generators (one per department) | `generators.py`: `draft_warehouse`, `draft_lastmile`, `draft_reverse` |
| Evaluators | `evaluators.py` (readability, relevance, compliance, consolidation into the `EvaluationResult`) and `rules.py` (the five guidelines) |
| Knowledge base access | `knowledge.py` (optional improvement: real policies and figures) |
| Persistence | `proposal_store.py` + new columns on `rfp_tickets` / `rfp_department_sections` (`services/models.py`) |
| Runner | `proposal_runner.py`: `run_proposal(ticket_id, engine)` |
| HTTP | `services/routers/rfp.py` (same API): the drafting follows the intake in the same background task; `POST /rfp/tickets/{id}/proposal` starts it by hand |

## The flow

```
 Part 1 handoff (rfp_tickets.handoff, ready_for_part_2)
      │
   assign ── one brief per department (client data + THAT department's key_aspects)
      │
      ├──► warehouse_section ─┐   each section = its own generator-evaluator loop, in parallel with the others
      ├──► lastmile_section  ─┼──► finalize ──► part3_handoff (rfp_tickets.part3_handoff)
      └──► reverse_section   ─┘

   section loop:   generate ──► evaluate_readability ─┐
                      ▲         evaluate_relevance   ─┼─► decide ── pass ──────────────► passed
                      │         evaluate_compliance  ─┘      │
                      └──── feedback_for_generator ◄─────────┤ failed, attempts left
                                                              └ failed, limit reached ─► needs_human_review (last draft + its evaluation kept)
```

- **Input is the handoff, never the PDF.** `build_briefs` reads `ticket_id`, the client, the volume and the department's `key_aspects` from `rfp_tickets.handoff`. A test deletes the PDF and the
  stored Markdown and the drafting still runs; another makes `pdf_to_markdown` raise if it is called.
- **One generator per department**, with its own content: warehousing (capacity, onboarding, storage pricing), last mile (carriers by destination, delivery times, cost per shipment) and returns
  (process, turnaround, cost per unit). Each section also carries a volume discount tier table and the on-time SLA.
- **Evaluators run in parallel** (LangGraph `Send`, one node each) and `decide` waits for the three. Departments are parallel branches: a slow or retrying department does not block the others (tested).
- **The loop is bounded.** `RFP_MAX_DRAFT_ITERATIONS` (default 3) is the number of attempts per section, the first draft included. If the last attempt still fails, the section ends
  `needs_human_review` with **that last draft and its EvaluationResult**; it is never dropped and the ticket is never discarded.

## What the generators may write (figures)

A price or figure is written only if it comes from the **knowledge base** (`docs/company-knowledge-base/`, through the semantic RAG when it is up and a lexical search over the same
documents otherwise) or from the RFP itself. From the knowledge base: the contractual on-time SLA (90% per month, by country), the storage rate (16 EUR per cubic meter per month in Zaragoza,
18 USD in Los Angeles), the 30-day grace period, the long-term inventory fee, the 30-day return window, the carriers of each country and the delivery times. What no source has
(cost per pallet, cost per shipment, returns cost, capacity, onboarding time, and **the discount of each tier**) is written as "To be confirmed by <owner>" and listed among the open items:
the knowledge base says only Miguel Torres can offer a preferential rate, so the draft does not invent discounts. If the knowledge base gives no SLA, the draft does not state a
percentage, GL-2 fails, and after the attempts the section goes to a person, which is the right outcome.

With a model configured, the generator asks it for the section (same inputs, the knowledge base text and the feedback of the previous attempt) and falls back to its rules if the answer is not
usable. Evaluators are deterministic either way, so a model draft is judged by the same rules.

## The evaluators

| Evaluator | Passes when |
|---|---|
| **readability** (`py-readability-metrics` on the prose: tables and Markdown marks removed) | Flesch-Kincaid grade ≤ 14, at least 60 words, no sentence over 40 words |
| **relevance** | the draft covers what the department contributes (CONTEXT 2.1: capacity / cost per pallet or SKU / onboarding; cost per shipment / carriers / SLA; returns cost / turnaround), the client's own figures and requests from the intake (SKUs, volume, ERP integration, carrier selection, requested returns steps), flags the open questions, **and contains no figure that is in neither the RFP nor the knowledge base** |
| **compliance** | none of the five rules is violated (below) |

| Rule | CONTEXT section 5 | How it is tested |
|---|---|---|
| GL-1 currency | USD for US operations, EUR for Spain, by `client_country` | the other currency's code, symbol or word is absent and the right one is present; an unknown country fails (the currency cannot be decided) |
| GL-2 sla | state the on-time delivery SLA (%) | a percentage near "on-time"/"SLA" exists and equals the contractual one of the knowledge base |
| GL-3 returns-48h | never promise returns processing in under 48 hours | sentences about returns with hours < 48, one day or less, same/next day, "under 48" (not negated) |
| GL-4 discount-tiers | a volume-based discount tier table | a Markdown table with a volume column, a discount column and at least two tiers |
| GL-5 carrier-rates | never disclose negotiated rates with specific carriers | a sentence or table row naming a carrier together with a money amount, "negotiated", "contract rate" or a per-parcel price |

`GL-2` and `GL-4` apply to every section. A section for a client that keeps its own carrier (ModaViva) states the SLA and says that it does not cover the client's own last mile.

### EvaluationResult (stored in `rfp_department_sections.evaluation_results`)

```json
{
  "section_id": "<ticket_id>:warehouse", "department_id": "warehouse", "iteration": 2,
  "readability": {"pass": true, "score": 6.9, "details": {"flesch_kincaid_grade": 6.9, "gunning_fog": 9.1, "word_count": 310, "problems": []}},
  "relevance":   {"pass": true, "missing_aspects": [], "unsupported_figures": []},
  "compliance":  {"pass": false, "rule_ids": ["GL-1"], "violations": ["GL-1 (currency): The text quotes prices in USD, but the client's country requires EUR."], "checked_rule_ids": ["GL-1", "GL-2", "GL-3", "GL-4", "GL-5"]},
  "overall_pass": false,
  "feedback_for_generator": "Fix these before the next draft: (1) GL-1 Quote prices in USD for US operations and EUR for Spain operations (client_country decides). Problem: ...",
  "fixes": [{"kind": "compliance", "id": "GL-1", "instruction": "..."}],
  "history": [{"iteration": 1, "overall_pass": false, "feedback_for_generator": "..."}]
}
```

`feedback_for_generator` is what goes back to the generator; `fixes` is the same, structured. A `readability` fix makes the rules generator drop its optional paragraphs on the next attempt.

## Ticket status in real time

The status is **derived from the sections after every event**, so it is what is really happening:

| Status | When |
|---|---|
| `intake_complete` | Part 1 result; Part 2 does not rewrite it, it only leaves it when the drafting starts |
| `drafting` | some section has not produced its first draft yet |
| `under_evaluation` | every section has a draft: evaluators and the loop are running (also while a section is rewritten after feedback) |
| `needs_human_review` | every section has finished and at least one exhausted its attempts |

Part 3 statuses (`waiting_for_approval`, `done`) are not used. When every section passes, the ticket rests in `under_evaluation` with `proposal_state = ready_for_part_3` (the backoffice
shows it as "Ready for approval", a label and not a status). `proposal_state` (`not_started`, `queued`, `in_progress`, `ready_for_part_3`, `failed`, `interrupted`) says where the drafting is. Each draft and
each evaluation is written as soon as it exists (`draft_status`, `iterations`, `draft_content`, `evaluation_results` per section, and a step in the ticket's `progress`), and the backoffice polls.

## Failure behaviour

- A crash in the drafting puts the ticket back to `intake_complete` with `proposal_state = failed` and a safe message; the intake result is intact and `POST /rfp/tickets/{id}/proposal` (or
  the button in the backoffice) starts it again.
- A service restart interrupts background tasks: at startup, tickets with a drafting in progress go back to `intake_complete` with `proposal_state = interrupted`.
- The Part 2 columns are added to existing Part 1 tables at startup (`proposal_store.ensure_columns`, idempotent).

## Tests

```powershell
python -m pytest tests/pipelines/test_rfp_drafting.py          # generators, evaluators, the five rules, the loop and its bound, persistence
python -m pytest services/tests/test_rfp_proposal_api.py       # the workflow through the API
node --test uis/backoffice/tests/rfp.test.cjs                  # the page's helpers
```

Includes: a generator per department; an evaluator failing (readability, relevance with an invented figure, compliance); **a compliance failure tied to CONTEXT section 5** (returns promised
in 24 hours: `compliance.pass == false`, `rule_ids == ["GL-3"]`); a retry with the feedback that then passes; the bound and `needs_human_review`; evaluators and departments running in parallel.

## Limits, honestly

- **No live model was used.** The drafts of every run and verification came from the rules; the model path is tested with stand-ins only. Real-model draft quality is not measured.
- **The drafts are first drafts, not offers**: the prices Warehouse, Last Mile and Reverse Logistics own are "to be confirmed" because no source has them. That is deliberate, and it is what the open items list.
- **Readability and relevance are heuristics** (a grade limit, patterns for aspects). Compliance is exact only in the sense of the patterns of `rules.py`: a promise phrased in a way they do not know can pass.
- The tier boundaries are derived from the client's stated volume (once, twice) so they are not invented figures; without a volume the tiers are named, not sized.
- Part 3 arbitration (conflict triggers) is not in this part; the evaluators already detect a currency mismatch and an under-48-hour promise, which are two of its triggers.
