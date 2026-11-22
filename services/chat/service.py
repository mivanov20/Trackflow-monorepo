"""The chat service: sessions, turns, and the generations that stream the first-line CX agent's answer. It knows nothing about WebSockets: it PRODUCES events to the session's pub/sub channel
(`chat.<session_id>`) and the connections consume them.

A generation is one assistant message being produced in a worker thread. The agent is the support agent as it is (its logic, tools and guardrails are untouched); only how its answer reaches the user changes:
  * `token_chunk` events as the model generates (LangGraph `custom` stream mode, see `services/agent/streaming.py`);
  * an interrupt really aborts it: the control is cancelled, no further token is published (the check and the publish happen under one lock), the connection to the model is closed, the partial message is KEPT
    and marked `interrupted` (never overwritten), and a new turn with `new_input` starts as a NEW assistant message;
  * a generation outlives its connection: a client that reconnects is rehydrated with the text so far (`session_snapshot`) and continues from the next `sequence`.
The agent has no conversation memory of its own (it answers one question at a time, by design), so the thread the user sees is the message history stored here; each turn answers the user's latest text.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
import weakref
from dataclasses import dataclass, field

from ..agent.streaming import Control, GenerationCancelled
from . import protocol, store
from .pubsub import ChatBus, bus as default_bus

logger = logging.getLogger("trackflow.chat")

FLUSH_EVERY = 8  # tokens between saves of the partial text (a crash keeps almost everything that was shown)
FAILURE_TEXT = "The support agent could not answer right now. Please try again in a moment."


class ChatError(Exception):
    """A request the chat cannot honour. `code` goes to the client in an `error` event."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def agent_runner(question: str, *, run_id: str, user_id: str, role: str, on_token, control: Control):
    """The first-line CX agent, exactly as the HTTP endpoint runs it (same session context: the customer's own orders), streaming."""
    from .. import agent
    from ..agent.guardrails.session import SessionContext

    return agent.run_agent(question, run_id=run_id, conversation_id=None, actor=user_id, session=SessionContext(customer_id=user_id, role=role), on_token=on_token, control=control)


@dataclass
class Generation:
    session_id: str
    message_id: str
    run_id: str
    question: str
    user_id: str
    role: str
    control: Control = field(default_factory=Control)
    parts: list[str] = field(default_factory=list)
    sequence: int = 0
    pending_input: str | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)  # tokens are published under it; cancelling takes it too: after a cancel, no token goes out


class ChatService:
    def __init__(self, engine, bus: ChatBus | None = None, runner=agent_runner, fixed_answer_delay: float | None = None):
        self.engine, self.bus, self.runner = engine, bus or default_bus, runner
        self.fixed_answer_delay = float(os.getenv("CHAT_FIXED_ANSWER_DELAY", "0.012")) if fixed_answer_delay is None else fixed_answer_delay
        self._generations: dict[str, Generation] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    # ------------------------------------------------------------------------------------------------ sessions
    def _session_lock(self, session_id: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(session_id, threading.RLock())

    def create_session(self, user_id: str, client_id: str | None = None) -> dict:
        return store.create_session(self.engine, user_id, client_id)

    def session_for(self, user_id: str, session_id: str) -> dict | None:
        """The session when it exists AND belongs to this user (somebody else's session is indistinguishable from a missing one)."""
        found = store.get_session(self.engine, session_id)
        return found if found and found["user_id"] == user_id else None

    def is_generating(self, session_id: str) -> bool:
        return session_id in self._generations

    def snapshot(self, session_id: str) -> dict:
        """The conversation as it is NOW, for a connection that attaches (or re-attaches): the stored history, with the text generated so far for a message that is still streaming and the
        `token_sequence` it reached, so the client continues from the next token and ignores any it already has."""
        found = store.get_session(self.engine, session_id) or {}
        messages = store.messages_of(self.engine, session_id)
        generation = self._generations.get(session_id)
        for message in messages:
            message["token_sequence"] = message.pop("token_count")
            if generation is not None and message["message_id"] == generation.message_id:
                with generation.lock:
                    message["text"], message["token_sequence"] = "".join(generation.parts), generation.sequence
        return {"session_id": session_id, "agent_id": found.get("agent_id", protocol.AGENT_ID), "status": found.get("status", "active"), "messages": messages}

    def close(self, session_id: str) -> None:
        with self._session_lock(session_id):
            generation = self._generations.get(session_id)
            if generation is not None:
                with generation.lock:
                    generation.pending_input = None
                    generation.control.cancel()
            store.close_session(self.engine, session_id)

    # ------------------------------------------------------------------------------------------------ turns
    def user_message(self, session_id: str, user_id: str, role: str, text: str) -> dict:
        """A new user turn. One generation at a time per session: while the agent is answering, the way to say something new is `interrupt`."""
        with self._session_lock(session_id):
            found = store.get_session(self.engine, session_id)
            if found is None or found["status"] == "closed":
                raise ChatError("session_closed", "This conversation is closed. Start a new one.")
            if session_id in self._generations:
                raise ChatError("generation_in_progress", "The agent is still answering. Interrupt it to say something new.")
            return self._start_turn(session_id, user_id, role, text)

    def interrupt(self, session_id: str, user_id: str, role: str, new_input: str) -> str:
        """Aborts the running generation; a non-empty `new_input` then starts a new turn. Returns what happened: `interrupting` (the abort was requested) or `started` (nothing was running, so it is just the next turn)."""
        with self._session_lock(session_id):
            found = store.get_session(self.engine, session_id)
            if found is None or found["status"] == "closed":
                raise ChatError("session_closed", "This conversation is closed. Start a new one.")
            generation = self._generations.get(session_id)
            if generation is None:
                if not new_input:
                    raise ChatError("no_active_generation", "The agent is not answering right now.")
                self._start_turn(session_id, user_id, role, new_input)
                return "started"
            with generation.lock:
                generation.pending_input = new_input or None
                generation.user_id, generation.role = user_id, role
                generation.control.cancel()  # from here no token of this generation is published
            return "interrupting"

    def _start_turn(self, session_id: str, user_id: str, role: str, text: str) -> dict:
        """Stores the user's message, opens the assistant message and starts the generation. The caller holds the session lock."""
        user = store.add_message(self.engine, session_id, "user", text)
        store.set_session_status(self.engine, session_id, "active")
        self.bus.publish(session_id, protocol.USER_MESSAGE, {"session_id": session_id, "message_id": user["message_id"], "text": text})
        run_id = str(uuid.uuid4())
        assistant = store.add_message(self.engine, session_id, "assistant", "", status="streaming", run_id=run_id)
        generation = Generation(session_id, assistant["message_id"], run_id, text, user_id, role)
        self._generations[session_id] = generation
        self.bus.publish(session_id, protocol.GENERATION_STARTED, {"session_id": session_id, "message_id": assistant["message_id"]})
        threading.Thread(target=self._run, args=(generation,), name=f"chat-{session_id}", daemon=True).start()
        return user

    # ------------------------------------------------------------------------------------------------ the generation
    def _on_token(self, generation: Generation, token: str) -> None:
        with generation.lock:
            if generation.control.cancelled:
                return  # an aborted generation produces nothing more
            generation.sequence += 1
            generation.parts.append(token)
            self.bus.publish(generation.session_id, protocol.TOKEN_CHUNK, {"session_id": generation.session_id, "message_id": generation.message_id, "token": token, "sequence": generation.sequence})
            if generation.sequence % FLUSH_EVERY == 0:
                store.update_message(self.engine, generation.message_id, text="".join(generation.parts), token_count=generation.sequence)

    def _run(self, generation: Generation) -> None:
        def on_token(token: str) -> None:
            self._on_token(generation, token)

        try:
            result = self.runner(generation.question, run_id=generation.run_id, user_id=generation.user_id, role=generation.role, on_token=on_token, control=generation.control)
            if not generation.control.cancelled and generation.sequence == 0:
                self._emit_fixed_answer(generation, result.answer)  # a guardrail's refusal or a fixed message: not from the model, but it reaches the user the same way
        except GenerationCancelled:
            pass
        except Exception:  # noqa: BLE001 - the agent failed: the message says so, the conversation goes on
            logger.exception("chat generation %s failed", generation.run_id)
            self._finish(generation, "failed")
            return
        if generation.control.cancelled:
            self._finish(generation, "interrupted")
        else:
            self._finish(generation, "completed", result)

    def _emit_fixed_answer(self, generation: Generation, text: str) -> None:
        for piece in re.findall(r"\S+\s*", text):
            if generation.control.cancelled:
                return
            self._on_token(generation, piece)
            if self.fixed_answer_delay:
                time.sleep(self.fixed_answer_delay)

    def _finish(self, generation: Generation, how: str, result=None) -> None:
        session_id, message_id = generation.session_id, generation.message_id
        with generation.lock:
            partial = "".join(generation.parts)
            produced = generation.sequence
        with self._session_lock(session_id):
            if how == "completed":
                store.update_message(self.engine, message_id, text=result.answer, status="completed", token_count=produced)
                self._generations.pop(session_id, None)
                self.bus.publish(session_id, protocol.GENERATION_COMPLETED, {"session_id": session_id, "message_id": message_id, "text": result.answer, "outcome": result.outcome})
                return
            if how == "failed":
                store.update_message(self.engine, message_id, text=partial, status="failed", token_count=produced)
                self._generations.pop(session_id, None)
                self.bus.publish(session_id, protocol.GENERATION_FAILED, {"session_id": session_id, "message_id": message_id, "error": FAILURE_TEXT, "reference": generation.run_id})
                return
            # interrupted: the partial message is KEPT, marked, and never overwritten by the next reply
            store.update_message(self.engine, message_id, text=partial, status="interrupted", token_count=produced)
            store.set_session_status(self.engine, session_id, "interrupted")
            self._generations.pop(session_id, None)
            self.bus.publish(session_id, protocol.GENERATION_INTERRUPTED, {"session_id": session_id, "message_id": message_id, "status": "interrupted", "text": partial})
            new_input = generation.pending_input
            if new_input and (found := store.get_session(self.engine, session_id)) and found["status"] != "closed":
                self._start_turn(session_id, generation.user_id, generation.role, new_input)  # the redirected reply is a NEW assistant message


_services: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_registry_lock = threading.Lock()


def service_for(engine) -> ChatService:
    """One chat service (generations, locks) per database engine, shared by every connection."""
    with _registry_lock:
        if engine not in _services:
            _services[engine] = ChatService(engine)
        return _services[engine]
