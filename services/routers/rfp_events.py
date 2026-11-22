"""`GET /rfp/events`: the Server-Sent Events stream of RFP notifications (Real Time Systems, part 1). No model or agent is involved: this is a communication layer.

    event: rfp_ticket_created
    data: {"ticket_id": "...", "rfp_id": "...", "client_name": "...", "client_country": "US", "services_requested": [...], "departments_needed": [...], "status": "analyzing", "created_at": "...Z"}

 * Same JWT as the rest of the backoffice API (`Authorization: Bearer ...`): without a valid token there is no stream (401), so a client cannot read events it is not allowed to see.
   A browser's bare `EventSource` cannot send that header, which is why the dashboard reads the stream with `fetch`.
 * Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`, `Connection: keep-alive`, `X-Accel-Buffering: no` (a reverse proxy must not buffer it).
 * Keep-alive: a comment frame (`: keep-alive`) every `RFP_SSE_KEEPALIVE_SECONDS` (15) so proxies and the client's watchdog see the connection is alive.
 * Recovery: every event has an `id`; a client that reconnects with `Last-Event-ID` receives what it missed from a bounded replay buffer (`services/events.py`).
 * The stream closes by itself after `RFP_SSE_MAX_SECONDS` (900): the token is checked again when the client reconnects, so a stream can never outlive a session by long.
"""

import asyncio
import logging
import os

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from ..accounts import User
from ..auth import get_current_user
from ..events import bus, frame

logger = logging.getLogger("trackflow.events")
router = APIRouter(prefix="/rfp", tags=["rfp"])

RETRY_MS = 3000


def keepalive_seconds() -> float:
    try:
        return max(0.05, float(os.getenv("RFP_SSE_KEEPALIVE_SECONDS", "15")))
    except ValueError:
        return 15.0


def max_stream_seconds() -> float:
    try:
        return max(0.5, float(os.getenv("RFP_SSE_MAX_SECONDS", "900")))
    except ValueError:
        return 900.0


@router.get("/events")
async def rfp_events(user: User = Depends(get_current_user), last_event_id: str | None = Header(default=None, alias="Last-Event-ID")) -> StreamingResponse:
    """The stream of `rfp_ticket_created` events for signed-in users."""
    interval, lifetime = keepalive_seconds(), max_stream_seconds()

    async def stream():
        loop = asyncio.get_running_loop()
        deadline = loop.time() + lifetime
        subscription = bus.subscribe(last_event_id)  # inside the generator: a stream that never starts never leaves a subscriber behind
        try:
            yield f"retry: {RETRY_MS}\n: connected\n\n"
            for item in subscription.backlog:  # what the client missed while it was disconnected
                yield frame(item)
            while not subscription.overflowed:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(subscription.queue.get(), timeout=min(interval, remaining))
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield frame(item)
        finally:
            bus.unsubscribe(subscription)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
