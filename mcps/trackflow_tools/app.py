"""The ASGI app of the TrackFlow MCP server: OAuth in front, the MCP endpoint behind.

TRANSPORT: Streamable HTTP, not stdio. stdio is for a server a single client starts as its own child process, on the same machine, and the
MCP authorization spec does not apply to it (it takes credentials from the environment). This server is meant to be reused by several clients (the
agent, other agents, other teams, a browser tool such as MCP Playground) over the network, and it protects company data: that needs HTTP, where every
request carries an OAuth bearer token that the server validates. It runs stateless (no session to keep or to hijack, each request is checked on its own)
and answers with plain JSON.

AUTH (MCP Auth, `mcpauth`, resource-server mode):
* `GET /.well-known/oauth-protected-resource[/mcp]` (RFC 9728, public): says which authorization server issues tokens for this resource and which scopes exist.
* `GET /.well-known/oauth-authorization-server` (public): the authorization server metadata MCP Auth holds, for clients that look for it here.
* everything else under `/`, the MCP endpoint included, goes through the MCP Auth bearer middleware: the JWT is verified against the provider's keys (JWKS), the
  issuer and the audience must match, and no valid token means no `tools/list` and no `tools/call`.
* `GET /health` is public and returns no company data.
A 401 carries `WWW-Authenticate: Bearer resource_metadata="..."`, which is how an MCP client finds the authorization server on its own.
"""

from __future__ import annotations

import contextlib
import logging

from mcpauth import MCPAuth
from mcpauth.config import AuthServerConfig, AuthServerType
from mcpauth.utils import fetch_server_config
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import SCOPES, Settings
from .server import build_server
from .upstream import IncidentsClient, InventoryClient

logger = logging.getLogger("trackflow.mcp")
PRM_PATH = "/.well-known/oauth-protected-resource"


class ChallengeMiddleware:
    """Adds `WWW-Authenticate: Bearer resource_metadata=...` to every 401, so a client can discover the authorization server (MCP authorization spec)."""

    def __init__(self, app: ASGIApp, metadata_url: str):
        self.app, self.challenge = app, f'Bearer resource_metadata="{metadata_url}"'

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_challenge(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] == 401:
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"www-authenticate"]
                headers.append((b"www-authenticate", self.challenge.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_challenge) if scope["type"] == "http" else await self.app(scope, receive, send)


def protected_resource_metadata(settings: Settings) -> dict:
    """RFC 9728 document: this server, who issues its tokens, and the scopes it understands."""
    return {
        "resource": settings.audience,
        "authorization_servers": [settings.issuer],
        "scopes_supported": list(SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_name": "TrackFlow company tools (tickets and inventory)",
        "resource_documentation": f"{settings.resource_origin}/health",
    }


def load_auth_server(settings: Settings) -> AuthServerConfig:
    """Discovers the provider's metadata (and so its signing keys) from the issuer. Fails at start if the issuer cannot be reached."""
    return fetch_server_config(settings.issuer, AuthServerType.OIDC)


def create_app(settings: Settings, *, auth_server: AuthServerConfig | None = None, transport=None) -> Starlette:
    """Builds the app. `auth_server` and `transport` are injectable for tests (a provider config; an ASGI transport to the TrackFlow API)."""
    mcp_auth = MCPAuth(server=auth_server or load_auth_server(settings))
    incidents, inventory = IncidentsClient(settings, transport), InventoryClient(settings, transport)
    server = build_server(settings, mcp_auth, incidents, inventory)
    mcp_http = server.streamable_http_app()
    bearer = mcp_auth.bearer_auth_middleware("jwt", audience=settings.audience)

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette):
        async with server.session_manager.run():
            yield
        await incidents.aclose()
        await inventory.aclose()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": "trackflow-tools"})

    async def resource_metadata(_: Request) -> JSONResponse:
        return JSONResponse(protected_resource_metadata(settings), headers={"Cache-Control": "public, max-age=300"})

    metadata_url = f"{settings.resource_origin}{PRM_PATH}{settings.resource_path if settings.resource_path != '/' else ''}"
    app = Starlette(
        routes=[
            Route("/health", health),
            Route(PRM_PATH, resource_metadata),
            Route(f"{PRM_PATH}{settings.resource_path}", resource_metadata),
            mcp_auth.metadata_route(),
            Mount("/", app=mcp_http, middleware=[Middleware(bearer)]),
        ],
        lifespan=lifespan,
        middleware=[
            Middleware(ChallengeMiddleware, metadata_url=metadata_url),
            Middleware(
                CORSMiddleware,  # a browser client (MCP Playground) preflights with OPTIONS before it can send its token
                allow_origins=list(settings.allowed_origins),
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=["authorization", "content-type", "mcp-protocol-version", "mcp-session-id", "accept"],
                expose_headers=["WWW-Authenticate", "Mcp-Session-Id"],
            ),
        ],
    )
    app.state.mcp_auth = mcp_auth
    return app
