"""Observability of the harness: every time a guardrail acts, one event, with the TYPE of failure it detected.

| failure_type | Means | Examples of guards |
|---|---|---|
| `structural` | the format is wrong: a malformed model reply, a tool answer with the wrong shape, an empty or oversized output | `output_format`, `model_format`, `tool_format` |
| `content` | what is said or asked is out of bounds: off-topic, personal use, sensitive data in an answer | `personal_task`, `out_of_domain`, `output_sensitive`, `output_other_customer` |
| `security` | someone or something tries to take control or to reach what is not theirs: instruction change, prompt extraction, text in a document or tool result that tries to give orders, an order that is not the customer's, a policy swap | `instruction_change`, `prompt_extraction`, `external_instruction`, `session_authorization`, `policy_mix`, `output_leak` |

Each event is written twice: as one JSON line on the logger `trackflow.agent.guardrails` and as a row of `guardrail_events`, so it can be counted afterwards.
`summary()` answers "how many times was each guardrail triggered?" (the endpoint `GET /agent/guardrails/summary` and `scripts/guardrail_report.py` use it).
The excerpt of the text is short and redacted; a failure to write the event never breaks the request that triggered it.
"""

from __future__ import annotations

import contextlib
import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from ... import database
from ...models import GuardrailEvent
from ..memory import policy as memory_policy

logger = logging.getLogger("trackflow.agent.guardrails")
FAILURE_TYPES = ("structural", "content", "security")
ACTIONS = ("blocked", "redirected", "redacted", "isolated", "replaced")
EXCERPT = 120


@dataclass(frozen=True)
class Context:
    conversation_id: str | None = None
    actor: str | None = None
    run_id: str | None = None
    engine: Engine | None = None


_context: ContextVar[Context] = ContextVar("guardrail_context", default=Context())


@contextlib.contextmanager
def bind(context: Context):
    """Events raised deeper in the stack (isolation of a document, a malformed tool answer) carry the conversation, the user and the run of the request that caused them."""
    token = _context.set(context)
    try:
        yield
    finally:
        _context.reset(token)


def record(guard: str, failure_type: str, action: str, reason: str, *, text: str = "", context: Context | None = None) -> None:
    assert failure_type in FAILURE_TYPES and action in ACTIONS, (failure_type, action)
    ctx = context or _context.get()
    excerpt = memory_policy.redact(" ".join((text or "").split()), EXCERPT)
    logger.warning(json.dumps({"event": "guardrail", "guard": guard, "failure_type": failure_type, "action": action, "reason": reason, "conversation_id": ctx.conversation_id, "actor": ctx.actor, "run_id": ctx.run_id, "excerpt": excerpt}))
    try:
        with Session(ctx.engine or database.engine) as session:
            session.add(GuardrailEvent(guard=guard, failure_type=failure_type, action=action, reason=reason, conversation_id=ctx.conversation_id, actor=ctx.actor, run_id=ctx.run_id, excerpt=excerpt))
            session.commit()
            if failure_type == "security" and ctx.actor:
                _alert_on_repeated_attacks(session, ctx.actor)
    except Exception:  # noqa: BLE001 - observability must never take the request down
        logger.warning("could not store the guardrail event (the table may not exist yet)")


ALERT_AFTER = 5  # security events by one account ...
ALERT_WINDOW_MINUTES = 10  # ... inside this many minutes raise an alert (override: SECURITY_ALERT_AFTER, SECURITY_ALERT_WINDOW_MINUTES)


def _alert_on_repeated_attacks(session: Session, actor: str) -> None:
    """DETECT: one account that keeps tripping the security guards (injection, extraction, someone else's order, rate limit) is probing the agent. The Nth event in the window logs a
    single `security_alert` at ERROR level (once per burst: it fires on the Nth event exactly, not on every later one), which is what a log alert or a pager rule watches for."""
    import os
    from datetime import timedelta

    try:
        limit = max(1, int(os.getenv("SECURITY_ALERT_AFTER", ALERT_AFTER)))
        window = max(1, int(os.getenv("SECURITY_ALERT_WINDOW_MINUTES", ALERT_WINDOW_MINUTES)))
    except ValueError:
        limit, window = ALERT_AFTER, ALERT_WINDOW_MINUTES
    since = datetime.now(timezone.utc) - timedelta(minutes=window)
    count = session.exec(select(func.count()).select_from(GuardrailEvent).where(GuardrailEvent.actor == actor, GuardrailEvent.failure_type == "security", GuardrailEvent.created_at >= since)).one()
    if count == limit:
        logger.error(json.dumps({"event": "security_alert", "actor": actor, "security_events": count, "window_minutes": window, "reason": "repeated attempts against the agent's guardrails"}))


def summary(engine: Engine | None = None, since: datetime | None = None) -> dict:
    """How many times each guardrail was triggered (since a moment, if given): per guard, per type of failure, per action."""
    with Session(engine or database.engine) as session:
        statement = select(GuardrailEvent.guard, GuardrailEvent.failure_type, GuardrailEvent.action, func.count()).group_by(GuardrailEvent.guard, GuardrailEvent.failure_type, GuardrailEvent.action)
        if since is not None:
            statement = statement.where(GuardrailEvent.created_at >= (since if since.tzinfo else since.replace(tzinfo=timezone.utc)))
        rows = session.exec(statement).all()
    by_guard: dict[str, dict] = {}
    by_type = dict.fromkeys(FAILURE_TYPES, 0)
    by_action = dict.fromkeys(ACTIONS, 0)
    total = 0
    for guard, failure_type, action, count in rows:
        entry = by_guard.setdefault(guard, {"failure_type": failure_type, "count": 0, "actions": {}})
        entry["count"] += count
        entry["actions"][action] = entry["actions"].get(action, 0) + count
        by_type[failure_type] = by_type.get(failure_type, 0) + count
        by_action[action] = by_action.get(action, 0) + count
        total += count
    return {"total": total, "by_guard": dict(sorted(by_guard.items())), "by_failure_type": by_type, "by_action": by_action}


def recent(engine: Engine | None = None, limit: int = 50) -> list[dict]:
    with Session(engine or database.engine) as session:
        rows = session.exec(select(GuardrailEvent).order_by(GuardrailEvent.created_at.desc()).limit(limit)).all()
    return [{"created_at": r.created_at.isoformat(), "guard": r.guard, "failure_type": r.failure_type, "action": r.action, "reason": r.reason, "conversation_id": r.conversation_id, "run_id": r.run_id, "excerpt": r.excerpt} for r in rows]
