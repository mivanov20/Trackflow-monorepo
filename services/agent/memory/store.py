"""The agent's persistent memory: an explicit read / write interface over two SQL tables.

WHY SQL (the application database), and not Redis, a vector store or a knowledge graph. What TrackFlow's agent must remember is a short list of
*standing facts* filed under an exact key: a carrier rule for a country, a recurring incident of a country, a client's report preference. They are
retrieved by those keys (the carrier, the country, the client named in the question), not by semantic similarity, and getting "SEUR in Zaragoza" and
"SEUR in Los Angeles" mixed up is exactly the failure to avoid, which is what a cosine search does. Memory must also be auditable (who approved what, when),
consolidated by scope, expired and purged, and a write must be atomic with its audit record. That is a small relational problem; the application
database already exists and is already backed up and access controlled. Redis would give expiry and speed for free, but nothing here needs sub-millisecond
reads of a few dozen rows, and it has no transaction across the memory and its audit trail; a vector store would add similarity fuzz where exactness is the
point and is what the RAG milestone already uses for documents; a knowledge graph is for explicit relationships (dependencies, hierarchies), which this facts do
not have. The company RAG collections (`trackflow_knowledge`) are never touched: they stay a read-only tool. This store is a separate pair of tables.

THE ONLY WAY IN IS `resolve`. `propose` records a *pending* proposal (nothing is remembered), and `resolve` writes an entry only for an approved or edited
proposal, in the same transaction that stores the decision, who made it and when. Rejected, ignored and unclear proposals are written to the audit log and
nowhere else.

CLEANUP (`consolidate`, also run after each write): see the constants below and docs/agent/memory-design.md.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import Engine, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ... import database
from ...models import AgentMemoryEntry, AgentMemoryProposal, utc_now
from . import policy
from .schema import COUNTRY_ALIASES, INCIDENT_TTL, MemoryProposal

# ---- the cleanup policy ----
MAX_PER_SCOPE = 5  # active entries per carrier+country (or client): beyond it the oldest are merged into one
MAX_ACTIVE = 200  # active entries in the whole memory: beyond it the least useful are evicted
IDLE_DAYS = 180  # an entry nobody has needed for this long is expired (incident context has its own, shorter, lifetime)
PURGE_AFTER = timedelta(days=30)  # superseded / expired / forgotten entries are kept this long for traceability, then deleted
MERGED_MAX_CHARS = 600
DUPLICATE_SIMILARITY = 0.8
RECALL_LIMIT = 5
MIN_RECALL_SCORE = 4  # a country alone ("Zaragoza") is not enough to bring a carrier rule into an unrelated question


class PendingExists(Exception):
    """The conversation already has an unresolved proposal: there can only be one at a time."""


class NotPending(Exception):
    """The proposal was already resolved."""


@dataclass(frozen=True)
class ProposalRecord:
    id: str
    conversation_id: str
    summary: str
    kind: str
    scope_key: str
    proposal: dict


@dataclass(frozen=True)
class Resolution:
    status: str  # approved | edited | rejected | discarded_topic_change | discarded_unclear | rejected_by_policy
    label: str
    entry_id: str | None
    notice: str


@dataclass(frozen=True)
class Recalled:
    id: str
    kind: str
    scope_key: str
    content: str
    updated_at: datetime


def _aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)  # SQLite hands back naive UTC


STOPWORDS = frozenset(
    "the and for with what which when where who how why this that these those from are was were has have had not its but you your can could should would will does did our their there here any all about into onto over under after before "
    "use used using been being than then them they she him her his out per via yes".split()
)


def _tokens(text: str) -> set[str]:
    """Meaningful words only: common words ("the", "what") must not make two unrelated sentences look alike."""
    return {t for t in re.findall(r"[a-z0-9áéíóúñ]+", text.lower()) if len(t) > 2 and t not in STOPWORDS}


def similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(" ,;|") + "…"


class MemoryStore:
    def __init__(self, engine: Engine | None = None, clock: Callable[[], datetime] = utc_now):
        self._engine, self._clock = engine, clock

    @property
    def engine(self) -> Engine:
        return self._engine or database.engine  # resolved at call time, like the rest of the agent

    # ------------------------------------------------------------------ read
    def recall(self, question: str, limit: int = RECALL_LIMIT, *, touch: bool = True) -> list[Recalled]:
        """The approved memories relevant to a question: those filed under a carrier, client or country the question names. At most `limit`."""
        text = question.lower()
        words = _tokens(question)
        countries = {code for alias, code in COUNTRY_ALIASES.items() if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text)}
        now = self._clock()
        with Session(self.engine) as session:
            rows = session.exec(select(AgentMemoryEntry).where(AgentMemoryEntry.status == "active")).all()
            scored = []
            for row in rows:
                if row.expires_at and _aware(row.expires_at) <= now:
                    continue
                score = 0
                score += 3 if row.carrier and row.carrier in words else 0
                score += 3 if row.client and _tokens(row.client) & words else 0
                score += 2 if row.country and row.country in countries else 0
                score += min(3, len(_tokens(row.content) & words))
                if score >= MIN_RECALL_SCORE and (row.carrier or row.client or row.country):
                    scored.append((score, _aware(row.updated_at), row))
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            chosen = [row for _, _, row in scored[:limit]]
            if touch:  # `touch=False` is a pure read (it does not count as the memory being used)
                for row in chosen:
                    row.uses += 1
                    row.last_used_at = now
                    session.add(row)
                session.commit()
            return [Recalled(str(r.id), r.kind, r.scope_key, r.content, _aware(r.updated_at)) for r in chosen]

    def entries(self, status: str | None = "active") -> list[dict]:
        with Session(self.engine) as session:
            statement = select(AgentMemoryEntry).order_by(AgentMemoryEntry.scope_key, AgentMemoryEntry.created_at)
            if status:
                statement = statement.where(AgentMemoryEntry.status == status)
            return [self._entry_dict(r) for r in session.exec(statement).all()]

    def pending(self, conversation_id: str | None) -> ProposalRecord | None:
        if not conversation_id:
            return None
        with Session(self.engine) as session:
            row = session.exec(select(AgentMemoryProposal).where(AgentMemoryProposal.conversation_id == conversation_id, AgentMemoryProposal.status == "pending")).first()
            return self._record(row) if row else None

    def audit(self, limit: int = 100, conversation_id: str | None = None) -> list[dict]:
        """The audit log, newest first: every proposal with its outcome, who decided and when."""
        with Session(self.engine) as session:
            statement = select(AgentMemoryProposal).order_by(AgentMemoryProposal.proposed_at.desc()).limit(limit)
            if conversation_id:
                statement = statement.where(AgentMemoryProposal.conversation_id == conversation_id)
            return [self._proposal_dict(r) for r in session.exec(statement).all()]

    def is_known(self, proposal: MemoryProposal) -> bool:
        """Is this already remembered (an active entry of the same scope that says the same)? Then there is nothing new to propose."""
        with Session(self.engine) as session:
            rows = session.exec(select(AgentMemoryEntry).where(AgentMemoryEntry.scope_key == proposal.scope_key, AgentMemoryEntry.status == "active")).all()
        return any(similarity(r.content, proposal.summary) >= DUPLICATE_SIMILARITY for r in rows)

    # ------------------------------------------------------------------ propose: records a PENDING proposal, remembers nothing
    def propose(self, conversation_id: str, proposal: MemoryProposal, *, actor: str | None, origin_message: str) -> ProposalRecord:
        if self.pending(conversation_id):
            raise PendingExists(conversation_id)
        row = AgentMemoryProposal(conversation_id=conversation_id, actor=actor, origin_message=policy.redact(origin_message), proposal=proposal.model_dump(), status="pending", proposed_at=self._clock())
        with Session(self.engine) as session:
            session.add(row)
            try:
                session.commit()
            except IntegrityError:  # the partial unique index: another request got there first
                session.rollback()
                raise PendingExists(conversation_id) from None
            session.refresh(row)
            return self._record(row)

    def log_blocked(self, conversation_id: str, *, actor: str | None, origin_message: str, categories: tuple[str, ...], kind: str | None = None) -> None:
        """A proposal the policy refused before anyone was asked: logged by category only, never with the text that was refused."""
        row = AgentMemoryProposal(
            conversation_id=conversation_id, actor=actor, origin_message=policy.redact(origin_message), proposal={"kind": kind, "withheld": True}, status="blocked_by_policy",
            proposed_at=self._clock(), resolved_at=self._clock(), note=f"forbidden: {', '.join(categories)}",
        )
        with Session(self.engine) as session:
            session.add(row)
            session.commit()

    # ------------------------------------------------------------------ resolve: the ONLY place memory is written
    def resolve(self, proposal_id: str, decision, *, decided_by: str | None, message: str) -> Resolution:
        from .decisions import APPROVE, EDIT, MIN_CONFIDENCE, REJECT, UNRELATED

        now = self._clock()
        with Session(self.engine) as session:
            row = session.get(AgentMemoryProposal, uuid.UUID(proposal_id))
            if row is None or row.status != "pending":
                raise NotPending(proposal_id)
            proposal = MemoryProposal.model_validate(row.proposal)
            row.resolved_at, row.decided_by = now, decided_by
            row.decision_label, row.decision_confidence, row.decision_method = decision.label, round(float(decision.confidence), 3), decision.method
            row.decision_message = policy.redact(message)
            confident = decision.confidence >= MIN_CONFIDENCE
            entry_id, content, notice = None, None, ""

            if decision.label == APPROVE and confident:
                row.status, content = "approved", proposal.summary
            elif decision.label == EDIT and confident and decision.edited_content:
                row.status, content = "edited", " ".join(decision.edited_content.split())
            elif decision.label == REJECT and confident:
                row.status, notice = "rejected", "Okay, I won't remember that."
            elif decision.label == UNRELATED:
                row.status, notice = "discarded_topic_change", "I didn't save the earlier suggestion, since you moved on to something else."
            else:
                row.status, notice = "discarded_unclear", "I wasn't sure whether you wanted that saved, so I didn't save it."

            if content is not None:
                verdict = policy.check(content, proposal.reason, proposal.carrier, proposal.client, proposal.topic)  # the third check, on what is about to be written
                if not verdict.allowed or not (15 <= len(content) <= 300):
                    row.status, row.note = "rejected_by_policy", f"forbidden or invalid at write time: {verdict.reason or 'length'}"
                    notice = "I can't save that: " + (f"it contains {verdict.reason.replace('_', ' ')} details, which are never stored." if not verdict.allowed else "it must be one clear sentence of 15 to 300 characters.")
                    content = None
                else:
                    entry_id = self._write_entry(session, proposal, content, row.id, now)
                    row.final_content, row.entry_id = content, entry_id
                    notice = f"Saved to memory: “{content}”" if row.status == "approved" else f"Saved to memory with your edit: “{content}”"
            session.add(row)
            session.commit()
            if entry_id:
                self._consolidate(session, now)
                session.commit()
            return Resolution(row.status, decision.label, str(entry_id) if entry_id else None, notice)

    def _write_entry(self, session: Session, proposal: MemoryProposal, content: str, proposal_id: uuid.UUID, now: datetime) -> uuid.UUID:
        twins = session.exec(select(AgentMemoryEntry).where(AgentMemoryEntry.scope_key == proposal.scope_key, AgentMemoryEntry.status == "active")).all()
        for twin in twins:
            if similarity(twin.content, content) >= DUPLICATE_SIMILARITY:  # already known: confirm it instead of duplicating it
                twin.confirmations += 1
                twin.updated_at = now
                twin.source_proposal_ids = [*twin.source_proposal_ids, str(proposal_id)]
                session.add(twin)
                return twin.id
        entry = AgentMemoryEntry(
            kind=proposal.kind, scope_key=proposal.scope_key, carrier=(proposal.carrier or "").lower() or None, country=proposal.country, client=(proposal.client or "").lower() or None,
            topic=proposal.topic, content=content, created_at=now, updated_at=now, expires_at=now + INCIDENT_TTL if proposal.kind == "incident_context" else None, source_proposal_ids=[str(proposal_id)],
        )
        session.add(entry)
        session.flush()
        return entry.id

    # ------------------------------------------------------------------ consolidate / cleanup
    def consolidate(self, now: datetime | None = None) -> dict:
        with Session(self.engine) as session:
            counts = self._consolidate(session, now or self._clock())
            session.commit()
            return counts

    def _consolidate(self, session: Session, now: datetime) -> dict:
        counts = {"expired": 0, "deduplicated": 0, "merged": 0, "evicted": 0, "purged": 0}
        active = session.exec(select(AgentMemoryEntry).where(AgentMemoryEntry.status == "active")).all()
        # 1. expiry: incident context past its lifetime, and anything idle for too long
        for row in active:
            idle_since = _aware(row.last_used_at or row.updated_at)
            if (row.expires_at and _aware(row.expires_at) <= now) or (row.kind != "incident_context" and now - idle_since > timedelta(days=IDLE_DAYS)):
                row.status, row.updated_at = "expired", now
                session.add(row)
                counts["expired"] += 1
        # 2. dedupe inside each scope, 3. merge a scope that grew past MAX_PER_SCOPE (carrier + country, or client)
        by_scope: dict[str, list[AgentMemoryEntry]] = {}
        for row in session.exec(select(AgentMemoryEntry).where(AgentMemoryEntry.status == "active")).all():
            by_scope.setdefault(row.scope_key, []).append(row)
        for scope, rows in by_scope.items():
            rows.sort(key=lambda r: (r.topic != "consolidated", _aware(r.updated_at)), reverse=True)  # a consolidated note summarises OLD facts: it is always first in line to be merged again
            kept: list[AgentMemoryEntry] = []
            for row in rows:
                twin = next((k for k in kept if similarity(k.content, row.content) >= DUPLICATE_SIMILARITY), None)
                if twin:
                    twin.confirmations += row.confirmations
                    twin.source_proposal_ids = [*twin.source_proposal_ids, *row.source_proposal_ids]
                    row.status, row.updated_at = "superseded", now
                    session.add_all([twin, row])
                    counts["deduplicated"] += 1
                else:
                    kept.append(row)
            if len(kept) > MAX_PER_SCOPE:
                newest, older = kept[: MAX_PER_SCOPE - 1], kept[MAX_PER_SCOPE - 1 :]
                template = older[0]
                merged = AgentMemoryEntry(
                    kind=template.kind, scope_key=scope, carrier=template.carrier, country=template.country, client=template.client, topic="consolidated", created_at=min(_aware(o.created_at) for o in older), updated_at=now,
                    content=_trim(" | ".join(o.content for o in older), MERGED_MAX_CHARS), confirmations=sum(o.confirmations for o in older), expires_at=max((o.expires_at for o in older if o.expires_at), default=None),
                    source_proposal_ids=[pid for o in older for pid in o.source_proposal_ids],
                )
                for o in older:
                    o.status, o.updated_at = "superseded", now
                    session.add(o)
                session.add(merged)
                counts["merged"] += len(older)
        session.flush()
        # 4. global cap: evict the least useful (fewest confirmations and uses, oldest)
        active = session.exec(select(AgentMemoryEntry).where(AgentMemoryEntry.status == "active")).all()
        if len(active) > MAX_ACTIVE:
            active.sort(key=lambda r: (r.confirmations + r.uses, _aware(r.updated_at)))
            for row in active[: len(active) - MAX_ACTIVE]:
                row.status, row.updated_at = "expired", now
                session.add(row)
                counts["evicted"] += 1
        # 5. purge the dead entries after a month (the audit log of proposals is never purged)
        for row in session.exec(select(AgentMemoryEntry).where(AgentMemoryEntry.status != "active")).all():
            if now - _aware(row.updated_at) > PURGE_AFTER:
                session.delete(row)
                counts["purged"] += 1
        return counts

    def forget(self, entry_id: str, *, actor: str | None) -> bool:
        """Selective forgetting: an entry stops being recalled at once (and is purged later). The proposal that created it stays in the audit log."""
        with Session(self.engine) as session:
            row = session.get(AgentMemoryEntry, uuid.UUID(entry_id))
            if row is None or row.status != "active":
                return False
            row.status, row.updated_at = "forgotten", self._clock()
            session.add(row)
            session.commit()
            return True

    # ------------------------------------------------------------------ views
    @staticmethod
    def _record(row: AgentMemoryProposal) -> ProposalRecord:
        proposal = MemoryProposal.model_validate(row.proposal)
        return ProposalRecord(str(row.id), row.conversation_id, proposal.summary, proposal.kind, proposal.scope_key, row.proposal)

    @staticmethod
    def _entry_dict(r: AgentMemoryEntry) -> dict:
        return {"id": str(r.id), "kind": r.kind, "scope_key": r.scope_key, "content": r.content, "status": r.status, "confirmations": r.confirmations, "uses": r.uses, "created_at": _aware(r.created_at).isoformat(),
                "updated_at": _aware(r.updated_at).isoformat(), "expires_at": _aware(r.expires_at).isoformat() if r.expires_at else None, "source_proposal_ids": r.source_proposal_ids}

    @staticmethod
    def _proposal_dict(r: AgentMemoryProposal) -> dict:
        return {"id": str(r.id), "conversation_id": r.conversation_id, "actor": r.actor, "origin_message": r.origin_message, "proposal": r.proposal, "status": r.status, "proposed_at": _aware(r.proposed_at).isoformat(),
                "resolved_at": _aware(r.resolved_at).isoformat() if r.resolved_at else None, "decision_label": r.decision_label, "decision_confidence": r.decision_confidence, "decision_method": r.decision_method,
                "decision_message": r.decision_message, "decided_by": r.decided_by, "final_content": r.final_content, "entry_id": str(r.entry_id) if r.entry_id else None, "note": r.note}


def get_store() -> MemoryStore:
    return MemoryStore()
