"""The validation layer between what the agent decides to call and what is executed.

The agent's plan is not model output (the planner is a pure function over the question, `routing.py`), but the arguments still come from user text ("ticket 482", "stock of
hoodies"), and a future model-based planner would produce them directly. Either way NOTHING reaches the MCP client without passing `validate_call`:

* the tool must be in the allow-list below. The agent is read-only: `create_ticket` and `update_ticket_status` are not in it, so even a plan that asked for them is refused
  here, before the network, and (second line) the agent's token does not carry the scope for them either;
* the arguments must be exactly the expected ones, with the expected types and bounds: no extra keys (a smuggled `status`, `confirm`, `operation: "update"`), no booleans
  where an integer is expected, a bounded string for a search, only the read-only `search` operation of the inventory tool;
* a string argument carrying control characters, or what looks like an instruction or a query fragment, is refused: tools take identifiers and short names, never sentences.
"""

from __future__ import annotations

import re
from typing import Any

ALLOWED_TOOLS = {"get_ticket", "query_inventory"}
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_QUERY_FRAGMENT = re.compile(r"(?i)(;|--|/\*|\*/|\b(?:drop|delete|insert|update|union|select)\b\s+\w|\$\{|`|<\s*script|\bignore\b.{0,20}\binstructions?\b)")


class ToolCallRefused(ValueError):
    """The call is not allowed to run. `code` is stable, for the trace and the tests."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_call(name: str, arguments: dict[str, Any]) -> None:
    if name not in ALLOWED_TOOLS:
        raise ToolCallRefused("TOOL_NOT_ALLOWED", f"The agent may not call '{name}': it is not one of its read-only tools.")
    if not isinstance(arguments, dict):
        raise ToolCallRefused("INVALID_ARGUMENTS", "Tool arguments must be an object.")
    if name == "get_ticket":
        _exact(arguments, {"ticket_id"})
        ticket_id = arguments["ticket_id"]
        if not isinstance(ticket_id, int) or isinstance(ticket_id, bool) or not 0 < ticket_id < 10**9:
            raise ToolCallRefused("INVALID_ARGUMENTS", "ticket_id must be a positive integer.")
    else:
        if arguments.get("operation") != "search":  # first, so a write attempt is named for what it is, not as a missing argument
            raise ToolCallRefused("TOOL_NOT_ALLOWED", "The agent may only use the read-only 'search' operation of the inventory tool.")
        _exact(arguments, {"operation", "product_query"})
        query = arguments["product_query"]
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 120:
            raise ToolCallRefused("INVALID_ARGUMENTS", "product_query must be a short text (2 to 120 characters).")
        if _CONTROL.search(query) or _QUERY_FRAGMENT.search(query):
            raise ToolCallRefused("INVALID_ARGUMENTS", "product_query looks like a command or a query fragment, not a product name.")


def _exact(arguments: dict[str, Any], expected: set[str]) -> None:
    if set(arguments) != expected:
        extra, missing = sorted(set(arguments) - expected), sorted(expected - set(arguments))
        raise ToolCallRefused("INVALID_ARGUMENTS", f"Unexpected or missing arguments (extra: {extra or 'none'}, missing: {missing or 'none'}).")
