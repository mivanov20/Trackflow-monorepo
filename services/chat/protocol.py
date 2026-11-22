"""The WebSocket contract of the chat (Real Time Systems, part 2). Every frame is a JSON text frame `{"event": <name>, "data": {...}}` with an explicit event name: there is no generic "message" type.
This contract is the chat's own; it does not reuse the RFP notification payloads of part 1.

    client -> server
        auth                 {"token": "<jwt>"}                      first frame, when the token was not in the URL (`?token=`)
        user_message         {"session_id", "text"}                  a new user turn
        interrupt_requested  {"session_id", "new_input"}             abort the running generation; a non-empty `new_input` starts a new turn with it
        ping                 {}                                      liveness (answered with `pong`)
    server -> client
        session_snapshot     {"session_id", "agent_id", "status", "messages": [{"message_id", "role", "text", "status", "position", "token_sequence", ...}]}   the conversation as it is now, sent right after
                             connecting (reconnect = rehydrate); a message still `streaming` carries the text so far and the `token_sequence` it reached
        user_message         {"session_id", "message_id", "text"}    a user turn was accepted (also delivered to the user's other tabs)
        generation_started   {"session_id", "message_id"}            an assistant message begins streaming
        token_chunk          {"session_id", "message_id", "token", "sequence"}    one piece of the answer, in order
        generation_completed {"session_id", "message_id", "text"}    done; `text` is the final text (the output guard may have changed what was streamed)
        generation_interrupted {"session_id", "message_id", "status": "interrupted", "text"}   the generation was aborted; `text` is what had been generated (kept)
        generation_failed    {"session_id", "message_id", "error"}   the agent could not answer
        error                {"code", "message"}                     a frame was refused (the socket stays open unless the code says otherwise)
        pong                 {}
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# --- event names
AUTH = "auth"
USER_MESSAGE = "user_message"
INTERRUPT_REQUESTED = "interrupt_requested"
PING = "ping"
SESSION_SNAPSHOT = "session_snapshot"
GENERATION_STARTED = "generation_started"
TOKEN_CHUNK = "token_chunk"
GENERATION_COMPLETED = "generation_completed"
GENERATION_INTERRUPTED = "generation_interrupted"
GENERATION_FAILED = "generation_failed"
ERROR = "error"
PONG = "pong"

CLIENT_EVENTS = (AUTH, USER_MESSAGE, INTERRUPT_REQUESTED, PING)
SERVER_EVENTS = (SESSION_SNAPSHOT, USER_MESSAGE, GENERATION_STARTED, TOKEN_CHUNK, GENERATION_COMPLETED, GENERATION_INTERRUPTED, GENERATION_FAILED, ERROR, PONG)

AGENT_ID = "first_line_cx"
SESSION_STATUSES = ("active", "interrupted", "closed")
MESSAGE_STATUSES = ("completed", "streaming", "interrupted", "failed")

MAX_FRAME_BYTES = 16 * 1024
MAX_TEXT = 1000  # the agent's own limit for a question

# --- close codes (4xxx are ours)
CLOSE_UNAUTHORIZED = 4401
CLOSE_NOT_FOUND = 4404
CLOSE_CLOSED = 4409
CLOSE_OVERLOADED = 1013  # try again later


class FrameError(ValueError):
    """A client frame that cannot be accepted. `code` is sent back in an `error` event."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Frame:
    event: str
    data: dict


def parse_client_frame(raw: str) -> Frame:
    """The frame a client sent, validated: size, JSON, `{event, data}` shape, a known event."""
    if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
        raise FrameError("frame_too_large", f"A frame can be at most {MAX_FRAME_BYTES // 1024} KB.")
    try:
        value = json.loads(raw)
    except ValueError:
        raise FrameError("invalid_json", "The frame is not valid JSON.") from None
    if not isinstance(value, dict) or not isinstance(value.get("event"), str):
        raise FrameError("invalid_frame", 'A frame must be {"event": <name>, "data": {...}}.')
    data = value.get("data", {})
    if not isinstance(data, dict):
        raise FrameError("invalid_frame", "`data` must be an object.")
    if value["event"] not in CLIENT_EVENTS:
        raise FrameError("unknown_event", f"Unknown event `{value['event'][:40]}`. Use one of: {', '.join(CLIENT_EVENTS)}.")
    return Frame(value["event"], data)


def clean_text(value, field: str, required: bool) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise FrameError("invalid_" + field, f"`{field}` must be text.")
    text = value.strip()
    if required and not text:
        raise FrameError("empty_" + field, f"`{field}` cannot be empty.")
    if len(text) > MAX_TEXT:
        raise FrameError(field + "_too_long", f"`{field}` can be at most {MAX_TEXT} characters.")
    return text


def encode(event: str, data: dict) -> str:
    return json.dumps({"event": event, "data": data}, separators=(",", ":"), ensure_ascii=False)
