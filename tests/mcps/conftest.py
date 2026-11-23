"""A real stack for the MCP tests: the dev OAuth provider and the MCP server on real ports (threads), in front of the REAL TrackFlow API
(in-process over ASGI, with an in-memory database). Nothing about the incident or inventory data is simulated.

Clients registered in the provider (to try least privilege):
    ops-bot   tickets:read tickets:write inventory:read     a client allowed everything
    agent     tickets:read inventory:read                   what the support agent gets: it can read, it cannot change anything
    reader    tickets:read                                  can look at a ticket and nothing else
    nobody    (no scopes)                                   a valid client that was granted nothing
"""

import asyncio
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# the API reads these when it is imported
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "test-only-secret-with-at-least-32-characters")
os.environ.setdefault("BCRYPT_ROUNDS", "4")

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.testclient import TestClient

from mcps.dev_auth_server.app import ClientRegistration
from mcps.dev_auth_server.app import create_app as create_idp
from mcps.trackflow_tools.app import create_app as create_mcp
from mcps.trackflow_tools.app import load_auth_server
from mcps.trackflow_tools.config import load_settings

CLIENTS = {
    "ops-bot": ClientRegistration(secret="ops-secret", scopes=("tickets:read", "tickets:write", "inventory:read")),
    "agent": ClientRegistration(secret="agent-secret", scopes=("tickets:read", "inventory:read")),
    "reader": ClientRegistration(secret="reader-secret", scopes=("tickets:read",)),
    "nobody": ClientRegistration(secret="nobody-secret", scopes=()),
}
SERVICE_EMAIL, SERVICE_PASSWORD = "mcp-service@example.com", "correct-horse-42"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Running:
    """A uvicorn server in a thread."""

    def __init__(self, app, port: int):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 15
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)


class RecordingTransport(httpx.ASGITransport):
    """The MCP server's way into the TrackFlow API, recording every request: which HTTP calls really reach the Incidents Manager and the inventory."""

    def __init__(self, app, log: list):
        super().__init__(app=app)
        self.log = log

    async def handle_async_request(self, request):
        self.log.append((request.method, request.url.path))
        return await super().handle_async_request(request)


class Stack:
    def __init__(self, idp_url, mcp_url, api_engine, settings, api_requests, api_app):
        self.idp_url, self.mcp_url, self.api_engine, self.settings, self.api_requests, self.api_app = idp_url, mcp_url, api_engine, settings, api_requests, api_app

    def token(self, client: str = "ops-bot", scope: str | None = None, resource: str | None = None) -> str:
        data = {"grant_type": "client_credentials", "client_id": client, "client_secret": CLIENTS[client].secret, "resource": resource or self.settings.audience}
        if scope is not None:
            data["scope"] = scope
        response = httpx.post(f"{self.idp_url}/token", data=data)
        response.raise_for_status()
        return response.json()["access_token"]

    def headers(self, client: str = "ops-bot", **kwargs) -> dict:
        return {"Authorization": f"Bearer {self.token(client, **kwargs)}"}

    def call(self, tool: str, arguments: dict, client: str = "ops-bot"):
        """Calls a tool as an MCP client would, with a token of `client`. Returns the CallToolResult."""
        return run(_call(self.mcp_url, self.headers(client), tool, arguments))

    def call_with(self, headers: dict, tool: str, arguments: dict):
        """Like `call`, with headers chosen by the test (for example a token asked with fewer scopes)."""
        return run(_call(self.mcp_url, headers, tool, arguments))

    def list_tools(self, client: str = "ops-bot"):
        return run(_list(self.mcp_url, self.headers(client)))

    def initialize(self, client: str = "ops-bot"):
        return run(_initialize(self.mcp_url, self.headers(client)))

    def api_calls(self, prefix: str, since: int = 0) -> list:
        return [call for call in self.api_requests[since:] if call[1].startswith(prefix)]


def run(coro):
    return asyncio.run(coro)


async def _call(url, headers, tool, arguments):
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool, arguments)


async def _list(url, headers):
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.list_tools()


async def _initialize(url, headers):
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            return await session.initialize()


def seed(api: TestClient) -> None:
    """Products and movements through the real inventory API: Blue Hoodie ends with 42 units in ZGZ, Wireless Mouse with 0 in LA."""
    token = api.post("/auth/login", json={"email": SERVICE_EMAIL, "password": SERVICE_PASSWORD}).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    hoodie = api.post("/inventory/products", json={"name": "Blue Hoodie", "sku": "HOOD-BLU-M", "client_name": "Fashion Co", "category": "fashion", "warehouse": "ZGZ"}, headers=auth)
    api.post("/inventory/products", json={"name": "Wireless Mouse", "sku": "MOUSE-01", "client_name": "Tech Brand", "category": "electronics", "warehouse": "LA"}, headers=auth)
    assert hoodie.status_code == 201, hoodie.text
    sku_id = hoodie.json()["id"]
    assert api.post("/inventory/orders/inbound", json={"sku_id": sku_id, "quantity": 50, "reference": "PO-1", "warehouse": "ZGZ"}, headers=auth).status_code == 201
    outbound = api.post("/inventory/orders/outbound", json={"sku_id": sku_id, "quantity": 8, "exit_type": "dispatch", "tracking_number": "TRK-1", "warehouse": "ZGZ"}, headers=auth)
    assert outbound.status_code == 201, outbound.text


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    os.environ["AUTH_DB_PATH"] = str(tmp_path_factory.mktemp("accounts") / "users.json")
    from services.database import get_session
    from services.main import app as api_app

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def session():
        with Session(engine) as db:
            yield db

    api_app.dependency_overrides[get_session] = session
    with TestClient(api_app) as api:  # registers the service account the MCP server signs in with, and seeds inventory
        registered = api.post("/auth/register", json={"name": "MCP Service", "email": SERVICE_EMAIL, "password": SERVICE_PASSWORD})
        assert registered.status_code == 201
        from services import accounts

        accounts.update_user(registered.json()["id"], role=accounts.Role.manager)  # the incident and inventory APIs are for staff (audit H-03): so is the service account
        seed(api)

    idp_port, mcp_port = free_port(), free_port()
    issuer, public_url = f"http://127.0.0.1:{idp_port}", f"http://127.0.0.1:{mcp_port}/mcp"
    settings = load_settings({"MCP_PUBLIC_URL": public_url, "OAUTH_ISSUER": issuer, "INCIDENTS_API_URL": "http://api.test", "API_SERVICE_EMAIL": SERVICE_EMAIL, "API_SERVICE_PASSWORD": SERVICE_PASSWORD})
    api_requests: list = []
    with Running(create_idp(issuer, clients=CLIENTS), idp_port):
        mcp_app = create_mcp(settings, auth_server=load_auth_server(settings), transport=RecordingTransport(api_app, api_requests))
        with Running(mcp_app, mcp_port):
            yield Stack(issuer, public_url, engine, settings, api_requests, api_app)
    api_app.dependency_overrides.pop(get_session, None)
