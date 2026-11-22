"""Runs the agent's memory flow end to end and writes the evidence document `docs/agent/memory-evidence.md` from what actually happens.

    python scripts/memory_evidence.py

Cycle A: a memory update is proposed, approved, and shows up in a later conversation. Cycle B: a proposal is rejected and memory stays unchanged. Plus two short
cases (a change of topic, a proposal blocked by the policy). Everything is the real code: the graph, the decision classifier, the policy, the store and its audit
log, and the prompt the next conversation receives. The ONE stand-in is the language model: no live model is available to this script, so the model's replies are
scripted by hand, in exactly the JSON format the real model is asked to produce. The document says so at the top.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "evidence-only-secret-with-at-least-32-chars")

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import SQLModel, create_engine  # noqa: E402

from data.pipelines import rag  # noqa: E402
from data.process import model_client  # noqa: E402
from services import database  # noqa: E402
from services.agent import graph as agent_graph  # noqa: E402
from services.agent import runner  # noqa: E402
from services.agent.memory.store import MemoryStore  # noqa: E402

OUT = ROOT / "docs" / "agent" / "memory-evidence.md"
CHUNK = {"source_document": "carrier-coverage", "section": "Carrier Coverage > Spain carriers (Zaragoza)", "chunk_index": 1, "text": "SEUR: best coverage in rural areas of Aragón.", "score": 0.9}
SEUR = {"kind": "carrier_rule", "summary": "SEUR no longer covers the rural area of Zaragoza; the local carrier has been used since last month.", "reason": "A support agent corrected the carrier assignment rule.", "carrier": "SEUR", "country": "ES"}
COSMETICS = {"kind": "client_preference", "summary": "The cosmetics client wants its monthly report with the returns breakdown first, before shipment volume.", "reason": "A standing report preference of a recurring B2B client.", "client": "Cosmetics Co"}
LEAKY = {"kind": "client_preference", "summary": "The cosmetics client wants its monthly report sent to 500 Industrial Way, Los Angeles CA 90058.", "reason": "A standing report preference.", "client": "Cosmetics Co"}


class Scripted:
    def __init__(self):
        self.turns, self.prompts = [], []

    def say(self, answer, proposal=None):
        self.turns.append({"answer": answer, "memory_proposal": proposal})

    def post(self, path, payload):
        self.prompts.append(payload["messages"][-1]["content"])
        return {"choices": [{"message": {"content": json.dumps(self.turns.pop(0))}}]}


def main() -> int:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    database.engine = engine
    os.environ["LLM_MODEL"] = "scripted-model"
    model = Scripted()
    model_client.post = model.post
    rag.retrieve = lambda question, **kw: [CHUNK]
    agent = agent_graph.compile_graph(agent_graph.build_graph(), MemorySaver())
    store = MemoryStore(engine)
    lines: list[str] = []

    def talk(conversation, actor, message):
        result = runner.run_agent(message, graph=agent, engine=engine, conversation_id=conversation, actor=actor)
        steps = " → ".join(s["node"] for s in runner.run_trace(result.run_id, engine=engine))
        lines.extend([f"**{actor}:** {message}", "", "**Agent:**", "", *[f"> {line}" if line else ">" for line in result.answer.splitlines()], "", f"*trace: {steps}*", ""])
        return result

    def state(title):
        lines.extend([f"**{title}**", ""])
        entries = store.entries(None)
        lines.append("`agent_memory_entries`: " + ("*(empty)*" if not entries else ""))
        for e in entries:
            lines.append(f"- `{e['scope_key']}` ({e['status']}, confirmations {e['confirmations']}): {e['content']}")
        lines.extend(["", "`agent_memory_proposals` (audit log):", "", "| conversation | status | decision | confidence | method | decided by | decided at (UTC) | the message that decided | written |", "|---|---|---|---|---|---|---|---|---|"])
        for a in reversed(store.audit()):
            lines.append(
                f"| {a['conversation_id']} | **{a['status']}** | {a['decision_label'] or '-'} | {a['decision_confidence'] if a['decision_confidence'] is not None else '-'} | {a['decision_method'] or '-'} | {a['decided_by'] or '-'} | "
                f"{(a['resolved_at'] or '-')[:19].replace('T', ' ')} | {a['decision_message'] or a['note'] or '-'} | {a['final_content'] or '*nothing*'} |"
            )
        lines.append("")

    lines.extend(
        [
            "# Agent memory: evidence of the proposal → decision → memory flow",
            "",
            "Generated by `python scripts/memory_evidence.py` from a real run of the agent's graph, decision classifier, forbidden-content policy, memory store and audit log (an in-memory database). Design and policy: [memory-design.md](memory-design.md).",
            "",
            "> **What is real and what is not.** No live language model was available to this run, so the model's replies (the answer and its `memory_proposal` field) are **scripted by hand**, in exactly the JSON format the real model is asked to produce. Everything that happens *around* the model is the real code: the gates on the proposal, the policy, the classification of the user's reply, the store, the audit log and the prompt that the next conversation receives. The text of those prompts below is the real prompt.",
            "",
            "## Cycle A: approved, and reflected in a later conversation",
            "",
            "**Conversation `conv-A1`.** A support agent corrects a carrier rule.",
            "",
        ]
    )
    model.say("Thanks for the correction: I'll treat the local carrier as the option for rural Zaragoza from now on in this conversation.", SEUR)
    talk("conv-A1", "valentina", "Actually SEUR no longer covers that rural area of Zaragoza, we've had to use the local carrier since last month.")
    state("State after the proposal, before any decision (nothing is remembered yet):")
    lines.extend(["The user answers the proposal in the next message:", ""])
    talk("conv-A1", "valentina", "Yes, please")
    state("State after the approval:")
    lines.extend(["**Conversation `conv-A2`**, a new conversation by a different support agent, later. Nothing in the question says anything about the correction:", ""])
    model.say("Don't use SEUR for the rural area of Zaragoza: it stopped covering it. Use the local carrier.")
    result = talk("conv-A2", "carlos", "Which carrier should I use for a rural address near Zaragoza? SEUR?")
    prompt = model.prompts[-1]
    start = prompt.index("Remembered notes")
    lines.extend(["The prompt the model received for that question started with this block, read from the memory store (not appended to the system prompt):", "", "```text", prompt[start : prompt.index("Context:")].strip(), "```", ""])
    lines.extend([f"The trace of that run records which memory was used: `memories_used = {runner.run_trace(result.run_id, engine=engine)[-1]['output']['memories_used']}`.", ""])

    lines.extend(["## Cycle B: rejected, memory unchanged", "", "**Conversation `conv-B1`.**", ""])
    before = store.entries(None)
    model.say("Understood.", COSMETICS)
    talk("conv-B1", "valentina", "The cosmetics client always wants their monthly report with the returns breakdown first, before shipment volume.")
    talk("conv-B1", "valentina", "No thanks")
    state("State after the rejection:")
    lines.append(f"Memory entries before cycle B: {len(before)}; after: {len(store.entries(None))} (unchanged). The rejected proposal is still in the audit log above, with who refused it and when.")
    lines.extend(["", "**Conversation `conv-B2`**, later: the agent knows nothing about the rejected suggestion:", ""])
    model.say("Format it however the client's template asks.")
    talk("conv-B2", "carlos", "How should I format the monthly report for the cosmetics client?")
    prompt = model.prompts[-1]
    lines.extend(["The memory block of that prompt:", "", "```text", prompt[prompt.index("Remembered notes") :].split("\n\n")[0], "```", ""])
    assert COSMETICS["summary"] not in prompt

    lines.extend(["## Also: a change of topic is a discard, never an approval", "", "**Conversation `conv-C1`.**", ""])
    model.say("Noted.", COSMETICS)
    talk("conv-C1", "valentina", "The cosmetics client always wants their monthly report with the returns breakdown first, before shipment volume.")
    model.say("Express shipping takes 24 to 48 hours.")
    talk("conv-C1", "valentina", "What is the SLA for express?")
    lines.extend(["The proposal was discarded (`discarded_topic_change`) and the new question was still answered. A message such as \"maybe\", \"ok\" or \"sounds good\" is discarded too (`discarded_unclear`): approval is never assumed.", ""])

    lines.extend(["## Also: a proposal with forbidden content is never shown to the user", "", "**Conversation `conv-D1`.**", ""])
    model.say("Noted.", LEAKY)
    talk("conv-D1", "valentina", "The cosmetics client always wants the report sent to their warehouse.")
    state("State after the blocked proposal (the audit log keeps the category, never the text):")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
