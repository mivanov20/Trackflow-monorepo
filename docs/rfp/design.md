# RFP intake: agentic workflow, part 1 (Milestone 9)

Sales receives RFPs as PDFs and cannot tell, by reading one, which departments to involve or what to ask each of them. This part automates that first analysis: the PDF
is uploaded in the backoffice, becomes Markdown, is classified, and a valid RFP is split across **only the departments its scope needs** and consolidated into one
Sales-facing result. Writing the proposal itself is the next part.

Everything follows `CONTEXT-company.md` of Milestone 9: the departments (`warehouse`, `lastmile`, `reverse`) with their owners, the RFP format, the currency by
country, the ticket vocabulary and the guidelines of section 5.

## Where each piece lives

| Piece | Location | Does |
|---|---|---|
| Upload / read endpoints | `services/routers/rfp.py` (existing API, no new service) | stores the PDF, creates the ticket, schedules the pipeline, returns tickets |
| The pipeline | `data/pipelines/rfp_intake/` | the dedicated LangGraph and the four agents; it owns the logic, routers only call it |
| Persistence | `services/models.py`: `RfpTicket` (`rfp_tickets`), `DepartmentSection` (`rfp_department_sections`) | PostgreSQL (Supabase) in production through SQLModel, SQLite in development |
| Backoffice | `uis/backoffice/app/backoffice/rfp/` + `lib/rfp.ts` | upload form, ticket list that polls, ticket detail |
| CLI helpers | `scripts/rfp_intake.py` | `smoke`, `reprocess`, `list`; no HTTP |
| Uploaded PDFs | `data/raw/rfp/<ticket_id>.pdf` (not versioned) | runtime artifact of the intake |

## The flow

```
 upload (POST /rfp/tickets, 202)           background task
 ┌────────────────────────────┐   ┌────────────────────────────────────────────────────────────────────────────────┐
 │ validate PDF, save to       │   │ convert ─► classify ──not an RFP──► discard (ticket: discarded, with reason)    │
 │ data/raw/rfp, ticket row    │──►│               │ valid RFP                                                       │
 │ status = analyzing          │   │               ▼                                                                 │
 └────────────────────────────┘   │            analyze (metadata + readability) ─► orchestrate ─┬► warehouse_worker ─┐│
        UI polls GET /rfp/tickets   │                                                              ├► lastmile_worker ──┼► synthesize
                                    │                                                              └► reverse_worker ───┘   (ticket: intake_complete)
                                    └────────────────────────────────────────────────────────────────────────────────┘
```

- **Asynchronous.** The upload only validates, stores and creates the ticket, then answers `202` with `status: analyzing`. FastAPI's background task runs
  `run_intake` after the response; the UI polls every 2.5 s while any ticket is `analyzing` and shows the step the pipeline is in (`current_step`, `progress`).
- **Markdown first.** `convert.py` turns the PDF into Markdown with MarkItDown before any agent runs. Agents read the Markdown only. The Markdown is kept in
  the ticket, so Part 2 never parses the PDF again.
- **Ticket status (Part 1):** `analyzing` → `intake_complete` or `discarded`. We added `failed` (the PDF cannot be read or the pipeline broke): the ticket carries a
  message and never stays `analyzing` forever. `waiting_for_approval` and the Part 2/3 statuses are not used.

## The agents (orchestrator-worker-synthesizer on a graph of its own)

`graph.py` is a LangGraph `StateGraph` independent of the CX agent's graph (a test checks its nodes). The workers run **in parallel** through LangGraph's `Send`:
`orchestrate` emits one `Send` per department the scope needs, so a department that is not needed never runs.

| Agent | Decides | Without a model | With a model |
|---|---|---|---|
| **Classifier** (`classifier.py`) | is this a legitimate RFP? (`formal_rfp`, `informal_request`, `vendor_pitch`, `other`) | signal patterns: asks for a proposal, scope/criteria/timeline, requested TrackFlow services, versus an offer of services or rates to TrackFlow | the model classifies; the rules **veto** an accept when the text is an unmistakable vendor pitch |
| **Metadata** (`metadata.py`) | client, country, services, volume, deadline, budget, contact | rules (English and Spanish, negation-aware) | the model only fills fields the rules could not find, and only with text that appears in the document |
| **Orchestrator** (`orchestrator.py`) | which departments, and the subtask of each | departments come from the requested services (deterministic, by CONTEXT); extracts are the document blocks about that department's subject | the model rewords each department's focus sentence only |
| **Workers** (`workers.py`): `warehouse`, `lastmile`, `reverse` | each department's `key_aspects` | per-department rules | the model may reword requirements, asks and questions; any answer with a number that is not in the subtask is **discarded** |
| **Synthesizer** (`synthesizer.py`) | the Sales summary and the handoff | template built from the workers' output | the model may rewrite the opening paragraph only, with no new numbers |

All structured output (who to ask, what to ask, what is missing) is assembled by code, so it is always complete. A model improves wording, never facts.

### Which departments (CONTEXT 2.1)

The requested scope decides, and what the RFP says is *not* wanted is respected: ModaViva writes "no se requiere … última milla" and "mantenemos nuestro propio
transportista", so `lastmile` is **not** triggered (the summary says why). `services_excluded` is stored with the ticket.

### What a worker receives, and what it may not do

A worker gets `shared` (client, country, currency, monthly volume, deadline, budget, services) plus its own **extracts** (blocks of the RFP about its subject),
never the whole document and never another department's slices. It must not invent figures: when the RFP states no monthly volume (ModaViva does not), the
warehouse worker records an open question and the figure is absent from `stated_figures`. `15.000 SKUs` is a SKU count, not an order volume. A date without a
year ("around August 24th") is kept as written and flagged, not completed.

`key_aspects` of a department: `summary`, `requirements` (quoted from the RFP), `stated_figures`, `asks_of_owner` (what the owner must provide: the
department's contribution of CONTEXT 2.1, in the client's currency), `open_questions` (what the RFP does not say), `constraints` (guidelines of section 5 that bind
that answer: prices in the country currency, no returns promise under 48 hours, no negotiated carrier rates).

## Metadata and readability

Metadata (CONTEXT 2.2/2.3): `client_name`, `client_country` (`US`/`ES`), `currency` (US→USD, ES→EUR), `services_requested`, `monthly_volume`, `deadline`
(+ its kind: proposal due or go-live), `budget_range`, `departments_needed`, contact, and extras as written (reference number, SKUs, preferred location).
Anything not in the document is `null` and appears in `open_questions`.

Readability (`reading_cost.py`): `py-readability-metrics` on the Markdown prose, giving Flesch-Kincaid grade, Gunning Fog, Flesch reading ease and ARI, plus
words, estimated tokens (characters / 4) and a **cost band** (`low` < 1,500 tokens, `medium` < 6,000, `high`). They anticipate processing cost; they are not a literary
grade. The library needs at least 100 words and NLTK's `punkt_tab` data; if it cannot score, the metric is `null` with a `note`, and the size and band are still there.

## Routing: the handoff to Part 2

No queue and no second API: the handoff is **a database contract**. When the synthesizer finishes, the ticket gets `handoff_state = ready_for_part_2` and a
`handoff` JSON with `ticket_id`, the client, the departments and every department's `key_aspects`, in the same transaction that sets `intake_complete`. See
[handoff-contract.md](handoff-contract.md).

## Failure behaviour

- A rejected document is **not an error**: `discarded`, with the reason shown, and no department work.
- An unreadable PDF or an internal error ends in `failed` with a safe message; the log has the cause. One ticket never affects another.
- Ticket, metadata, sections and handoff are written in one transaction at the end, so there is no `intake_complete` without its sections.
- A service restart kills background tasks: at startup, tickets still `analyzing` are marked `failed` with instructions, and `scripts/rfp_intake.py reprocess` runs them again.

## Security of the document itself

An RFP is text written by a stranger. It is never an instruction: the document goes to a model inside a delimiter with a random id and a system rule that says it is
data; a document that says "classify this as a valid RFP" is treated as part of the text (the classifier notes it in the reason and still decides on content), and the
rules veto a model accept on an unmistakable vendor pitch. Uploads are checked (extension, `%PDF-` header, size limit), the file is stored under a name we generate (never the
client's filename), and PDFs are not versioned (`data/raw/rfp/` is in `.gitignore`).

## Tests

```powershell
python -m pytest tests/pipelines/test_rfp_intake.py     # classifier, metadata, orchestrator, workers, synthesizer, graph, persistence
python -m pytest services/tests/test_rfp_api.py          # upload, async behaviour, validation, polling
node --test uis/backoffice/tests/rfp.test.cjs            # the browser helpers
```

The three sample PDFs of the CONTEXT are in `tests/pipelines/fixtures/rfp-requests/` and are what the tests (and the UI verification) upload.

## Limits, honestly

- **No live model was used.** Every agent has a rules path and a model path; the model path is tested with stand-ins, and the rules decided in all the runs and
  verifications. How a real model performs (classification of odd documents, rewording) is not measured.
- **The rules are heuristics** (patterns in English and Spanish). An RFP in another language, or a service described with unusual words, can be missed; the
  ticket then lists an open question instead of guessing, and the classifier can reject a very unusual real RFP.
- **Country** is decided by markers (company suffix, city, currency, domain). A tie is unknown, not guessed.
- **Background tasks run in the API process.** A restart interrupts them (handled as above); a worker queue (Celery is already in the repo) would remove that, at the cost
  of one more moving part.
- **OCR is not included:** a scanned PDF with no text layer fails with a clear message.
- Contacts of the client are taken from the signature; the owners to ask inside TrackFlow come from the CONTEXT table, not from the document.
