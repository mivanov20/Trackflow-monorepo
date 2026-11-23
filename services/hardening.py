"""Web hardening that is not tied to one router: sign-in throttling (OWASP A07) and security response headers / docs exposure (A05).

LOGIN THROTTLE. A password can be guessed by trying many. After `LOGIN_MAX_FAILURES` (5) failed sign-ins for the same (client address, email) inside
`LOGIN_WINDOW_SECONDS` (15 min) the next attempt gets `429` with `Retry-After`, even with the right password, until the window passes. An optional wider counter
per client address (`LOGIN_MAX_FAILURES_PER_IP`) stops one address from sweeping many accounts. Keying by address AND email means an attacker cannot lock a
victim's account out from another address. A successful sign-in clears that pair.

CLIENT ADDRESS. The counters trust the address the API sees. Behind a reverse proxy that is the proxy's, so set `TRUSTED_PROXY=true` (the proxy appends the real
client to `X-Forwarded-For`; only do it when nothing but the proxy can reach the API, as the firewall baseline guarantees). Without it, everyone behind the proxy shares one
address: brute force is still stopped, but an attacker could keep one account locked for the window (the trade-off of any lockout). For the same reason the wider per-address
cap is OFF unless `LOGIN_MAX_FAILURES_PER_IP` is set: on a shared address it would lock everybody out. In memory, one process (like the chat bus): behind several API processes
the counters would live in Redis, which the stack already has.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

logger = logging.getLogger("trackflow.security")
_lock = threading.Lock()
_failures: dict[str, deque[float]] = defaultdict(deque)


def _int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, default)))
    except ValueError:
        return default


def _window() -> int:
    return _int("LOGIN_WINDOW_SECONDS", 900)


def client_address(request: Request | None) -> str:
    """The caller's address. Behind a reverse proxy set `TRUSTED_PROXY=true` to use the last `X-Forwarded-For` hop (the one the proxy itself appended)."""
    if request is None:
        return "unknown"
    if os.getenv("TRUSTED_PROXY", "").lower() in ("1", "true", "yes"):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _keys(address: str, email: str) -> tuple[str, str]:
    return f"pair:{address}:{(email or '').strip().lower()}", f"ip:{address}"


def _recent(key: str, now: float) -> deque[float]:
    hits = _failures[key]
    while hits and hits[0] <= now - _window():
        hits.popleft()
    return hits


def check_login_allowed(request: Request | None, email: str) -> None:
    """Raises 429 when this client has failed too often. Call BEFORE checking the password."""
    now = time.monotonic()
    pair_key, ip_key = _keys(client_address(request), email)
    pair_limit = _int("LOGIN_MAX_FAILURES", 5)
    ip_limit = _int("LOGIN_MAX_FAILURES_PER_IP", 0) if os.getenv("LOGIN_MAX_FAILURES_PER_IP") else None
    with _lock:
        pair, ip = _recent(pair_key, now), _recent(ip_key, now)
        oldest = pair[0] if len(pair) >= pair_limit else ip[0] if ip_limit and len(ip) >= ip_limit else None
    if oldest is not None:
        wait = max(1, int(_window() - (now - oldest)) + 1)
        logger.warning("sign-in blocked: too many failures from %s (retry in %ds)", client_address(request), wait)  # the address, never the email or the password
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many failed sign-in attempts. Try again later.", headers={"Retry-After": str(wait)})


def record_login_failure(request: Request | None, email: str) -> None:
    now = time.monotonic()
    with _lock:
        for key in _keys(client_address(request), email):
            _recent(key, now).append(now)
    logger.info("sign-in failed from %s", client_address(request))


def record_login_success(request: Request | None, email: str) -> None:
    with _lock:
        _failures.pop(_keys(client_address(request), email)[0], None)


def reset_login_throttle() -> None:
    """For tests."""
    with _lock:
        _failures.clear()


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",  # the API serves JSON: nothing in it should ever load or be framed
}


def is_production() -> bool:
    return os.getenv("APP_ENV", "").strip().lower() in ("production", "prod")


def docs_urls() -> dict[str, str | None]:
    """The interactive API docs describe every route to an attacker: off in production (`APP_ENV=production`), on in development."""
    return {"docs_url": None, "redoc_url": None, "openapi_url": None} if is_production() else {}


async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/docs", "/redoc")):  # Swagger UI needs scripts and styles (development only)
        headers = {k: v for k, v in SECURITY_HEADERS.items() if k != "Content-Security-Policy"}
    else:
        headers = dict(SECURITY_HEADERS)
    for name, value in headers.items():
        response.headers.setdefault(name, value)
    if request.url.path.startswith("/auth/"):
        response.headers.setdefault("Cache-Control", "no-store")  # tokens and account data are never cached by a browser or proxy
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response
