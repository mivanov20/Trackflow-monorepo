"""Streaming and cancelling one agent generation (WebSocket chat). The agent's logic and tools do not change: only HOW its answer reaches the user.

A run started with a `Control` streams: the `generate` node asks the model for the SAME answer with `stream: true`, every piece of text goes out through LangGraph's `custom` stream mode
(`get_stream_writer`) to whoever is iterating `graph.stream(...)`, and the run ends with the same final answer as a normal run (the output guard still checks it).

Cancelling is real, not cosmetic: `Control.cancel()` (1) sets the flag that the next piece of text, the next node boundary and the start of generation all check, so nothing further is produced
or requested, and (2) closes the connection to the model service from the calling thread, so a generation that is waiting for its next token stops too and the model stops generating.
LangGraph's `interrupt()` is a graph-level pause for human decisions; it is not what stops a stream, and it is not used here.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar
from typing import Callable

from langgraph.config import get_stream_writer


class GenerationCancelled(Exception):
    """The generation was cancelled on purpose (the user interrupted it)."""


class Control:
    """The cancellation handle of ONE generation. Thread-safe: the chat service cancels it from another thread than the one generating."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self._closers: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

    def register_close(self, close: Callable[[], None]) -> None:
        """A way to close the connection to the model. Runs at once if the generation was already cancelled."""
        with self._lock:
            if not self.cancelled:
                self._closers.append(close)
                return
        self._run(close)

    def cancel(self) -> None:
        with self._lock:
            self.event.set()
            closers, self._closers = self._closers, []
        for close in closers:
            self._run(close)

    @staticmethod
    def _run(close: Callable[[], None]) -> None:
        try:
            close()
        except Exception:  # noqa: BLE001 - the connection may be gone already; the flag is what matters
            pass

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise GenerationCancelled()


# The control of the run that is being executed: the runner sets it, the generate node reads it (a context variable follows LangGraph into its worker threads).
active_control: ContextVar[Control | None] = ContextVar("agent_generation_control", default=None)


class NodeSink:
    """What `rag.generate_answer` talks to while it streams: it checks the flag BEFORE it lets a piece of text out, and hands the way to close the model connection to the control."""

    def __init__(self, control: Control, write: Callable[[dict], None]) -> None:
        self.control, self.write = control, write

    def on_token(self, text: str) -> None:
        self.control.raise_if_cancelled()
        self.write({"token": text})

    def on_open(self, close: Callable[[], None]) -> None:
        self.control.register_close(close)


def sink_for_node() -> NodeSink | None:
    """The sink of the generate node, or None when the run was not started for streaming (the normal case: nothing changes)."""
    control = active_control.get()
    if control is None:
        return None
    control.raise_if_cancelled()  # cancelled while retrieving or calling tools: do not even ask the model
    return NodeSink(control, get_stream_writer())
