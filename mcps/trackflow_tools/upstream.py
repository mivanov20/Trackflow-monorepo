"""The TrackFlow API as the MCP server sees it: the incident manager (read and write) and the inventory manager (read only).

Every call goes over HTTP to the real service (`GET/POST /api/incidents`, `PATCH /api/incidents/{id}/status`, `GET /inventory/products`): there is
no copy of the data here. The server signs in to the API with its own service account (credentials from the environment) and refreshes the token
when the API says it expired.

READ-ONLY BY DESIGN, not by omission: `InventoryClient` inherits a `_send` that refuses every method but GET, so even a future tool that tried to
write through it would fail with `InventoryReadOnly` before any request leaves the process.
"""

from __future__ import annotations

import httpx

from .config import Settings
from .errors import INVENTORY_READ_ONLY, NOT_FOUND, UPSTREAM_UNAVAILABLE, VALIDATION_ERROR, ToolFailure

SAFE_METHODS = frozenset({"GET"})


class ApiSession:
    """An HTTP client for the TrackFlow API that signs in with the service account and keeps the token."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._client = httpx.AsyncClient(base_url=settings.api_url, timeout=settings.upstream_timeout, transport=transport)
        self._token = settings.service_token

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _login(self) -> None:
        if not (self._settings.service_email and self._settings.service_password):
            raise ToolFailure(UPSTREAM_UNAVAILABLE, "The server's identity for the TrackFlow API was rejected and cannot be renewed.")
        response = await self._client.post("/auth/login", json={"email": self._settings.service_email, "password": self._settings.service_password})
        if response.status_code != 200:
            raise ToolFailure(UPSTREAM_UNAVAILABLE, "The server could not sign in to the TrackFlow API.")
        self._token = response.json()["access_token"]

    async def _send(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> httpx.Response:
        try:
            if self._token is None:
                await self._login()
            for attempt in (1, 2):
                response = await self._client.request(method, path, json=json, params=params, headers={"Authorization": f"Bearer {self._token}"})
                if response.status_code == 401 and attempt == 1:
                    self._token = None
                    await self._login()  # the token expired: sign in again once
                    continue
                return response
        except ToolFailure:
            raise
        except httpx.TimeoutException:
            raise ToolFailure(UPSTREAM_UNAVAILABLE, f"The TrackFlow API did not answer within {self._settings.upstream_timeout:g} seconds.") from None
        except httpx.HTTPError as error:
            raise ToolFailure(UPSTREAM_UNAVAILABLE, f"The TrackFlow API could not be reached ({type(error).__name__}).") from None
        raise ToolFailure(UPSTREAM_UNAVAILABLE, "The TrackFlow API rejected the server's credentials.")

    @staticmethod
    def interpret(response: httpx.Response, what: str):
        """Turns a response into data, or into the tool failure that tells the client what happened."""
        if response.status_code in (200, 201):
            return response.json()
        if response.status_code == 404:
            raise ToolFailure(NOT_FOUND, f"{what} was not found.")
        if response.status_code in (400, 422):
            try:
                body = response.json()
            except ValueError:
                body = {}
            problems = body.get("errors") or []
            detail = "; ".join(f"{p.get('field')}: {p.get('message')}" for p in problems if isinstance(p, dict)) if problems else body.get("message", "The request is not valid.")
            raise ToolFailure(VALIDATION_ERROR, detail)
        raise ToolFailure(UPSTREAM_UNAVAILABLE, f"The TrackFlow API answered {response.status_code}.")


class IncidentsClient(ApiSession):
    """The Incidents Manager: check, create and move a ticket along its lifecycle."""

    async def get_ticket(self, ticket_id: int) -> dict:
        return self.interpret(await self._send("GET", f"/api/incidents/{ticket_id}"), f"Ticket {ticket_id}")

    async def create_ticket(self, payload: dict) -> dict:
        return self.interpret(await self._send("POST", "/api/incidents", json=payload), "The ticket")

    async def update_status(self, ticket_id: int, status: str) -> dict:
        """The lifecycle endpoint, `PATCH /api/incidents/{id}/status`: the only way this server changes a ticket after it is created."""
        return self.interpret(await self._send("PATCH", f"/api/incidents/{ticket_id}/status", json={"status": status}), f"Ticket {ticket_id}")


class InventoryReadOnly(Exception):
    pass


class InventoryClient(ApiSession):
    """The inventory manager, read only: `_send` refuses anything but GET before a request is made."""

    async def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        if method.upper() not in SAFE_METHODS:
            raise InventoryReadOnly(f"{method} {path} refused: the inventory client only allows {', '.join(sorted(SAFE_METHODS))}.")
        return await super()._send(method, path, **kwargs)

    async def list_products(self) -> list[dict]:
        return self.interpret(await self._send("GET", "/inventory/products"), "The product list")

    async def get_product(self, product_id: int) -> dict:
        return self.interpret(await self._send("GET", f"/inventory/products/{product_id}"), f"Product {product_id}")


def read_only_failure(error: InventoryReadOnly) -> ToolFailure:
    return ToolFailure(INVENTORY_READ_ONLY, str(error))
