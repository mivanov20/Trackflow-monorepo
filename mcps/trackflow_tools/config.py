"""Settings of the MCP server, read from the environment. Nothing secret has a default and nothing is hardcoded.

| Variable | Meaning |
|---|---|
| `MCP_PUBLIC_URL` | where clients reach this server, e.g. `https://<codespace>-8765.app.github.dev/mcp`. It is the token `aud` (audience) clients must ask for, and what the Protected Resource Metadata announces |
| `OAUTH_ISSUER` | the issuer URL of the OAuth 2.1 / OIDC provider whose tokens are accepted. The signing keys are discovered from its metadata |
| `INCIDENTS_API_URL` | base URL of the TrackFlow API (incident manager and inventory), default `http://localhost:8000` |
| `API_SERVICE_EMAIL`, `API_SERVICE_PASSWORD` | the service account this server signs in with to call the API (the API's own login), or `API_SERVICE_TOKEN` for a ready token |
| `MCP_TOKEN_AUDIENCE` | the `aud` the tokens must carry; defaults to `MCP_PUBLIC_URL` (clients ask for it with the `resource` parameter, RFC 8707) |
| `MCP_ALLOWED_ORIGINS` | comma-separated browser origins allowed to call the server (CORS and Origin check); default is MCP Playground |
| `MCP_HOST`, `MCP_PORT` | where to listen, default `127.0.0.1:8765` |
| `MCP_UPSTREAM_TIMEOUT_SECONDS` | how long to wait for the API, default 5 |
"""

from __future__ import annotations

import os
from dataclasses import dataclass

SCOPES = {
    "tickets:read": "read the status and details of a ticket in the Incidents Manager",
    "tickets:write": "create tickets and move them along their lifecycle in the Incidents Manager",
    "inventory:read": "read products and stock from the inventory manager (never change it)",
}


class ConfigError(ValueError):
    """A required setting is missing or invalid. The process exits with code 2."""


@dataclass(frozen=True)
class Settings:
    public_url: str
    issuer: str
    api_url: str
    service_email: str | None
    service_password: str | None
    service_token: str | None
    host: str
    port: int
    upstream_timeout: float
    audience: str
    allowed_origins: tuple[str, ...]

    @property
    def resource_origin(self) -> str:
        parts = self.public_url.split("/", 3)
        return "/".join(parts[:3])

    @property
    def resource_path(self) -> str:
        return "/" + self.public_url.split("/", 3)[3] if self.public_url.count("/") >= 3 else "/"


def load_settings(env=None) -> Settings:
    env = os.environ if env is None else env
    missing = [name for name in ("MCP_PUBLIC_URL", "OAUTH_ISSUER") if not env.get(name)]
    if missing:
        raise ConfigError(f"missing required setting(s): {', '.join(missing)}")
    has_login = env.get("API_SERVICE_EMAIL") and env.get("API_SERVICE_PASSWORD")
    if not has_login and not env.get("API_SERVICE_TOKEN"):
        raise ConfigError("the server needs an identity to call the TrackFlow API: set API_SERVICE_EMAIL and API_SERVICE_PASSWORD (or API_SERVICE_TOKEN)")
    public_url = env["MCP_PUBLIC_URL"].rstrip("/")
    if not public_url.startswith(("http://", "https://")) or public_url.count("/") < 3:
        raise ConfigError("MCP_PUBLIC_URL must be the full URL of the MCP endpoint, including its path (for example http://localhost:8765/mcp)")
    try:
        return Settings(
            public_url=public_url,
            issuer=env["OAUTH_ISSUER"].rstrip("/"),
            api_url=env.get("INCIDENTS_API_URL", "http://localhost:8000").rstrip("/"),
            service_email=env.get("API_SERVICE_EMAIL"),
            service_password=env.get("API_SERVICE_PASSWORD"),
            service_token=env.get("API_SERVICE_TOKEN"),
            host=env.get("MCP_HOST", "127.0.0.1"),
            port=int(env.get("MCP_PORT", "8765")),
            upstream_timeout=float(env.get("MCP_UPSTREAM_TIMEOUT_SECONDS", "5")),
            audience=env.get("MCP_TOKEN_AUDIENCE", public_url).rstrip("/"),
            allowed_origins=tuple(o.strip() for o in env.get("MCP_ALLOWED_ORIGINS", "https://www.mcpplayground.tech").split(",") if o.strip()),
        )
    except ValueError as error:
        raise ConfigError(f"a numeric setting is not a number: {error}") from None
