"""The agent's answer with its self-evaluation, in ONE model call: the text the user sees, plus an optional `memory_proposal`.

There is no second model call, no second agent and no new graph: it is the same answer step with one more output field. The model is told the explicit criterion
(below) and asked to say `null` for almost every interaction; the server then does not trust its judgement blindly (`vet`): a non-memorable kind of message is dropped
whatever the model says, a proposal that is incomplete, forbidden by the policy, already remembered, or about an incident that is not recurring is dropped, and
nothing is written: a surviving proposal only becomes a question to the user.

THE CRITERION (what the prompt states, and what `NON_MEMORABLE` enforces for the clearest cases)
Propose to remember something ONLY if, in this interaction, the user:
  1. corrected a carrier assignment or coverage rule (a carrier changed its coverage for a zone, or stopped a route), or
  2. gave context about a KNOWN, RECURRING incident that would otherwise be re-escalated (a strike, a port closure; reported at least twice), or
  3. stated a standing preference of a recurring B2B client for its monthly report (format, highlighted metrics),
and it is a standing fact, not something about this one conversation. Never for: a one-off tracking question, a closing remark, a single-use task (a translation),
and never anything the policy forbids (customer or client addresses, warehouse routes or access details, a single package's data, contract negotiations).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from data.pipelines import rag
from data.process import model_client
from data.process import rag_config as config

from ..guardrails import events, isolation, prompt as secure_prompt
from . import policy
from .schema import MemoryProposal, parse_proposal
from .store import MemoryStore, Recalled

MEMORY_INSTRUCTIONS = """You also keep a small memory of standing facts that support agents confirm. The block "Remembered notes" in the user message lists the notes that are relevant now: they were approved by support agents on a given date. Use them like any other context, but they can be outdated: if a note and a live system disagree, trust the live system and say so.

SELF-EVALUATION. After you answer, decide whether this interaction contains something worth remembering. Almost always the answer is NO: memory_proposal must be null. Propose ONLY if the user, in this interaction:
 1. corrected a carrier assignment or coverage rule (a carrier changed its coverage for a zone, or stopped a route), or
 2. gave context about a KNOWN, RECURRING incident that would otherwise be escalated again (a strike, a port closure; reported at least twice), or
 3. stated a standing preference of a recurring B2B client for its monthly report (format, highlighted metrics),
and it is a standing fact, not about this one conversation. Examples that SHOULD produce a proposal:
 - "Actually SEUR no longer covers that rural area of Zaragoza, we've had to use the local carrier since last month."
 - "Those delays in the Los Angeles incidents this week are from the port strike, that's the third ticket about it already."
 - "The cosmetics client always wants its monthly report with the returns breakdown first, before shipment volume."
Examples that must NOT produce a proposal (memory_proposal: null): "Where's the package with tracking XJ4471?" (a one-off query), "Great, that's resolved." (a closing remark), "Translate this into English for the client." (a single-use task).
NEVER propose anything that contains: an address of an end customer or of a client site, warehouse routes or access details (aisles, racks, dock doors, codes), the data of a single package or complaint (tracking or order numbers), or anything from a commercial negotiation (contracts, discounts, quotes). If the only memorable part would contain one of those, propose nothing.
When in doubt, propose nothing. You never save anything yourself: a proposal is only a suggestion that the user is asked to confirm.

OUTPUT: reply with ONE JSON object and nothing else:
{"answer": "<what the user sees>", "memory_proposal": null}
or, when the criterion is met:
{"answer": "<what the user sees>", "memory_proposal": {"kind": "carrier_rule" | "incident_context" | "client_preference", "summary": "<the fact, one sentence, as it would be stored>", "reason": "<why it is worth remembering>", "carrier": "<carrier or null>", "country": "US" | "ES" | null, "client": "<client or null>", "topic": "<short topic or null>", "occurrences": <integer or null>}}
Do not put the question "want me to remember this?" in the answer: the system adds it."""

# The identity, authority, domain and sensitive-data rules are the harness's secure prompt (one prompt for the whole agent); memory adds its own instructions on top.
SYSTEM_PROMPT = secure_prompt.system_prompt(extra=MEMORY_INSTRUCTIONS)

# the clearest kinds of message that are never memorable (CONTEXT-company.md examples): enforced even if the model proposes something
NON_MEMORABLE = (
    ("closing", re.compile(r"^\s*(?:great|thanks|thank you|perfect|ok(?:ay)?|cool|got it|bye|goodbye|all good|that'?s (?:resolved|fixed|solved|all|great)|resolved|done)\b[^?]{0,50}$", re.IGNORECASE)),
    ("single_use_task", re.compile(r"^\s*(?:please\s+)?(?:translate|rewrite|summari[sz]e|proofread|reword|paraphrase|draft|write)\b", re.IGNORECASE)),
    ("one_off_tracking_query", re.compile(r"\b(?:where(?:'s| is)|status of|track(?:ing)?|locate|any news (?:on|about))\b[^.]{0,60}\b(?:[A-Z]{2}\s?\d{4,}|package|parcel|shipment|order)\b", re.IGNORECASE)),
)


def memorable_kind_of_message(message: str) -> tuple[bool, str]:
    """Is this the kind of message that can contain something to remember at all? `(False, why)` for a closing, a single-use task or a one-off tracking question."""
    for name, pattern in NON_MEMORABLE:
        if pattern.search(message or ""):
            return False, name
    return True, ""


@dataclass(frozen=True)
class Turn:
    answer: str
    proposal_raw: dict | None


def _remembered_block(memories: list[Recalled]) -> str:
    if not memories:
        return "Remembered notes: none relevant."
    lines = [f"- ({m.updated_at.date().isoformat()}, {m.kind.replace('_', ' ')}) {m.content}" for m in memories]
    # a note is text some user approved: it is data too, so it is isolated like a document (an instruction smuggled into memory must not become an order)
    return "Remembered notes (approved by support agents; they can be outdated):\n" + isolation.isolate("\n".join(lines), "agent-memory")


def build_messages(question: str, blocks: list[dict], memories: list[Recalled], scope_subtype: str | None = None) -> list[dict]:
    """The same context and question as the RAG prompt, plus the notes that are relevant NOW (at most a handful, read from the store: memory is not appended to the system prompt)."""
    user = rag.build_messages(question, blocks)[1]["content"]
    return [{"role": "system", "content": secure_prompt.system_prompt(scope_subtype, extra=MEMORY_INSTRUCTIONS)}, {"role": "user", "content": f"{_remembered_block(memories)}\n\n{user}"}]


def parse_turn(raw: str) -> Turn:
    """The model's JSON. If it is not valid JSON the whole text is the answer and there is no proposal: a broken format can never create a proposal."""
    try:
        data = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
        answer = data["answer"]
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("no answer")
        proposal = data.get("memory_proposal")
        return Turn(answer.strip(), proposal if isinstance(proposal, dict) else None)
    except Exception:  # noqa: BLE001
        events.record("model_format", "structural", "replaced", "the model's reply was not the expected JSON; it is used as a plain answer and no proposal is made", text=raw[:80])
        return Turn(raw.strip(), None)


def generate_with_self_evaluation(question: str, blocks: list[dict], memories: list[Recalled], scope_subtype: str | None = None) -> Turn:
    """ONE call to the generation model: the answer and the self-evaluation together."""
    model = config.generation_model()
    if not model:
        raise model_client.ModelServiceError("The generation model is not configured: set LLM_MODEL.")
    body = model_client.post("/chat/completions", {"model": model, "temperature": 0, "max_tokens": 700, "messages": build_messages(question, blocks, memories, scope_subtype)})
    try:
        raw = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise model_client.ModelServiceError("The generation response has no answer.") from None
    if not isinstance(raw, str) or not raw.strip():
        raise model_client.ModelServiceError("The generation model returned an empty answer.")
    return parse_turn(raw)


@dataclass(frozen=True)
class Vetted:
    proposal: MemoryProposal | None
    dropped: str = ""  # why a proposal did not go ahead ("" if there was none or it passed)
    blocked_categories: tuple[str, ...] = ()


def vet(raw: dict | None, user_message: str, store: MemoryStore) -> Vetted:
    """The server-side gates between the model's suggestion and a question to the user. Nothing here writes memory."""
    if raw is None:
        return Vetted(None)
    memorable, why = memorable_kind_of_message(user_message)
    if not memorable:
        return Vetted(None, f"the message is not the kind that holds a memory ({why})")
    try:
        proposal = parse_proposal(raw)
    except ValueError as error:
        return Vetted(None, f"invalid proposal: {error}")
    if proposal is None:
        return Vetted(None)
    verdict = policy.check(proposal.summary, proposal.reason, proposal.carrier, proposal.client, proposal.topic)
    if not verdict.allowed:
        return Vetted(None, "forbidden content", verdict.categories)
    if store.is_known(proposal):
        return Vetted(None, "already remembered")
    return Vetted(proposal)


def proposal_question(proposal: MemoryProposal) -> str:
    """The question appended to the answer, in the same conversation."""
    return f"\n\n---\nWant me to remember this for next time?\n> {proposal.summary}\nReply yes to save it, no to skip it, or tell me how to change it."
