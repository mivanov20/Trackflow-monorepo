"""Publish/subscribe for the chat: ONE CHANNEL PER SESSION, `chat.<session_id>`.

The producer (the agent generating tokens, in a worker thread) publishes to the channel of its session and knows nothing about WebSockets; every WebSocket connection attached to that session
subscribes to the channel and forwards what it receives. Two tabs of the same chat, or a reconnection, are just more subscribers; a generation that outlives its connection keeps publishing.

In-memory, one process (the brief accepts it): `publish` is thread-safe and hands each message to the subscriber's own event loop with `call_soon_threadsafe`. A Redis backplane would replace this class
and nothing else: the producer calls `publish(session_id, event, data)` and the consumer reads from a `Subscription`.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field

QUEUE_LIMIT = 1000  # a consumer that falls this far behind is dropped (it reconnects and rehydrates) instead of making the producer wait or the memory grow


def channel_name(session_id: str) -> str:
    return f"chat.{session_id}"


@dataclass
class Subscription:
    channel: str
    queue: "asyncio.Queue[dict]"
    loop: asyncio.AbstractEventLoop
    overflowed: bool = False
    closed: bool = field(default=False)


class ChatBus:
    def __init__(self) -> None:
        self._channels: dict[str, list[Subscription]] = {}
        self._lock = threading.Lock()

    def subscribe(self, session_id: str) -> Subscription:
        """Attach to a session's channel (call it from the consumer's own event loop)."""
        name = channel_name(session_id)
        subscription = Subscription(name, asyncio.Queue(maxsize=QUEUE_LIMIT), asyncio.get_running_loop())
        with self._lock:
            self._channels.setdefault(name, []).append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        subscription.closed = True
        with self._lock:
            members = self._channels.get(subscription.channel, [])
            if subscription in members:
                members.remove(subscription)
            if not members:
                self._channels.pop(subscription.channel, None)

    def publish(self, session_id: str, event: str, data: dict) -> int:
        """Publish `{"event", "data"}` to every subscriber of the session's channel. Safe from any thread. Returns how many subscribers it was handed to."""
        message = {"event": event, "data": data}
        with self._lock:
            members = list(self._channels.get(channel_name(session_id), []))
        for subscription in members:
            try:
                subscription.loop.call_soon_threadsafe(self._deliver, subscription, message)
            except RuntimeError:  # the consumer's loop is closed: the connection is gone
                self.unsubscribe(subscription)
        return len(members)

    def _deliver(self, subscription: Subscription, message: dict) -> None:
        if subscription.closed:
            return
        try:
            subscription.queue.put_nowait(message)
        except asyncio.QueueFull:
            subscription.overflowed = True
            self.unsubscribe(subscription)

    def subscriber_count(self, session_id: str) -> int:
        with self._lock:
            return len(self._channels.get(channel_name(session_id), []))


bus = ChatBus()
