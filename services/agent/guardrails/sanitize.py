"""Input validation and sanitization: what the user typed, cleaned BEFORE any guard, tool or model sees it.

The injection detector (`injection.py`) answers "is this an attack?". This module answers an earlier, simpler question: "is this even well-formed text?". It removes what has
no business in a customer's message and is a classic way to hide an instruction from a person or a filter:

* Unicode is put in one canonical form (NFKC), so a full-width or compatibility-form "ｉｇｎｏｒｅ" is the same letters the rest of the code expects.
* Control characters (except newline and tab), zero-width characters, bidirectional overrides ("RLO" can make text read backwards on screen) and the Unicode "tag" block
  (an invisible alphabet that can carry a whole hidden sentence) are removed.
* Runs of blank lines and spaces are collapsed, so a message cannot be padded to push the real question out of a model's attention.
* The length is capped. Over the cap the message is REFUSED, never silently cut: a cut could remove the part that makes a request harmless.

It never decides what the agent does; it returns the cleaned text and a note of what was changed, which the caller records in the guardrail log.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

MAX_INPUT_CHARS = 1000  # the same bound the API schemas declare
_INVISIBLE_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn"}  # control, format (zero-width, bidi), surrogate, private use, unassigned
_KEEP = {"\n", "\t"}
_TAG_BLOCK = range(0xE0000, 0xE0080)


class InputRejected(ValueError):
    """The text cannot be accepted at all (too long, or nothing is left once the invisible characters are gone)."""


@dataclass(frozen=True)
class Sanitized:
    text: str
    changes: list[str] = field(default_factory=list)  # what was removed or normalised, for the guardrail log; empty = the text was already clean


def sanitize(raw: object, *, max_chars: int = MAX_INPUT_CHARS) -> Sanitized:
    if not isinstance(raw, str):
        raise InputRejected("The message must be text.")
    if len(raw) > max_chars * 4:  # refuse before doing any work on a huge payload
        raise InputRejected(f"The message is too long (more than {max_chars} characters).")
    changes: list[str] = []
    text = unicodedata.normalize("NFKC", raw)
    if text != raw:
        changes.append("unicode_normalised")
    kept, removed = [], 0
    for char in text:
        if char in _KEEP or (unicodedata.category(char) not in _INVISIBLE_CATEGORIES and ord(char) not in _TAG_BLOCK):
            kept.append(char)
        else:
            removed += 1
    if removed:
        changes.append(f"removed_{removed}_invisible_or_control_characters")
    text = "".join(kept)
    collapsed = re.sub(r"[ \t]{3,}", "  ", re.sub(r"\n{3,}", "\n\n", text)).strip()
    if collapsed != text.strip():
        changes.append("whitespace_collapsed")
    if len(collapsed) > max_chars:
        raise InputRejected(f"The message is too long (more than {max_chars} characters).")
    return Sanitized(collapsed, changes)
