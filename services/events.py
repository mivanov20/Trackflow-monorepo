"""The in-process event bus behind the SSE stream (Real Time Systems, part 1).

`publish` is called from any thread (the intake runs in a background thread); every connected stream receives the event through its own queue, delivered to the stream's event loop with
`call_soon_threadsafe`. Each event gets an id `<boot>-<n>` and stays in a bounded ring buffer so a client that reconnects with `Last-Event-ID` is sent what it missed.

Honest limits: the buffer is in memory, so a restart empties it (the boot token in the id tells a client from a previous run apart: no replay is attempted for it, and the dashboard refetches the ticket
list on every reconnect anyway), and the bus is per process (one API process is supported; several would need a shared broker such as Redis).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("trackflow.events")

DEFAULT_BUFFER = 500
QUEUE_LIMIT = 1000  # a client that cannot keep up this far is disconnected (it reconnects and recovers), instead of growing without bound


@dataclass(frozen=True)
class StreamEvent:
    id: str
    seq: int
    event: str
    data: dict
    at: str


@dataclass
class Subscription:
    queue: "asyncio.Queue[StreamEvent]"
    loop: asyncio.AbstractEventLoop
    backlog: list[StreamEvent] = field(default_factory=list)
    overflowed: bool = False


class EventBus:
    def __init__(self, buffer_size: int | None = None):
        size = buffer_size or int(os.getenv("RFP_EVENT_BUFFER", DEFAULT_BUFFER))
        self.boot = uuid.uuid4().hex[:8]
        self._seq = 0
        self._buffer: deque[StreamEvent] = deque(maxlen=max(1, size))
        self._subscribers: list[Subscription] = []
        self._lock = threading.Lock()

    def publish(self, event: str, data: dict) -> str:
        """Stores the event and hands it to every connected stream. Safe to call from any thread. Returns the event id."""
        with self._lock:
            self._seq += 1
            item = StreamEvent(f"{self.boot}-{self._seq}", self._seq, event, data, datetime.now(timezone.utc).isoformat())
            self._buffer.append(item)
            subscribers = list(self._subscribers)
        for subscription in subscribers:
            try:
                subscription.loop.call_soon_threadsafe(self._deliver, subscription, item)
            except RuntimeError:  # the stream's loop is closed: the connection is gone
                self._remove(subscription)
        return item.id

    def _deliver(self, subscription: Subscription, item: StreamEvent) -> None:
        try:
            subscription.queue.put_nowait(item)
        except asyncio.QueueFull:
            subscription.overflowed = True
            self._remove(subscription)

    def _remove(self, subscription: Subscription) -> None:
        with self._lock:
            if subscription in self._subscribers:
                self._subscribers.remove(subscription)

    def missed_since(self, last_event_id: str | None) -> list[StreamEvent]:
        """The events after `last_event_id` still in the buffer. An id from another run (other boot token) or a malformed one gets nothing: the client recovers by refetching."""
        if not last_event_id:
            return []
        boot, _, number = last_event_id.partition("-")
        if boot != self.boot or not number.isdigit():
            return []
        return [item for item in self._buffer if item.seq > int(number)]

    def subscribe(self, last_event_id: str | None = None) -> Subscription:
        """Registers a stream (call it from the stream's own event loop). The backlog and the registration happen under one lock, so no event is missed or repeated between them."""
        subscription = Subscription(asyncio.Queue(maxsize=QUEUE_LIMIT), asyncio.get_running_loop())
        with self._lock:
            subscription.backlog = self.missed_since(last_event_id)
            self._subscribers.append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self._remove(subscription)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


bus = EventBus()


def frame(item: StreamEvent) -> str:
    """One SSE frame: the id, the NAMED event and a single-line JSON body (the ticket fields only, no envelope)."""
    import json

    return f"id: {item.id}\nevent: {item.event}\ndata: {json.dumps(item.data, separators=(',', ':'), ensure_ascii=False)}\n\n"
