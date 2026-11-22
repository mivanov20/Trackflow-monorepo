"""The harness around the agent: the guards, in order, before the model and after it.

    user message
        1. instruction change / prompt extraction   (security)  -> firm refusal, the model is never called
        2. session authorization of every order mentioned (security) -> refusal; nothing is looked up
        3. policy mix: "apply Spain's policy to my Los Angeles order" (security) -> the order's country decides; refusal
        4. scope: personal-assistant use (content) -> refusal + redirect ; casual / general (content) -> goes on, flagged, the model is told to answer briefly and redirect
        -> the agent (graph): retrieval, tools, memory; every external text isolated as data (security)
        5. output guard on the final answer: format (structural), leaked instructions (security), sensitive data (content), redirect for casual answers (content)
    user answer

The model decides what to say; the harness decides whether it may be asked, what it is given, and whether what it said may be shown. None of the first four steps depends on a
live model, so they can be (and are) tested with fixed fixtures. Every action is logged with its type of failure (`events.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Engine

from ..memory import store as memory_store
from . import events, injection, messages, output
from .scope import classify_scope, country_name, detect_policy_mix
from .session import SessionContext, authorize, normalise_reference

BLOCKED_OUTCOMES = {
    "instruction_change": "blocked_instruction_change",
    "prompt_extraction": "blocked_prompt_extraction",
    "personal_task": "blocked_personal_task",
    "session_authorization": "blocked_authorization",
    "policy_mix": "blocked_policy_mix",
}


@dataclass
class Pre:
    blocked: bool = False
    reply: str = ""
    outcome: str = ""
    guard: str = ""
    failure_type: str = ""
    scope: str = "domain"  # domain | casual | personal_task
    scope_subtype: str = ""
    verified_facts: list[dict] = field(default_factory=list)
    authorized_refs: set[str] = field(default_factory=set)
    triggered: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict | None:
        return {"guard": self.guard, "failure_type": self.failure_type, "action": "blocked"} if self.blocked else None


def _block(pre: Pre, guard: str, failure_type: str, reply: str, reason: str, ctx: events.Context, text: str) -> Pre:
    events.record(guard, failure_type, "blocked", reason, text=text, context=ctx)
    pre.blocked, pre.reply, pre.guard, pre.failure_type, pre.outcome = True, reply, guard, failure_type, BLOCKED_OUTCOMES[guard]
    pre.triggered.append(guard)
    return pre


def _fact(index: int, shipment) -> dict:
    where = country_name(shipment.country)
    label = f"#{shipment.order_id}" if shipment.order_id else shipment.tracking_number
    return {
        "source_document": "shipment-registry",
        "section": f"Order {label} (verified for this session)",
        "chunk_index": index,
        "text": f"Order {label} (tracking {shipment.tracking_number}) was shipped in {where}; status: {shipment.status}. The return policy and the SLAs of {where} apply to this order, and only those.",
        "score": 1.0,
    }


def pre_check(question: str, *, session: SessionContext | None = None, conversation_id: str | None = None, actor: str | None = None, run_id: str | None = None, engine: Engine | None = None) -> Pre:
    """Runs the input guards in order. A blocked result carries the fixed reply; the agent and the model are not reached."""
    pre = Pre()
    ctx = events.Context(conversation_id=conversation_id, actor=actor, run_id=run_id, engine=engine)
    # 1. security: instruction change and prompt extraction (also when the message is a decision about a memory proposal)
    found = injection.detect(question)
    if found:
        extraction = any(d.family == "prompt_extraction" for d in found) and not any(d.family != "prompt_extraction" for d in found)
        guard = "prompt_extraction" if extraction else "instruction_change"
        return _block(pre, guard, "security", messages.PROMPT_EXTRACTION if extraction else messages.INSTRUCTION_CHANGE, "families: " + ", ".join(d.family for d in found), ctx, question)
    # 2. security: session-based authorization of every order or tracking number mentioned
    order_country = None
    if session is not None:
        access = authorize(session, question, engine)
        if not access.allowed:
            return _block(pre, "session_authorization", "security", messages.AUTHORIZATION, f"{len(access.denied)} reference(s) not linked to this session", ctx, question)
        pre.authorized_refs = {normalise_reference(r) for r in access.references}
        pre.verified_facts = [_fact(i, s) for i, s in enumerate(access.shipments)]
        order_country = access.shipments[0].country if access.shipments else None
    # 3. security: a swap of the return policy of one country for another
    mix = detect_policy_mix(question, order_country)
    if mix is not None:
        return _block(pre, "policy_mix", "security", messages.policy_mix(mix.requested, mix.actual), f"asked for {mix.requested}, order is {mix.actual or 'unknown'}", ctx, question)
    # 4. content: scope. A reply to a pending memory proposal ("yes", "no thanks") is a decision, not a topic
    pending = memory_store.get_store().pending(conversation_id) if conversation_id else None
    if pending is None:
        scope = classify_scope(question)
        pre.scope, pre.scope_subtype = scope.category, scope.subtype
        if scope.category == "personal_task":
            return _block(pre, "personal_task", "content", messages.PERSONAL_TASK, scope.reason, ctx, question)
        if scope.category == "casual":
            events.record("out_of_domain", "content", "redirected", scope.subtype, text=question, context=ctx)
            pre.triggered.append("out_of_domain")
    return pre


def post_check(answer: str, outcome: str, pre: Pre, *, question: str, session: SessionContext | None, conversation_id: str | None = None, actor: str | None = None, run_id: str | None = None, engine: Engine | None = None, trusted: bool = False) -> output.OutputVerdict:
    """The output guard on the agent's final answer."""
    ctx = events.Context(conversation_id=conversation_id, actor=actor, run_id=run_id, engine=engine)
    with events.bind(ctx):
        return output.validate(answer, question=question, session=session, authorized_refs=pre.authorized_refs, scope=pre.scope, outcome=outcome, trusted=trusted)
