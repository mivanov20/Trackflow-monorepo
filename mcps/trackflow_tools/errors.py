"""The errors of the TrackFlow MCP server, each with its own code, so a client can tell a bad token from a missing permission from a bad argument.

Where they show up:

| Layer | Failure | HTTP | Code (in the response) |
|---|---|---|---|
| transport (MCP Auth) | no `Authorization: Bearer` header | 401 | `missing_auth_header` |
| transport (MCP Auth) | malformed header / empty token | 401 | `invalid_auth_header_format` / `missing_bearer_token` |
| transport (MCP Auth) | signature, expiry or format of the JWT is wrong | 401 | `invalid_token` |
| transport (MCP Auth) | token from another issuer / for another server | 401 | `invalid_issuer` / `invalid_audience` |
| tool | the token is valid but lacks the scope this tool needs | 200 + `isError` | `INSUFFICIENT_SCOPE` |
| tool | the argument is not valid (field, value or ticket lifecycle) | 200 + `isError` | `VALIDATION_ERROR` |
| tool | the ticket / product does not exist | 200 + `isError` | `NOT_FOUND` |
| tool | an attempt to write through the read-only inventory tool | 200 + `isError` | `INVENTORY_READ_ONLY` |
| tool | the Incidents Manager / inventory did not answer or failed | 200 + `isError` | `UPSTREAM_UNAVAILABLE` |
| process | bad or missing configuration at start | exit code 2 | (message on stderr) |

A tool failure is reported the MCP way (a result with `isError: true`), and its text starts with `[CODE]`.
"""

from __future__ import annotations

from mcp.server.fastmcp.exceptions import ToolError

INSUFFICIENT_SCOPE = "INSUFFICIENT_SCOPE"
VALIDATION_ERROR = "VALIDATION_ERROR"
NOT_FOUND = "NOT_FOUND"
INVENTORY_READ_ONLY = "INVENTORY_READ_ONLY"
UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"

CODES = (INSUFFICIENT_SCOPE, VALIDATION_ERROR, NOT_FOUND, INVENTORY_READ_ONLY, UPSTREAM_UNAVAILABLE)

EXIT_CONFIG_ERROR = 2  # the server did not start: a required setting is missing or invalid


class ToolFailure(ToolError):
    """A failure of a tool, with a stable code. The text a client reads is `[CODE] message`."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.detail = message
