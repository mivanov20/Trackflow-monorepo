"""Rate limiting for the endpoints that call a language model: a loop (a script, a stuck client, an attacker) must not turn into an unbounded bill.

Each call is counted per SIGNED-IN USER (the account, not the address: the address behind a proxy is shared) in two sliding windows, a short one against bursts and a long
one against a steady drip:

    RATE_LIMIT_PER_MINUTE   default 20     calls per user per 60 s
    RATE_LIMIT_PER_HOUR     default 200    calls per user per 3600 s
    RATE_LIMIT_ENABLED      default true   (only for emergencies and local load tests)

Over the limit the API answers `429 Too Many Requests` with `Retry-After` (seconds until a call is allowed again) and `X-RateLimit-Limit` / `X-RateLimit-Remaining`, BEFORE the
agent, the retrieval or the model are reached, so a refused call costs nothing. Every refusal is written to the guardrail log (`rate_limit`), where a burst shows up in
`/agent/guardrails/summary`. The WebSocket chat uses the same limiter per user message. In memory, one process (like the chat bus and the sign-in throttle): behind several
API processes the counters would live in Redis, which the stack already has.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Response, status

from .accounts import User
from .auth import get_current_user
from .agent.guardrails import events

logger = logging.getLogger("trackflow.security")
_lock = threading.Lock()
_calls: dict[str, deque[float]] = defaultdict(deque)
WINDOWS = (("minute", 60, "RATE_LIMIT_PER_MINUTE", 20), ("hour", 3600, "RATE_LIMIT_PER_HOUR", 200))


class RateLimited(Exception):
    def __init__(self, retry_after: int, limit: int, window: str):
        super().__init__(f"Too many requests ({limit} per {window}). Try again in {retry_after} s.")
        self.retry_after, self.limit, self.window = retry_after, limit, window


def enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _limit(env: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(env, default)))
    except ValueError:
        return default


def check(user_id: str, scope: str = "model") -> int:
    """Counts one call for `user_id` and returns how many calls are left in the tightest window. Raises `RateLimited` (without counting) when the call is not allowed."""
    if not enabled():
        return 10**9
    now = time.monotonic()
    key = f"{scope}:{user_id}"
    with _lock:
        hits = _calls[key]
        while hits and hits[0] <= now - WINDOWS[-1][1]:
            hits.popleft()
        remaining = 10**9
        for name, seconds, env, default in WINDOWS:
            limit = _limit(env, default)
            in_window = [t for t in hits if t > now - seconds]
            if len(in_window) >= limit:
                retry = max(1, int(seconds - (now - in_window[0])) + 1)
                raise RateLimited(retry, limit, name)
            remaining = min(remaining, limit - len(in_window) - 1)
        hits.append(now)
    return remaining


def reset() -> None:
    """For tests."""
    with _lock:
        _calls.clear()


def _refuse(error: RateLimited, user_id: str) -> HTTPException:
    events.record("rate_limit", "security", "blocked", f"over {error.limit} calls per {error.window}", context=events.Context(actor=user_id))
    logger.warning("rate limit: user %s over %d calls per %s", user_id, error.limit, error.window)
    return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error), headers={"Retry-After": str(error.retry_after), "X-RateLimit-Limit": str(error.limit), "X-RateLimit-Remaining": "0"})


def limit_model_calls(response: Response, user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency for an endpoint that calls a model: counts the call or answers 429."""
    try:
        remaining = check(user.id)
    except RateLimited as error:
        raise _refuse(error, user.id) from None
    if enabled():
        response.headers["X-RateLimit-Remaining"] = str(remaining)
    return user
