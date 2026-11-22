# `data/pipelines` folder

This folder groups **all data pipelines in the monorepo** related to the company: ingestion, ETL/ELT, cleaning, transformation, and loading into analytical or production systems.

Each subfolder or file under `data/pipelines/` should represent **one pipeline or job set** (for example `sales-etl`, `telemetry-stream`, `customer-segmentation`) and include the required configuration (scripts, orchestration, connectors, schemas, etc.).

- **Main purpose**: consolidate in one place the data movement and transformation logic that powers the company’s applications and analytics.
- **Recommendation**: document pipelines as you add them—their goal, data sources and sinks, dependencies, and how to run them in development, testing, and production.

> _Spanish version: [README.es.md](./README.es.md)._

## Pipelines

- **Weekly Warehouse & Client Performance** (business KPIs per warehouse and client, from `telemetry_events` into `reporting.weekly_warehouse_client_performance`): design in [PIPELINE_DESIGN.md](./PIPELINE_DESIGN.md). Implementation comes next.

## `rfp_intake/` (Milestone 9, part 1)

The dedicated LangGraph that turns an uploaded RFP PDF into a classified, department-split analysis: `convert` (MarkItDown) → `classify` → `analyze` (metadata + readability) →
`orchestrate` → parallel `warehouse_worker` / `lastmile_worker` / `reverse_worker` → `synthesize`. The API router (`services/routers/rfp.py`) only triggers `run_intake` and reads
tickets. Design, limits and the handoff contract for Part 2: [docs/rfp/design.md](../../docs/rfp/design.md), [docs/rfp/handoff-contract.md](../../docs/rfp/handoff-contract.md).

`rfp_intake/` also holds **Milestone 9, part 2**: the `rfp_drafting` graph (`drafting_graph.py`) with one generator per department (`generators.py`), three parallel evaluators (`evaluators.py`, rules of CONTEXT section 5 in `rules.py`) and a bounded generator-evaluator loop that ends in `passed` or `needs_human_review`. See [docs/rfp/proposal-design.md](../../docs/rfp/proposal-design.md) and [docs/rfp/proposal-handoff-contract.md](../../docs/rfp/proposal-handoff-contract.md).

`rfp_intake/` closes with **Milestone 9, part 3**: a human-in-the-loop approval per department (`approval_graph.py`: one checkpointed branch per department, `interrupt` at the gate, resume through `approval_runner.Workflow.submit`), an explicit arbitration node with the fixed arbiters of the CONTEXT (`arbitration.py`), the final document (`final_document.py`) and a trace of every node (`trace.py`). See [docs/rfp/approval-design.md](../../docs/rfp/approval-design.md); `scripts/rfp_e2e.py` runs the whole path with simulated approvals.
