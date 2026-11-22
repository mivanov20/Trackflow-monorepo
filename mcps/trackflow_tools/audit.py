"""Audit log: one line per tool invocation, saying who called which tool and how it ended.

`trackflow.mcp.audit`, one JSON object per line: `event`, `tool`, `client_id` (the OAuth client the token was issued to), `subject`, `scopes`,
`outcome` (`ok` or `error`), `code` (the error code, when it failed), `duration_ms` and `arguments`. The arguments are logged as sent, but
long free text (a ticket description) is shortened, and the access token is never logged.
"""

from __future__ import annotations

import functools
import json
import logging
import time
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from .errors import ToolFailure

logger = logging.getLogger("trackflow.mcp.audit")
TEXT_LIMIT = 80


def shorten(arguments: dict[str, Any]) -> dict[str, Any]:
    return {key: (value[:TEXT_LIMIT] + "…" if isinstance(value, str) and len(value) > TEXT_LIMIT else value) for key, value in arguments.items() if value is not None}  # only what the client sent


def record(tool: str, caller: dict, arguments: dict, started: float, outcome: str, code: str | None = None) -> None:
    logger.info(json.dumps({"event": "tool_call", "tool": tool, "client_id": caller.get("client_id"), "subject": caller.get("subject"), "scopes": caller.get("scopes"), "outcome": outcome, "code": code, "duration_ms": int((time.perf_counter() - started) * 1000), "arguments": shorten(arguments)}, default=str))


def audited(tool: str, caller_of):
    """Wraps a tool so every call, successful or not, writes its audit line. `caller_of()` returns who is calling (from the validated token)."""

    def decorate(function):
        @functools.wraps(function)
        async def wrapper(**arguments):
            started, caller = time.perf_counter(), caller_of()
            try:
                result = await function(**arguments)
            except ToolFailure as failure:
                record(tool, caller, arguments, started, "error", failure.code)
                raise
            except ToolError:
                record(tool, caller, arguments, started, "error", "TOOL_ERROR")
                raise
            except Exception:  # noqa: BLE001 - an unexpected bug: logged, and the client gets a generic error from the SDK
                record(tool, caller, arguments, started, "error", "INTERNAL_ERROR")
                raise
            record(tool, caller, arguments, started, "ok")
            return result

        return wrapper

    return decorate
