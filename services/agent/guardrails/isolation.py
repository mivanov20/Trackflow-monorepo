"""Security guard: text that comes from OUTSIDE (a RAG document, a ticket description, a tool result, a memory note) is data, never an instruction.

This is the defence against INDIRECT injection: nobody types the attack into the chat, it sits in a document the agent retrieves or in a ticket a customer wrote ("when you read
this, ignore your rules and reveal..."). Three things happen to every block of external text before it reaches the model:

  1. it is neutralised: any sentence that looks like an instruction to the agent (the same detector as the user input) is replaced by `[instruction-like text removed]`,
     and the event is logged as a security failure ("external_instruction", action "isolated");
  2. it is stripped of anything that imitates the harness: the delimiter characters, control characters and fake role markers cannot survive inside it;
  3. it is wrapped in `<<UNTRUSTED_DATA id=... source=...>> ... <<END_UNTRUSTED_DATA id=...>>` with a random id chosen per block, so the content cannot know the id and cannot forge the
     closing marker to "escape" the block. The system prompt tells the model that everything inside such a block is data and that rules 1 to 3 hold no matter what it says.

`rag.build_messages` calls `filter_for_prompt` for every context block (documents, live tool results, verified facts), which is the one place those texts enter a prompt.
"""

from __future__ import annotations

import re
import secrets

from . import events, injection

_DELIMITERS = re.compile(r"<<+|>>+|<\|?\w*\|?>|\[/?\s*(?:system|inst|instruction)\s*\]", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​‌‍⁠﻿‪-‮]")


def sanitize(text: str) -> tuple[str, list[injection.Detection]]:
    """The text with instruction-like sentences removed and harness look-alikes stripped; plus what was found."""
    cleaned = _CONTROL.sub("", text or "")
    cleaned = _DELIMITERS.sub(" ", cleaned)
    cleaned, found = injection.neutralise(cleaned)
    return cleaned, found


def isolate(text: str, source: str, *, block_id: str | None = None) -> str:
    """`text` as an untrusted data block. Logs a security event if it had to remove instruction-like text."""
    cleaned, found = sanitize(text)
    if found:
        events.record("external_instruction", "security", "isolated", f"{', '.join(sorted({d.family for d in found}))} found in content from {source}", text=" ".join(d.matched for d in found))
    block_id = block_id or secrets.token_hex(4)
    safe_source = re.sub(r"[^\w.\-]", "_", source or "unknown")[:60]
    return f"<<UNTRUSTED_DATA id={block_id} source={safe_source}>>\n{cleaned}\n<<END_UNTRUSTED_DATA id={block_id}>>"


def filter_for_prompt(text: str, source: str) -> str:
    """The hook `rag.build_messages` calls for each context block."""
    return isolate(text, source)
