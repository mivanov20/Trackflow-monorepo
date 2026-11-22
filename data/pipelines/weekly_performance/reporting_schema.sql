-- Destination of the Weekly Warehouse & Client Performance pipeline (PostgreSQL / Supabase).
-- The table is the one of the company CONTEXT, unchanged; the checks and the run log are added by the design
-- (data/pipelines/PIPELINE_DESIGN.md, sections 2.7 and 3.3). Idempotent: safe to run again.

create schema if not exists reporting;

create table if not exists reporting.weekly_warehouse_client_performance (
  id uuid primary key default gen_random_uuid(),
  warehouse text not null,
  client_id text not null,
  week_start date not null,
  inbound_units_count integer not null default 0,
  outbound_orders_count integer not null default 0,
  stockout_events_count integer not null default 0,
  discrepancy_events_count integer not null default 0,
  discrepancy_rate numeric not null default 0,
  computed_at timestamptz not null default now(),
  unique (warehouse, client_id, week_start),
  check (warehouse in ('los_angeles', 'zaragoza')),
  check (week_start = date_trunc('week', week_start))
);

-- Closed to Supabase's public API; the pipeline and the FastAPI service connect with roles that bypass it.
alter table reporting.weekly_warehouse_client_performance enable row level security;

create table if not exists reporting.pipeline_runs (
  run_id uuid primary key default gen_random_uuid(),
  pipeline_name text not null,
  trigger text not null check (trigger in ('schedule', 'manual', 'backfill')),
  triggered_by uuid,
  status text not null check (status in ('running', 'completed', 'failed')),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  window_start date not null,
  window_end date not null,
  source_max_timestamp timestamptz,
  records_extracted integer,
  duplicates_dropped integer,
  records_discarded integer,
  rows_inserted integer,
  rows_updated integer,
  rows_unchanged integer,
  rows_deleted integer,
  optional_steps_failed integer not null default 0,
  error_stage text check (error_stage in ('extract', 'transform', 'load', 'abandoned')),
  error_type text,
  error_message text,
  prefect_flow_run_id text
);

-- At most one run in progress per pipeline: a second start is refused by the database itself.
create unique index if not exists one_running_run on reporting.pipeline_runs (pipeline_name) where status = 'running';
create index if not exists ix_pipeline_runs_started_at on reporting.pipeline_runs (started_at desc);

alter table reporting.pipeline_runs enable row level security;
