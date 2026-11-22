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
