-- Trace of the support agent (LangGraph): one row per node that ran, in order, with its input and output summaries.
-- Run once in the Supabase SQL editor, or let the API create it (services/models.py defines the same table).
-- Idempotent: safe to run again.

create table if not exists agent_trace_steps (
  id          uuid primary key default gen_random_uuid(),
  run_id      text not null,
  step        integer not null,
  node        text not null,
  status      text not null,            -- ok | error
  started_at  timestamptz not null,
  duration_ms integer not null default 0,
  input       jsonb not null default '{}',
  output      jsonb not null default '{}',
  error       text
);

create index if not exists ix_agent_trace_steps_run_id on agent_trace_steps (run_id, step);
create index if not exists ix_agent_trace_steps_started_at on agent_trace_steps (started_at);

-- Closed to Supabase's public API; the API connects with a role that bypasses row level security.
alter table agent_trace_steps enable row level security;
