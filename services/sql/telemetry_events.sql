-- Telemetry storage (Phase 3). Run once in the Supabase SQL editor, or let the API create it on startup
-- (services/models.py defines the same table, indexes, trigger and row level security).
-- Idempotent: safe to run again.

CREATE TABLE IF NOT EXISTS telemetry_events (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp   timestamptz NOT NULL,
    service     text NOT NULL,
    event_type  text NOT NULL,
    level       text NOT NULL DEFAULT 'info',
    value       numeric,
    message     text,
    tags        jsonb NOT NULL DEFAULT '{}',
    CONSTRAINT ck_telemetry_events_level CHECK (level IN ('info', 'warn', 'error'))
);

-- The three indexes that keep analytical queries fast as the table grows.
CREATE INDEX IF NOT EXISTS ix_telemetry_events_timestamp  ON telemetry_events (timestamp);
CREATE INDEX IF NOT EXISTS ix_telemetry_events_event_type ON telemetry_events (event_type);
CREATE INDEX IF NOT EXISTS ix_telemetry_events_tags       ON telemetry_events USING gin (tags);

-- Events are immutable facts: the database itself refuses UPDATE and DELETE.
CREATE OR REPLACE FUNCTION telemetry_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'telemetry_events is append-only: % is not allowed', TG_OP;
END;
$$;

DROP TRIGGER IF EXISTS telemetry_events_no_update_delete ON telemetry_events;
CREATE TRIGGER telemetry_events_no_update_delete
    BEFORE UPDATE OR DELETE ON telemetry_events
    FOR EACH ROW EXECUTE FUNCTION telemetry_events_immutable();

-- Closed to Supabase's public (anon/authenticated) API. The FastAPI service connects with a role that
-- bypasses row level security, so it can still insert.
ALTER TABLE telemetry_events ENABLE ROW LEVEL SECURITY;
