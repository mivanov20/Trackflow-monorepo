"""The agent as an MCP client: how it reaches the Incidents Manager and the inventory now, through the TrackFlow MCP server.

This is the ONLY path from the agent to those systems. The agent used to read them in-process; that code is gone, so there are not two ways in.
The tools come from the server's own discovery (`langchain-mcp-adapters` loads them from `tools/list`), and every call carries an OAuth access token:

* the agent is an OAuth client of the same provider the MCP server trusts. It gets a token with the `client_credentials` grant (RFC 6749 4.4), for the
  MCP server as audience (`resource`, RFC 8707), and keeps it until shortly before it expires.
* LEAST PRIVILEGE: the agent only asks for `tickets:read inventory:read` (`AGENT_MCP_SCOPES`). Even if its code were changed to call `create_ticket`, the server
  would answer `[INSUFFICIENT_SCOPE]`: the agent's credential cannot write.

Settings (environment, nothing hardcoded): `AGENT_MCP_URL` (the MCP endpoint), `AGENT_OAUTH_ISSUER` (the provider; its token endpoint is discovered from its
metadata), `AGENT_OAUTH_CLIENT_ID`, `AGENT_OAUTH_CLIENT_SECRET`, `AGENT_MCP_SCOPES` (default `tickets:read inventory:read`).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any

import httpx
from langchain_core.tools import ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient

from . import tool_policy

DEFAULT_SCOPES = "tickets:read inventory:read"
TOKEN_REFRESH_MARGIN_SECONDS = 30
CODE_IN_MESSAGE = re.compile(r"\[([A-Z_]+)\]\s*(.*)", re.DOTALL)


class McpError(Exception):
    """A call through the MCP server failed. `code` is the server's error code, or one of ours for failures before the server answered."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.detail = code, message


@dataclass(frozen=True)
class McpSettings:
    url: str
    issuer: str
    client_id: str
    client_secret: str
    scopes: str = DEFAULT_SCOPES


def load_settings(env=None) -> McpSettings:
    env = os.environ if env is None else env
    missing = [n for n in ("AGENT_MCP_URL", "AGENT_OAUTH_ISSUER", "AGENT_OAUTH_CLIENT_ID", "AGENT_OAUTH_CLIENT_SECRET") if not env.get(n)]
    if missing:
        raise McpError("NOT_CONFIGURED", f"The MCP client is not configured: set {', '.join(missing)}.")
    return McpSettings(env["AGENT_MCP_URL"], env["AGENT_OAUTH_ISSUER"].rstrip("/"), env["AGENT_OAUTH_CLIENT_ID"], env["AGENT_OAUTH_CLIENT_SECRET"], env.get("AGENT_MCP_SCOPES", DEFAULT_SCOPES))


class TokenProvider:
    """OAuth client-credentials access tokens for the MCP server, cached until just before they expire."""

    def __init__(self, settings: McpSettings):
        self._settings, self._token, self._expires_at, self._token_endpoint, self._lock = settings, None, 0.0, None, Lock()

    def _discover_token_endpoint(self) -> str:
        if self._token_endpoint is None:
            for path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
                response = httpx.get(f"{self._settings.issuer}{path}", timeout=5)
                if response.status_code == 200:
                    self._token_endpoint = response.json()["token_endpoint"]
                    break
            else:
                raise McpError("AUTH_PROVIDER_UNAVAILABLE", "The OAuth provider's metadata could not be read.")
        return self._token_endpoint

    def token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
                return self._token
            try:
                response = httpx.post(
                    self._discover_token_endpoint(),
                    data={"grant_type": "client_credentials", "scope": self._settings.scopes, "resource": self._settings.url},
                    auth=(self._settings.client_id, self._settings.client_secret),
                    timeout=5,
                )
            except httpx.HTTPError:
                raise McpError("AUTH_PROVIDER_UNAVAILABLE", "The OAuth provider did not answer.") from None
            if response.status_code != 200:
                raise McpError("AUTH_FAILED", f"The OAuth provider refused the agent's credentials ({response.status_code}).")
            body = response.json()
            self._token, self._expires_at = body["access_token"], time.time() + float(body.get("expires_in", 300))
            return self._token


_providers: dict[McpSettings, TokenProvider] = {}


def _provider(settings: McpSettings) -> TokenProvider:
    return _providers.setdefault(settings, TokenProvider(settings))


def _structured(result: Any) -> Any:
    """The structured output of a tool result, whichever shape the adapter hands back."""
    artifact = getattr(result, "artifact", None)
    if isinstance(artifact, dict) and artifact.get("structured_content") is not None:
        return artifact["structured_content"]
    content = getattr(result, "content", result)
    if isinstance(content, list):
        content = "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        raise McpError("BAD_RESPONSE", "The MCP server answered with something that is not the expected data.") from None


async def acall_tool(name: str, arguments: dict, *, settings: McpSettings | None = None) -> Any:
    """Calls one tool of the MCP server, authenticated, and returns its structured result. Raises McpError."""
    try:
        tool_policy.validate_call(name, arguments)  # the allow-list and argument check: nothing else is ever sent (services/agent/tool_policy.py)
    except tool_policy.ToolCallRefused as refused:
        raise McpError(refused.code, str(refused)) from None
    settings = settings or load_settings()
    token = await asyncio.to_thread(_provider(settings).token)
    client = MultiServerMCPClient({"trackflow": {"transport": "streamable_http", "url": settings.url, "headers": {"Authorization": f"Bearer {token}"}}})
    try:
        tools = {tool.name: tool for tool in await client.get_tools()}
        if name not in tools:
            raise McpError("TOOL_NOT_OFFERED", f"The MCP server does not offer a tool called '{name}' to this client.")
        result = await tools[name].ainvoke({"type": "tool_call", "name": name, "args": arguments, "id": f"call-{name}"})
    except McpError:
        raise
    except ToolException as failure:  # the server answered with isError: its text starts with [CODE]
        match = CODE_IN_MESSAGE.search(str(failure))
        raise McpError(match.group(1) if match else "TOOL_ERROR", (match.group(2) if match else str(failure)).strip()) from None
    except Exception as error:  # noqa: BLE001 - 401/403 from the transport, connection refused, a closed session...
        raise McpError("MCP_UNAVAILABLE", f"The MCP server could not be used ({type(error).__name__}).") from None
    if getattr(result, "status", None) == "error":  # the server answered with isError: LangChain hands it back as a message, its text starts with [CODE]
        content = getattr(result, "content", "")
        text = content if isinstance(content, str) else "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
        match = CODE_IN_MESSAGE.search(text)
        raise McpError(match.group(1) if match else "TOOL_ERROR", (match.group(2) if match else text).strip())
    return _structured(result)


def call_tool(name: str, arguments: dict, *, settings: McpSettings | None = None, timeout: float = 4.0) -> Any:
    """Synchronous wrapper for the graph's sync nodes. It runs in a worker thread (see `tools.call_with_timeout`), where no event loop is running.

    `timeout` is an explicit number of seconds for the whole call (token, discovery, tool call): after it the call is cancelled and `McpError("MCP_TIMEOUT")` is raised.
    """

    async def bounded():
        return await asyncio.wait_for(acall_tool(name, arguments, settings=settings), timeout)

    try:
        return asyncio.run(bounded())
    except asyncio.TimeoutError:
        raise McpError("MCP_TIMEOUT", f"The MCP server did not answer within {timeout:g} seconds.") from None
