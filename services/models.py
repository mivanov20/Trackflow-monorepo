import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import DDL, Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, Numeric, Text, Uuid, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel
from trackflow_shared.incidents import BRANCHES, CATEGORIES, ORIGINS, STATUSES


def utc_now() -> datetime:
    """Devuelve la hora UTC actual para registrar movimientos."""
    return datetime.now(timezone.utc)


class SKU(SQLModel, table=True):
    __tablename__ = "sku"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    sku: str = Field(index=True)
    client_name: str = Field(index=True)
    category: str = Field(index=True)
    warehouse: str = Field(index=True)


class StockEntry(SQLModel, table=True):
    __tablename__ = "stockentry"

    id: int | None = Field(default=None, primary_key=True)
    sku_id: int = Field(foreign_key="sku.id", index=True)
    quantity: int
    reference: str
    warehouse: str = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    user_uid: str = Field(index=True)


class StockExit(SQLModel, table=True):
    __tablename__ = "stockexit"

    id: int | None = Field(default=None, primary_key=True)
    sku_id: int = Field(foreign_key="sku.id", index=True)
    quantity: int
    exit_type: str
    tracking_number: str | None = None
    warehouse: str = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    user_uid: str = Field(index=True)

def _allowed_values(column: str, values) -> CheckConstraint:
    """Build a CHECK constraint restricting `column` to `values`."""
    quoted = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=f"ck_incident_{column}")


class Incident(SQLModel, table=True):
    """Operational incident; status, origin, category and branch are constrained in the database."""

    __tablename__ = "incident"
    __table_args__ = (
        _allowed_values("status", STATUSES),
        _allowed_values("origin", ORIGINS),
        _allowed_values("category", CATEGORIES),
        _allowed_values("branch", tuple(BRANCHES)),
    )

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=120)
    description: str
    category: str = Field(index=True)
    status: str = Field(index=True)
    origin: str = Field(index=True)
    branch: str = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)


class IncidentSeedLedger(SQLModel, table=True):
    """CSV ids already imported by `scripts/seed_incidents.py`.

    The analyzer's `incident_id` is not part of the incident model, so this ledger is
    what makes the seed script idempotent.
    """

    __tablename__ = "incident_seed_ledger"

    source_id: str = Field(primary_key=True)


TELEMETRY_LEVELS = ("info", "warn", "error")

# gen_random_uuid() exists in PostgreSQL (Supabase) but not in SQLite, which the tests and local development may use.
_POSTGRES = (os.getenv("DATABASE_URL") or "").startswith("postgres")


class TelemetryEventRecord(SQLModel, table=True):
    """One telemetry event. Rows are immutable facts: the application only ever inserts them.

    There is deliberately no update or delete anywhere in the code, and on PostgreSQL a trigger refuses both.
    The mapping from a `TelemetryEvent` to these columns is documented in docs/telemetry/telemetry-plan.md.
    """

    __tablename__ = "telemetry_events"
    __table_args__ = (
        CheckConstraint("level IN ('info', 'warn', 'error')", name="ck_telemetry_events_level"),
        Index("ix_telemetry_events_timestamp", "timestamp"),
        Index("ix_telemetry_events_event_type", "event_type"),
        Index("ix_telemetry_events_tags", "tags", postgresql_using="gin"),
    )

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()") if _POSTGRES else None),
    )
    timestamp: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    service: str = Field(sa_column=Column(Text, nullable=False))
    event_type: str = Field(sa_column=Column(Text, nullable=False))
    level: str = Field(default="info", sa_column=Column(Text, nullable=False, server_default=text("'info'")))
    value: Decimal | None = Field(default=None, sa_column=Column(Numeric, nullable=True))
    message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    tags: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False, server_default=text("'{}'")),
    )


# PostgreSQL only: refuse UPDATE and DELETE at the database, and keep the table closed to Supabase's public API.
# The API connects with a role that bypasses row level security, so it can still insert.
_TELEMETRY_TABLE = TelemetryEventRecord.__table__
for _statement in (
    """CREATE OR REPLACE FUNCTION telemetry_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'telemetry_events is append-only: % is not allowed', TG_OP;
END;
$$""",
    "CREATE TRIGGER telemetry_events_no_update_delete BEFORE UPDATE OR DELETE ON telemetry_events FOR EACH ROW EXECUTE FUNCTION telemetry_events_immutable()",
    "ALTER TABLE telemetry_events ENABLE ROW LEVEL SECURITY",
):
    event.listen(_TELEMETRY_TABLE, "after_create", DDL(_statement.replace("%", "%%")).execute_if(dialect="postgresql"))


JOB_STATUSES = ("pending", "processing", "completed", "failed")


class JobRun(SQLModel, table=True):
    """One execution of a background job (the nightly telemetry script), with its state machine.

    pending -> processing -> completed | failed. The `processing` status is the lock: the partial unique index below makes
    the check-and-set atomic, so two instances can never both be processing the same job. This table is the nightly
    orchestration layer; `reporting.pipeline_runs` (the ETL layer, Milestone 6) is a different table with a different job.
    """

    __tablename__ = "job_runs"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'processing', 'completed', 'failed')", name="ck_job_runs_status"),
        # Idempotency checks ("did this job complete for this day?") read this pair.
        Index("ix_job_runs_job_name_target_date", "job_name", "target_date"),
        # At most one `processing` row per job: the status itself is the lock, and the database makes taking it atomic.
        Index("one_processing_run_per_job", "job_name", unique=True, postgresql_where=text("status = 'processing'"), sqlite_where=text("status = 'processing'")),
    )

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()") if _POSTGRES else None),
    )
    job_name: str = Field(sa_column=Column(Text, nullable=False))
    target_date: date = Field(sa_column=Column(Date, nullable=False))
    status: str = Field(default="pending", sa_column=Column(Text, nullable=False, server_default=text("'pending'")))
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class DeadLetterTask(SQLModel, table=True):
    """The dead letter queue: a Celery task that failed on every attempt, recorded once with why and when.

    The message that failed carries identifiers only (never a data payload), so `payload` holds those identifiers.
    Rows are written by the worker, never updated or deleted by the application.
    """

    __tablename__ = "dead_letter_tasks"
    __table_args__ = (Index("ix_dead_letter_tasks_task_id", "task_id"), Index("ix_dead_letter_tasks_failed_at", "failed_at"))

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()") if _POSTGRES else None),
    )
    task_id: str = Field(sa_column=Column(Text, nullable=False))
    task_name: str = Field(sa_column=Column(Text, nullable=False))
    attempt: int = Field(sa_column=Column(Integer, nullable=False))
    error_message: str = Field(sa_column=Column(Text, nullable=False))
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False, server_default=text("'{}'")))
    failed_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class AgentTraceStep(SQLModel, table=True):
    """The trace of the support agent: one row per node that ran, in order, with what went in and what came out.

    Written while the run goes on (a node that fails leaves its row with the error), so a run that died half way can still be read.
    Inputs and outputs are summaries: the retrieved chunks appear as source / section / chunk index / score, never as their full text.
    """

    __tablename__ = "agent_trace_steps"
    __table_args__ = (Index("ix_agent_trace_steps_run_id", "run_id", "step"), Index("ix_agent_trace_steps_started_at", "started_at"))

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()") if _POSTGRES else None),
    )
    run_id: str = Field(sa_column=Column(Text, nullable=False))
    step: int = Field(sa_column=Column(Integer, nullable=False))
    node: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(sa_column=Column(Text, nullable=False))  # ok | error
    started_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    duration_ms: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    input: dict = Field(default_factory=dict, sa_column=Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False, server_default=text("'{}'")))
    output: dict = Field(default_factory=dict, sa_column=Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False, server_default=text("'{}'")))
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class AgentMemoryEntry(SQLModel, table=True):
    """What the support agent remembers: facts a user approved (carrier rules, recurring incident context, client report preferences).

    Not a RAG collection and not part of the company knowledge base: this is the agent's own store, written only after an explicit, logged
    user decision. Entries are consolidated by scope (carrier + country, or client) and expire; a superseded, expired or forgotten entry stays
    for a while for traceability and is then purged. The record of who approved what lives in `agent_memory_proposals`, which is never purged.
    """

    __tablename__ = "agent_memory_entries"
    __table_args__ = (Index("ix_agent_memory_entries_scope_key", "scope_key"), Index("ix_agent_memory_entries_status", "status"))

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()") if _POSTGRES else None),
    )
    kind: str = Field(sa_column=Column(Text, nullable=False))  # carrier_rule | incident_context | client_preference
    scope_key: str = Field(sa_column=Column(Text, nullable=False))  # carrier:seur|country:ES, client:cosmetics-co, incident:ES:zaragoza-catalonia
    carrier: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    country: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # US | ES
    client: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    topic: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    content: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(default="active", sa_column=Column(Text, nullable=False))  # active | superseded | expired | forgotten
    confirmations: int = Field(default=1, sa_column=Column(Integer, nullable=False))
    uses: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    last_used_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    source_proposal_ids: list = Field(default_factory=list, sa_column=Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False, server_default=text("'[]'")))


class AgentMemoryProposal(SQLModel, table=True):
    """The audit log of the agent's memory: every proposal, its outcome, who decided and when, whether it was approved or not.

    Rows are never deleted by the cleanup. At most ONE proposal per conversation is `pending` (a partial unique index enforces it). Text that
    could be sensitive (addresses, tracking numbers, contact data) is redacted before it is stored here.
    """

    __tablename__ = "agent_memory_proposals"
    __table_args__ = (
        Index("ix_agent_memory_proposals_conversation_id", "conversation_id"),
        Index("one_pending_proposal_per_conversation", "conversation_id", unique=True, postgresql_where=text("status = 'pending'"), sqlite_where=text("status = 'pending'")),
    )

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()") if _POSTGRES else None),
    )
    conversation_id: str = Field(sa_column=Column(Text, nullable=False))
    actor: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # the signed-in user the proposal was shown to
    origin_message: str = Field(default="", sa_column=Column(Text, nullable=False))  # the user message that led to it (redacted)
    proposal: dict = Field(default_factory=dict, sa_column=Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False, server_default=text("'{}'")))
    status: str = Field(sa_column=Column(Text, nullable=False))  # pending | approved | edited | rejected | discarded_topic_change | discarded_unclear | rejected_by_policy | blocked_by_policy
    proposed_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    resolved_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    decision_label: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # approve | reject | edit | unrelated | unclear
    decision_confidence: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    decision_method: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # rules | model | none
    decision_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # the user message that decided it (redacted)
    decided_by: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # the user who authorised (or refused) it
    final_content: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # what was written, if anything
    entry_id: uuid.UUID | None = Field(default=None, sa_column=Column(Uuid, nullable=True))
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class CustomerShipment(SQLModel, table=True):
    """Who an order belongs to: the registry the agent's session authorization consults before it says anything about a shipment.

    The agent may only talk about an order that is linked to the customer authenticated in the session. This table is that link (and the order's real
    country, which decides whether the US or the Spain policy applies). It is filled by the order / tracking integration, never by the agent.
    """

    __tablename__ = "customer_shipments"
    __table_args__ = (Index("ix_customer_shipments_customer_id", "customer_id"),)

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()") if _POSTGRES else None),
    )
    tracking_number: str = Field(sa_column=Column(Text, nullable=False, unique=True))  # normalised: upper case, no # or dashes
    order_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True, unique=True))
    customer_id: str = Field(sa_column=Column(Text, nullable=False))  # the authenticated account that owns the order
    country: str = Field(sa_column=Column(Text, nullable=False))  # US | ES: where the order was shipped; decides which return policy applies
    status: str = Field(default="in_transit", sa_column=Column(Text, nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class GuardrailEvent(SQLModel, table=True):
    """Every time a guardrail blocks, redirects, redacts or isolates something: which guard, what kind of failure, what it did, and why.

    `failure_type` is one of structural (a malformed format), content (out of scope, sensitive data) or security (injection, extraction, unauthorised access).
    The excerpt is redacted and short: the log must not become a place where sensitive text ends up.
    """

    __tablename__ = "guardrail_events"
    __table_args__ = (Index("ix_guardrail_events_created_at", "created_at"), Index("ix_guardrail_events_guard", "guard", "failure_type"))

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()") if _POSTGRES else None),
    )
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    guard: str = Field(sa_column=Column(Text, nullable=False))  # instruction_change | prompt_extraction | personal_task | out_of_domain | session_authorization | policy_mix | output_leak | ...
    failure_type: str = Field(sa_column=Column(Text, nullable=False))  # structural | content | security
    action: str = Field(sa_column=Column(Text, nullable=False))  # blocked | redirected | redacted | isolated | replaced
    reason: str = Field(default="", sa_column=Column(Text, nullable=False))
    conversation_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    actor: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    run_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    excerpt: str = Field(default="", sa_column=Column(Text, nullable=False))


def _json_column(default: str) -> Column:
    return Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False, server_default=text(f"'{default}'"))


class RfpTicket(SQLModel, table=True):
    """One uploaded RFP and its lifecycle (Milestone 9). The same row continues through Parts 2 and 3.

    Part 1 uses `analyzing` -> `intake_complete` | `discarded` (and `failed` when the pipeline itself broke, so a ticket never stays `analyzing` forever).
    The columns after `handoff` belong to the classifier, the metadata extraction and the synthesizer; the pipeline in `data/pipelines/rfp_intake` writes them.
    """

    __tablename__ = "rfp_tickets"
    __table_args__ = (Index("ix_rfp_tickets_status", "status"), Index("ix_rfp_tickets_created_at", "created_at"))

    ticket_id: str = Field(sa_column=Column(Text, primary_key=True))
    rfp_id: str = Field(sa_column=Column(Text, nullable=False, unique=True))  # TF-RFP-<year>-<n>: what Sales quotes to the client
    status: str = Field(default="analyzing", sa_column=Column(Text, nullable=False))
    raw_pdf_path: str = Field(sa_column=Column(Text, nullable=False))  # relative to the repository root, under data/raw/
    original_filename: str = Field(default="", sa_column=Column(Text, nullable=False))
    uploaded_by: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    current_step: str = Field(default="queued", sa_column=Column(Text, nullable=False))
    progress: list = Field(default_factory=list, sa_column=_json_column("[]"))  # [{step, at, detail}]: what the UI shows while it polls

    # converted document (the agents read this, never the PDF)
    markdown: str | None = Field(default=None, sa_column=Column(Text, nullable=True))

    # classifier
    is_rfp: bool | None = Field(default=None, sa_column=Column(Boolean, nullable=True))
    document_type: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # formal_rfp | informal_request | vendor_pitch | other
    classification_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    classification_confidence: float | None = Field(default=None, sa_column=Column(Float, nullable=True))

    # RFP metadata (CONTEXT section 2.3)
    client_name: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    client_country: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # US | ES
    currency: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # USD | EUR, from the country
    client_contact: dict = Field(default_factory=dict, sa_column=_json_column("{}"))
    services_requested: list = Field(default_factory=list, sa_column=_json_column("[]"))  # warehousing | last_mile | reverse_logistics
    monthly_volume: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))  # orders per month, only when the RFP states it
    deadline: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # ISO date when the RFP gives a full date, else the phrase as written
    budget_range: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    departments_needed: list = Field(default_factory=list, sa_column=_json_column("[]"))  # warehouse | lastmile | reverse
    metadata_extra: dict = Field(default_factory=dict, sa_column=_json_column("{}"))  # reference number, SKUs, locations... as written in the RFP
    open_questions: list = Field(default_factory=list, sa_column=_json_column("[]"))  # what the RFP does not say

    # readability / processing cost
    readability: dict = Field(default_factory=dict, sa_column=_json_column("{}"))

    # synthesizer and routing
    summary: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # the Sales-facing text
    handoff: dict = Field(default_factory=dict, sa_column=_json_column("{}"))  # the contract Part 2 reads (docs/rfp/handoff-contract.md)
    handoff_state: str = Field(default="not_ready", sa_column=Column(Text, nullable=False))  # not_ready | ready_for_part_2
    engine: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # llm | rules | mixed: how the agents decided
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))

    # Part 2: the proposal drafting. `status` moves intake_complete -> drafting -> under_evaluation (-> needs_human_review when a section exhausts its iterations);
    # `proposal_state` says where the drafting is without inventing a status: not_started | queued | in_progress | ready_for_part_3 | interrupted
    proposal_state: str = Field(default="not_started", sa_column=Column(Text, nullable=False, server_default=text("'not_started'")))
    part3_handoff: dict = Field(default_factory=dict, sa_column=_json_column("{}"))  # the contract Part 3 reads (docs/rfp/proposal-handoff-contract.md)

    # Part 3: the human approval per department. `status` becomes `waiting_for_approval` while any approval is pending and `done` when the final document is stored;
    # `approval_state`: not_started | waiting | blocked | complete
    approval_state: str = Field(default="not_started", sa_column=Column(Text, nullable=False, server_default=text("'not_started'")))


class DepartmentSection(SQLModel, table=True):
    """What one department has to say about one RFP. Part 1 fills `key_aspects`; the other columns are for Parts 2 and 3."""

    __tablename__ = "rfp_department_sections"
    __table_args__ = (Index("ix_rfp_sections_ticket", "ticket_id", "department_id", unique=True),)

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()") if _POSTGRES else None),
    )
    ticket_id: str = Field(sa_column=Column(Text, ForeignKey("rfp_tickets.ticket_id"), nullable=False))
    department_id: str = Field(sa_column=Column(Text, nullable=False))  # warehouse | lastmile | reverse
    owner: str = Field(default="", sa_column=Column(Text, nullable=False))
    key_aspects: dict = Field(default_factory=dict, sa_column=_json_column("{}"))
    engine: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    draft_content: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # Part 2
    evaluation_results: dict = Field(default_factory=dict, sa_column=_json_column("{}"))  # Part 2
    draft_status: str = Field(default="pending", sa_column=Column(Text, nullable=False, server_default=text("'pending'")))  # Part 2: pending | drafting | under_evaluation | passed | needs_human_review
    iterations: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))  # Part 2: drafts written so far (KPI: average per section)
    draft_engine: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # Part 2: llm | rules
    approval_status: str = Field(default="pending", sa_column=Column(Text, nullable=False))  # Part 3: pending | approved | rejected (final, rounds exhausted); changes requested go back to `pending` with a new draft
    approval_round: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))  # Part 3: approval gates opened for this section (bounded)
    approval_comment: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # Part 3: the owner's last comment
    decided_by: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # Part 3: the signed-in account that registered the decision
    approval_history: list = Field(default_factory=list, sa_column=_json_column("[]"))  # Part 3: every decision: round, decision, comment, approver, at
    commitments: dict = Field(default_factory=dict, sa_column=_json_column("{}"))  # Part 3: structured commitments the arbitration reads (warehouse capacity, last-mile assumed volume)
    approver: str | None = Field(default=None, sa_column=Column(Text, nullable=True))  # Part 3
    approved_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))  # Part 3
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class RfpFinalDocument(SQLModel, table=True):
    """The final proposal (CONTEXT 2.3 FinalDocument): the approved sections consolidated, generated only when every active department has approved."""

    __tablename__ = "rfp_final_documents"

    ticket_id: str = Field(sa_column=Column(Text, ForeignKey("rfp_tickets.ticket_id"), primary_key=True))
    rfp_id: str = Field(sa_column=Column(Text, nullable=False))
    currency: str = Field(sa_column=Column(Text, nullable=False))
    sections: list = Field(default_factory=list, sa_column=_json_column("[]"))  # department, owner, content, approver, approved_at
    content_markdown: str = Field(default="", sa_column=Column(Text, nullable=False))
    generated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class RfpTraceEvent(SQLModel, table=True):
    """Which agent did what, in what order, for a ticket across the three parts: agent, node, a summary of its input and output, and the time."""

    __tablename__ = "rfp_trace_events"
    __table_args__ = (Index("ix_rfp_trace_ticket_seq", "ticket_id", "seq"),)

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()") if _POSTGRES else None),
    )
    ticket_id: str = Field(sa_column=Column(Text, nullable=False))
    seq: int = Field(sa_column=Column(Integer, nullable=False))
    part: int = Field(sa_column=Column(Integer, nullable=False))  # 1 intake, 2 drafting, 3 approval
    agent: str = Field(sa_column=Column(Text, nullable=False))  # classifier, generator:warehouse, evaluator:compliance, human:Ana Whitfield, arbiter:Miguel Torres...
    node: str = Field(sa_column=Column(Text, nullable=False))
    department_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    input: dict = Field(default_factory=dict, sa_column=_json_column("{}"))
    output: dict = Field(default_factory=dict, sa_column=_json_column("{}"))
    dedupe_key: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
