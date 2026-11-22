-- Dead letter queue of the Celery workers: a task that failed on every attempt, recorded once with why and when.
-- Run once in the Supabase SQL editor, or let the worker create it (services/models.py defines the same table).
-- Idempotent: safe to run again.

create table if not exists dead_letter_tasks (
  id            uuid primary key default gen_random_uuid(),
  task_id       text not null,
  task_name     text not null,
  attempt       integer not null,
  error_message text not null,
  payload       jsonb not null default '{}',
  failed_at     timestamptz not null default now()
);

create index if not exists ix_dead_letter_tasks_task_id on dead_letter_tasks (task_id);
create index if not exists ix_dead_letter_tasks_failed_at on dead_letter_tasks (failed_at);

-- Closed to Supabase's public API; the worker connects with a role that bypasses row level security.
alter table dead_letter_tasks enable row level security;
