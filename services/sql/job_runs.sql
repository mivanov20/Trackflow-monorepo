-- Execution log of background jobs (the nightly telemetry script). Run once in the Supabase SQL editor, or let the API
-- or the script create it (services/models.py defines the same table). Idempotent: safe to run again.
--
-- This is the orchestration layer of the nightly script. It is NOT reporting.pipeline_runs, which records the ETL phases
-- of the data pipeline (Milestone 6): the script writes here, the pipeline subprocess writes there.

create table if not exists job_runs (
  id            uuid primary key default gen_random_uuid(),
  job_name      text not null,
  target_date   date not null,
  status        text not null default 'pending',
  started_at    timestamptz,
  finished_at   timestamptz,
  error_message text,
  created_at    timestamptz not null default now(),
  constraint ck_job_runs_status check (status in ('pending', 'processing', 'completed', 'failed'))
);

-- Idempotency: "did job X complete for day D?" reads this pair.
create index if not exists ix_job_runs_job_name_target_date on job_runs (job_name, target_date);

-- The lock IS the `processing` status. This partial unique index only makes taking it atomic: the database refuses a
-- second `processing` row for the same job, so two instances started together cannot both run.
create unique index if not exists one_processing_run_per_job on job_runs (job_name) where status = 'processing';

-- Closed to Supabase's public API; the script connects with a role that bypasses row level security.
alter table job_runs enable row level security;
