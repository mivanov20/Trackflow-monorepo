"""A minimal OAuth 2.0 / OIDC authorization server for DEVELOPMENT and TESTS of the TrackFlow MCP server.

NOT for production. In production the MCP server trusts a real provider (Auth0, Keycloak, Logto, Entra ID, Supabase Auth, ...): it only needs the
provider's issuer URL, because it discovers the signing keys from the provider's metadata. This server exists so the whole flow (a client gets
a token, the MCP server validates it with MCP Auth, scopes decide what each tool may do) can run on a laptop and in CI with no account.

What it does (and nothing else):
* `GET /.well-known/openid-configuration` and `GET /.well-known/oauth-authorization-server`: discovery metadata (RFC 8414 / OIDC discovery).
* `GET /jwks.json`: the public key that signs the tokens (RS256).
* `POST /token`: the `client_credentials` grant, the one a backend client such as the agent uses. The client authenticates with
  `client_id` and `client_secret` (HTTP Basic or form fields). The scopes it gets are the requested ones that its registration allows, so a client can
  never receive a scope it was not registered for. The token carries `iss`, `sub`, `client_id`, `aud` (the MCP server it is for), `scope`, `iat`, `exp`, `jti`.
* `GET /authorize` + `POST /token` with `grant_type=authorization_code` + `POST /register`: the authorization-code flow with PKCE (S256) and dynamic client
  registration (RFC 7591) that the MCP authorization spec requires and that MCP Auth checks for, so a browser tool such as MCP Playground can log in.
  DEVELOPMENT SHORTCUT: there is no login or consent screen, `/authorize` approves at once, and a dynamically registered client may ask for any scope. A real
  provider asks a person; this one exists so the whole flow runs without an account.

Clients come from `DEV_IDP_CLIENTS`, a JSON object `{"client_id": {"secret": "...", "scopes": ["tickets:read", ...]}}`. There are no built-in secrets.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

TOKEN_LIFETIME_SECONDS = int(os.getenv("DEV_IDP_TOKEN_SECONDS", "900"))
KEY_ID = "dev-key-1"


@dataclass(frozen=True)
class ClientRegistration:
    secret: str
    scopes: tuple[str, ...]


def load_clients(raw: str | None = None) -> dict[str, ClientRegistration]:
    """Clients from `DEV_IDP_CLIENTS` (JSON). Raises ValueError on a malformed registration, so a typo does not become an open door."""
    raw = raw if raw is not None else os.getenv("DEV_IDP_CLIENTS", "")
    if not raw.strip():
        return {}
    clients = {}
    for client_id, entry in json.loads(raw).items():
        if not isinstance(entry, dict) or not entry.get("secret") or not isinstance(entry.get("scopes"), list):
            raise ValueError(f"client '{client_id}' needs a non-empty 'secret' and a list of 'scopes'")
        clients[client_id] = ClientRegistration(secret=entry["secret"], scopes=tuple(entry["scopes"]))
    return clients


def _b64(value: int) -> str:
    return base64.urlsafe_b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()


def create_app(issuer: str, *, clients: dict[str, ClientRegistration] | None = None, scopes_supported: list[str] | None = None, private_key=None) -> Starlette:
    """The authorization server. `issuer` is its public base URL; the tokens it signs name it in `iss`."""
    issuer = issuer.rstrip("/")
    key = private_key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    numbers = key.public_key().public_numbers()
    jwks = {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": KEY_ID, "n": _b64(numbers.n), "e": _b64(numbers.e)}]}
    registry = clients if clients is not None else load_clients()
    supported = scopes_supported or sorted({scope for client in registry.values() for scope in client.scopes})

    metadata = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks.json",
        "registration_endpoint": f"{issuer}/register",
        "scopes_supported": supported,
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }

    async def discovery(_: Request) -> JSONResponse:
        return JSONResponse(metadata)

    async def keys(_: Request) -> JSONResponse:
        return JSONResponse(jwks)

    public_clients: dict[str, list[str]] = {}  # client_id -> redirect_uris, registered through /register
    codes: dict[str, dict] = {}  # authorization code -> what it was issued for (one use)

    async def register(request: Request) -> JSONResponse:
        body = await request.json()
        redirect_uris = body.get("redirect_uris") if isinstance(body, dict) else None
        if not isinstance(redirect_uris, list) or not redirect_uris or not all(isinstance(u, str) for u in redirect_uris):
            return JSONResponse({"error": "invalid_redirect_uri", "error_description": "redirect_uris must be a non-empty list."}, status_code=400)
        client_id = f"dev-public-{uuid.uuid4().hex[:12]}"
        public_clients[client_id] = redirect_uris
        return JSONResponse({"client_id": client_id, "redirect_uris": redirect_uris, "grant_types": ["authorization_code"], "response_types": ["code"], "token_endpoint_auth_method": "none"}, status_code=201)

    async def authorize(request: Request):
        q = request.query_params
        client_id, redirect_uri = q.get("client_id", ""), q.get("redirect_uri", "")
        if client_id not in public_clients or redirect_uri not in public_clients[client_id]:
            return JSONResponse({"error": "invalid_request", "error_description": "Unknown client or redirect_uri (register it at /register first)."}, status_code=400)
        def back(**params):
            return RedirectResponse(f"{redirect_uri}{'&' if '?' in redirect_uri else '?'}{urlencode({**params, **({'state': q['state']} if q.get('state') else {})})}", status_code=302)
        if q.get("response_type") != "code":
            return back(error="unsupported_response_type")
        if not q.get("code_challenge") or q.get("code_challenge_method") != "S256":
            return back(error="invalid_request", error_description="PKCE with S256 is required.")
        requested = [x for x in q.get("scope", "").split() if x] or list(supported)
        denied = [x for x in requested if x not in supported]
        if denied:
            return back(error="invalid_scope", error_description=f"Unknown scope: {' '.join(denied)}.")
        code = secrets.token_urlsafe(24)
        codes[code] = {"client_id": client_id, "redirect_uri": redirect_uri, "challenge": q["code_challenge"], "scope": requested, "resource": q.get("resource"), "expires": time.time() + 120}
        return back(code=code)  # DEVELOPMENT: approved without asking anyone

    def authorization_code_token(form) -> JSONResponse:
        issued = codes.pop(str(form.get("code", "")), None)  # a code works once
        if not issued or issued["expires"] < time.time() or issued["client_id"] != form.get("client_id") or issued["redirect_uri"] != form.get("redirect_uri"):
            return JSONResponse({"error": "invalid_grant", "error_description": "The authorization code is unknown, used, expired or for another client."}, status_code=400)
        verifier = str(form.get("code_verifier", ""))
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not secrets.compare_digest(expected, issued["challenge"]):
            return JSONResponse({"error": "invalid_grant", "error_description": "The PKCE code_verifier does not match."}, status_code=400)
        return issue(issued["client_id"], issued["scope"], issued["resource"] or form.get("resource"))

    def credentials(request: Request, form) -> tuple[str | None, str | None]:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                client_id, _, client_secret = base64.b64decode(header[6:]).decode().partition(":")
                return client_id, client_secret
            except Exception:  # noqa: BLE001
                return None, None
        return form.get("client_id"), form.get("client_secret")

    def issue(client_id: str, granted: list[str], audience: str | None) -> JSONResponse:
        now = int(time.time())
        claims = {"iss": issuer, "sub": client_id, "client_id": client_id, "scope": " ".join(granted), "iat": now, "exp": now + TOKEN_LIFETIME_SECONDS, "jti": uuid.uuid4().hex}
        if audience:
            claims["aud"] = audience
        access_token = jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": KEY_ID})
        return JSONResponse({"access_token": access_token, "token_type": "Bearer", "expires_in": TOKEN_LIFETIME_SECONDS, "scope": " ".join(granted)}, headers={"Cache-Control": "no-store"})

    async def token(request: Request) -> JSONResponse:
        form = await request.form()
        if form.get("grant_type") == "authorization_code":
            return authorization_code_token(form)
        if form.get("grant_type") != "client_credentials":
            return JSONResponse({"error": "unsupported_grant_type", "error_description": "Only client_credentials and authorization_code are supported."}, status_code=400)
        client_id, client_secret = credentials(request, form)
        registration = registry.get(client_id or "")
        # compare in constant time, and do the comparison even for an unknown client so the answer does not tell which part was wrong
        expected = registration.secret if registration else secrets.token_urlsafe(16)
        if registration is None or not secrets.compare_digest(str(client_secret or ""), expected):
            return JSONResponse({"error": "invalid_client", "error_description": "Unknown client or wrong secret."}, status_code=401, headers={"WWW-Authenticate": "Basic"})
        requested = [s for s in str(form.get("scope", "")).split() if s]
        denied = [s for s in requested if s not in registration.scopes]
        if denied:
            return JSONResponse({"error": "invalid_scope", "error_description": f"This client is not allowed: {' '.join(denied)}."}, status_code=400)
        return issue(client_id, requested or list(registration.scopes), form.get("resource") or form.get("audience"))

    return Starlette(
        routes=[
            Route("/.well-known/openid-configuration", discovery),
            Route("/.well-known/oauth-authorization-server", discovery),
            Route("/jwks.json", keys),
            Route("/authorize", authorize),
            Route("/register", register, methods=["POST"]),
            Route("/token", token, methods=["POST"]),
        ]
    )
