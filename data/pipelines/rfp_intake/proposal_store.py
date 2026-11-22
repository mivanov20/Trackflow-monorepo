"""Persistence of Part 2: drafts, evaluation results and the ticket's progress, in the application database (PostgreSQL / Supabase in production).

The ticket row of Part 1 continues: `intake_complete` -> `drafting` -> `under_evaluation` -> (`needs_human_review`). The status is DERIVED from the state of the sections after every
event (`derive_status`), so it is always what is really happening:

    drafting           some section has not produced its first draft yet
    under_evaluation   every section has a draft; evaluators and the generator-evaluator loop are running (also while a section is rewritten after feedback)
    needs_human_review once every section has finished and at least one exhausted its iterations (last draft + EvaluationResult kept)
    under_evaluation   with `proposal_state = ready_for_part_3` when every section has finished and all passed (Part 3 statuses are not invented here)

`DbReporter` receives the graph's events and writes them. The departments run in parallel threads, so every write takes one lock (SQLite in development has one connection).
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlmodel import Session

from . import store, trace
from .dblock import LOCK
from .store import now

DRAFTING, UNDER_EVALUATION, NEEDS_HUMAN_REVIEW = "drafting", "under_evaluation", "needs_human_review"
FINISHED = {"passed", "needs_human_review"}
READY_FOR_PART_3 = "ready_for_part_3"

LABELS = {
    "queued": "Waiting to draft the proposal",
    "drafting": "Writing the department sections",
    "under_evaluation": "Evaluating the drafts (readability, relevance, compliance)",
    "needs_human_review": "A section needs human review",
    "ready": "Proposal sections ready for approval",
}


def ensure_columns(engine) -> None:
    """`create_all` does not alter existing tables: adds the Part 2 columns to the Part 1 tables of an existing database (idempotent)."""
    from services import models

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for model in (models.RfpTicket, models.DepartmentSection):
            table = model.__table__
            if table.name not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                kind = column.type.compile(dialect=engine.dialect)
                default = ""
                if column.server_default is not None:
                    default = f" DEFAULT {column.server_default.arg.text if hasattr(column.server_default.arg, 'text') else column.server_default.arg}"
                null = " NOT NULL" if not column.nullable and default else ""
                connection.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}{null}{default}'))


def derive_status(sections: list) -> str:
    if sections and all(section.draft_status in FINISHED for section in sections):
        return NEEDS_HUMAN_REVIEW if any(section.draft_status == "needs_human_review" for section in sections) else UNDER_EVALUATION
    if any(section.iterations == 0 for section in sections):
        return DRAFTING
    return UNDER_EVALUATION


def step_label(status: str, sections: list, finished: bool) -> str:
    if finished:
        return LABELS["needs_human_review"] if status == NEEDS_HUMAN_REVIEW else LABELS["ready"]
    return LABELS["drafting"] if status == DRAFTING else LABELS["under_evaluation"]


def mark_queued(session: Session, ticket_id: str) -> None:
    ticket = store.get_ticket(session, ticket_id)
    ticket.proposal_state = "queued"
    ticket.updated_at = now()
    session.add(ticket)
    session.commit()


def begin(session: Session, ticket_id: str) -> None:
    """The drafting starts: every section is `drafting` with no draft yet, and the ticket leaves `intake_complete`."""
    ticket = store.get_ticket(session, ticket_id)
    previous_status = ticket.status
    for section in store.sections_of(session, ticket_id):
        section.draft_status, section.iterations, section.draft_content, section.evaluation_results, section.draft_engine = "drafting", 0, None, {}, None
        session.add(section)
    ticket.status, ticket.proposal_state, ticket.error_message = DRAFTING, "in_progress", None
    ticket.part3_handoff = {}
    ticket.current_step = LABELS["drafting"]
    ticket.progress = [*ticket.progress, {"step": "proposal", "detail": "Drafting started from the intake handoff", "at": now().isoformat()}]
    ticket.updated_at = now()
    session.add(ticket)
    session.commit()
    trace.status_change(session.get_bind(), ticket_id, 2, previous_status, DRAFTING, "the proposal drafting started from the intake handoff")


class DbReporter:
    """Writes what the graph reports: each draft and each evaluation as soon as they exist, and the ticket status derived from the sections."""

    def __init__(self, engine, ticket_id: str):
        self.engine, self.ticket_id, self.lock = engine, ticket_id, LOCK

    def _section(self, session: Session, department_id: str):
        return next(section for section in store.sections_of(session, self.ticket_id) if section.department_id == department_id)

    def _refresh_ticket(self, session: Session, entry: dict | None) -> tuple[str, str]:
        """Updates the ticket from its sections and returns (old, new) status, so the caller records the change once the transaction is committed."""
        ticket = store.get_ticket(session, self.ticket_id)
        sections = store.sections_of(session, self.ticket_id)
        old = ticket.status
        ticket.status = derive_status(sections)
        ticket.current_step = step_label(ticket.status, sections, finished=False)
        if entry:
            ticket.progress = [*ticket.progress, {**entry, "at": now().isoformat()}]
        ticket.updated_at = now()
        session.add(ticket)
        return old, ticket.status

    def _announce(self, change: tuple[str, str], reason: str) -> None:
        trace.status_change(self.engine, self.ticket_id, 2, change[0], change[1], reason)

    def trace(self, agent: str, node: str, input: dict, output: dict, department_id: str | None = None) -> None:
        trace.record(self.engine, self.ticket_id, 2, agent, node, input, output, department_id)

    def generating(self, department_id: str, iteration: int) -> None:
        with self.lock, Session(self.engine) as session:
            section = self._section(session, department_id)
            section.draft_status = "drafting"
            session.add(section)
            session.flush()
            change = self._refresh_ticket(session, {"step": f"draft:{department_id}", "detail": f"Attempt {iteration}: writing the {department_id} section"})
            session.commit()
        self._announce(change, f"{department_id} is being written")

    def drafted(self, department_id: str, iteration: int, content: str, engine: str) -> None:
        with self.lock, Session(self.engine) as session:
            section = self._section(session, department_id)
            section.draft_content, section.iterations, section.draft_engine, section.draft_status = content, iteration, engine, "under_evaluation"
            session.add(section)
            session.flush()
            change = self._refresh_ticket(session, {"step": f"evaluate:{department_id}", "detail": f"Attempt {iteration}: readability, relevance and compliance running in parallel"})
            session.commit()
        self._announce(change, "every section has a draft: the evaluators are running")

    def evaluated(self, department_id: str, iteration: int, result: dict, outcome: str | None) -> None:
        with self.lock, Session(self.engine) as session:
            section = self._section(session, department_id)
            earlier = (section.evaluation_results or {}).get("history", [])
            if section.evaluation_results.get("section_id"):
                earlier = [*earlier, {key: section.evaluation_results[key] for key in ("iteration", "overall_pass", "feedback_for_generator")}]
            section.evaluation_results = {**result, "history": earlier}
            if outcome:
                section.draft_status = outcome
            else:  # failed with iterations left: the section goes back to its generator
                section.draft_status = "drafting"
            session.add(section)
            session.flush()
            verdict = "passed" if result["overall_pass"] else "failed"
            detail = f"Attempt {iteration}: {verdict}" + ("" if result["overall_pass"] else f" ({result['feedback_for_generator'][:200]})") + (f" -> {outcome}" if outcome else " -> back to the generator")
            change = self._refresh_ticket(session, {"step": f"result:{department_id}", "detail": detail})
            session.commit()
        self._announce(change, f"{department_id} was evaluated")


def finish(session: Session, ticket_id: str, part3: dict) -> str:
    """Every section finished: the Part 3 handoff is stored with the final status, in one transaction. Returns the ticket status."""
    ticket = store.get_ticket(session, ticket_id)
    sections = store.sections_of(session, ticket_id)
    previous_status = ticket.status
    ticket.status = derive_status(sections)
    ticket.part3_handoff = part3
    ticket.proposal_state = READY_FOR_PART_3
    ticket.current_step = step_label(ticket.status, sections, finished=True)
    ticket.progress = [*ticket.progress, {"step": "proposal_ready", "detail": f"{len(sections)} section(s) handed to Part 3; {len(part3['needs_human_review'])} need human review", "at": now().isoformat()}]
    ticket.updated_at = now()
    session.add(ticket)
    session.commit()
    trace.status_change(session.get_bind(), ticket_id, 2, previous_status, ticket.status, "every section finished: handed to Part 3")
    return ticket.status


def fail(session: Session, ticket_id: str, message: str, state: str = "failed") -> None:
    """The drafting broke: back to `intake_complete` (the intake result is intact) with the reason, so it can be started again."""
    ticket = store.get_ticket(session, ticket_id)
    if ticket is None:
        return
    previous_status = ticket.status
    ticket.status, ticket.proposal_state, ticket.error_message = "intake_complete", state, message
    ticket.current_step = "Proposal drafting failed"
    ticket.progress = [*ticket.progress, {"step": "proposal_failed", "detail": message, "at": now().isoformat()}]
    ticket.updated_at = now()
    session.add(ticket)
    session.commit()
    trace.status_change(session.get_bind(), ticket_id, 2, previous_status, "intake_complete", message)


def interrupt_stale(session: Session) -> int:
    """At startup: a drafting that was running when the process stopped cannot continue; the ticket goes back to `intake_complete` with `proposal_state = interrupted`."""
    from services import models
    from sqlmodel import select

    stale = list(session.exec(select(models.RfpTicket).where(models.RfpTicket.proposal_state.in_(("queued", "in_progress")))))
    for ticket in stale:
        fail(session, ticket.ticket_id, "The proposal drafting was interrupted when the service restarted. Start it again.", "interrupted")
    return len(stale)
