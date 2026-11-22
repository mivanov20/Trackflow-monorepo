"""The development OAuth provider (flows, PKCE, scopes) and the MCP server's way into the TrackFlow API (failures, token refresh)."""

import asyncio
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import httpx
import jwt as pyjwt
import pytest

from mcps.dev_auth_server.app import ClientRegistration, create_app, load_clients
from mcps.trackflow_tools import errors
from mcps.trackflow_tools.config import load_settings
from mcps.trackflow_tools.errors import ToolFailure
from mcps.trackflow_tools.upstream import IncidentsClient

REDIRECT = "https://client.example/callback"


def pkce() -> tuple[str, str]:
    verifier = "v" * 60
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def register(stack) -> str:
    response = httpx.post(f"{stack.idp_url}/register", json={"redirect_uris": [REDIRECT]})
    assert response.status_code == 201 and response.json()["token_endpoint_auth_method"] == "none"
    return response.json()["client_id"]


def authorize(stack, client_id, challenge, **extra):
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz", "resource": stack.settings.audience, **extra}
    return httpx.get(f"{stack.idp_url}/authorize", params=params, follow_redirects=False)


# ================================================================== the authorization-code flow with PKCE (what a browser client such as MCP Playground uses)
def test_the_full_authorization_code_flow_with_pkce_gives_a_token_the_mcp_server_accepts(stack):
    client_id, (verifier, challenge) = register(stack), pkce()
    redirect = authorize(stack, client_id, challenge)
    assert redirect.status_code == 302
    query = parse_qs(urlparse(redirect.headers["location"]).query)
    assert query["state"] == ["xyz"] and query["code"]
    token = httpx.post(f"{stack.idp_url}/token", data={"grant_type": "authorization_code", "code": query["code"][0], "redirect_uri": REDIRECT, "client_id": client_id, "code_verifier": verifier})
    assert token.status_code == 200 and token.json()["token_type"] == "Bearer"
    claims = pyjwt.decode(token.json()["access_token"], options={"verify_signature": False})
    assert claims["aud"] == stack.settings.audience and claims["client_id"] == client_id and claims["iss"] == stack.idp_url
    tools = stack.call_with({"Authorization": f"Bearer {token.json()['access_token']}"}, "query_inventory", {"operation": "list"})
    assert not tools.isError  # the token a browser client gets works against the server


def test_a_wrong_pkce_verifier_and_a_reused_code_are_refused(stack):
    client_id, (verifier, challenge) = register(stack), pkce()
    code = parse_qs(urlparse(authorize(stack, client_id, challenge).headers["location"]).query)["code"][0]
    wrong = httpx.post(f"{stack.idp_url}/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": client_id, "code_verifier": "x" * 60})
    assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"
    code = parse_qs(urlparse(authorize(stack, client_id, challenge).headers["location"]).query)["code"][0]
    good = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": client_id, "code_verifier": verifier}
    assert httpx.post(f"{stack.idp_url}/token", data=good).status_code == 200
    assert httpx.post(f"{stack.idp_url}/token", data=good).json()["error"] == "invalid_grant"  # a code works once


def test_authorize_requires_a_registered_client_a_known_redirect_and_pkce(stack):
    _, challenge = pkce()
    assert authorize(stack, "never-registered", challenge).status_code == 400
    client_id = register(stack)
    assert httpx.get(f"{stack.idp_url}/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": "https://evil.example/cb", "code_challenge": challenge, "code_challenge_method": "S256"}, follow_redirects=False).status_code == 400
    no_pkce = httpx.get(f"{stack.idp_url}/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT}, follow_redirects=False)
    assert "error=invalid_request" in no_pkce.headers["location"]
    assert "error=invalid_scope" in authorize(stack, client_id, challenge, scope="admin:everything").headers["location"]


def test_registration_needs_redirect_uris(stack):
    assert httpx.post(f"{stack.idp_url}/register", json={}).status_code == 400


# ================================================================== the client-credentials flow
def test_a_wrong_secret_and_an_unknown_client_get_the_same_401(stack):
    wrong = httpx.post(f"{stack.idp_url}/token", data={"grant_type": "client_credentials", "client_id": "agent", "client_secret": "nope"})
    unknown = httpx.post(f"{stack.idp_url}/token", data={"grant_type": "client_credentials", "client_id": "ghost", "client_secret": "nope"})
    assert wrong.status_code == unknown.status_code == 401 and wrong.json() == unknown.json()  # the answer does not say which part was wrong


def test_http_basic_client_authentication_works_and_other_grants_are_refused(stack):
    ok = httpx.post(f"{stack.idp_url}/token", data={"grant_type": "client_credentials", "scope": "tickets:read"}, auth=("agent", "agent-secret"))
    assert ok.status_code == 200 and ok.json()["scope"] == "tickets:read"
    assert httpx.post(f"{stack.idp_url}/token", data={"grant_type": "password", "client_id": "agent", "client_secret": "agent-secret"}).json()["error"] == "unsupported_grant_type"


def test_the_provider_publishes_what_mcp_auth_checks_for(stack):
    metadata = httpx.get(f"{stack.idp_url}/.well-known/openid-configuration").json()
    assert {"authorization_code", "client_credentials"} <= set(metadata["grant_types_supported"]) and metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["jwks_uri"].endswith("/jwks.json") and metadata["registration_endpoint"].endswith("/register")
    keys = httpx.get(metadata["jwks_uri"]).json()["keys"]
    assert keys[0]["kty"] == "RSA" and keys[0]["alg"] == "RS256" and "d" not in keys[0]  # only the public key is published


def test_client_registrations_are_validated_and_there_are_no_built_in_secrets():
    assert load_clients("") == {} and load_clients("{}") == {}
    assert load_clients(json.dumps({"a": {"secret": "s", "scopes": ["x"]}}))["a"] == ClientRegistration(secret="s", scopes=("x",))
    for bad in ({"a": {"scopes": ["x"]}}, {"a": {"secret": "", "scopes": ["x"]}}, {"a": {"secret": "s", "scopes": "x"}}, {"a": "text"}):
        with pytest.raises(ValueError):
            load_clients(json.dumps(bad))


# ================================================================== the way into the TrackFlow API
def settings(**env):
    base = {"MCP_PUBLIC_URL": "http://localhost:8765/mcp", "OAUTH_ISSUER": "http://localhost:9000", "API_SERVICE_EMAIL": "svc@example.com", "API_SERVICE_PASSWORD": "pw", "INCIDENTS_API_URL": "http://api.test"}
    return load_settings({**base, **env})


def client_with(handler, **env) -> IncidentsClient:
    return IncidentsClient(settings(**env), transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def login_then(responder):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/login":
            return httpx.Response(200, json={"access_token": "token-1"})
        return responder(request)

    return handler


@pytest.mark.parametrize("status, code", [(404, errors.NOT_FOUND), (400, errors.VALIDATION_ERROR), (500, errors.UPSTREAM_UNAVAILABLE), (502, errors.UPSTREAM_UNAVAILABLE), (503, errors.UPSTREAM_UNAVAILABLE)])
def test_an_api_status_becomes_the_matching_tool_error_code(status, code):
    client = client_with(login_then(lambda request: httpx.Response(status, json={"message": "no", "errors": [{"field": "title", "message": "Title is required."}]})))
    with pytest.raises(ToolFailure) as failure:
        run(client.get_ticket(1))
    assert failure.value.code == code


def test_a_validation_response_names_each_field():
    client = client_with(login_then(lambda r: httpx.Response(400, json={"error": "validation_error", "message": "Some fields need your attention.", "errors": [{"field": "category", "message": "Category must be one of: a, b."}, {"field": "title", "message": "Title is required."}]})))
    with pytest.raises(ToolFailure) as failure:
        run(client.create_ticket({}))
    assert "category: Category must be one of: a, b." in failure.value.detail and "title: Title is required." in failure.value.detail


@pytest.mark.parametrize("raised", [httpx.ReadTimeout("slow"), httpx.ConnectError("refused"), httpx.RemoteProtocolError("broken")])
def test_a_slow_or_unreachable_api_is_upstream_unavailable_without_internals(raised):
    def handler(request):
        if request.url.path == "/auth/login":
            return httpx.Response(200, json={"access_token": "t"})
        raise raised

    with pytest.raises(ToolFailure) as failure:
        run(client_with(handler).get_ticket(1))
    assert failure.value.code == errors.UPSTREAM_UNAVAILABLE and "api.test" not in str(failure.value)


def test_an_expired_token_is_renewed_once_and_the_call_succeeds():
    logins, seen = [], []

    def handler(request):
        if request.url.path == "/auth/login":
            logins.append(1)
            return httpx.Response(200, json={"access_token": f"token-{len(logins)}"})
        seen.append(request.headers["authorization"])
        return httpx.Response(401) if request.headers["authorization"] == "Bearer token-1" else httpx.Response(200, json={"id": 1})

    assert run(client_with(handler).get_ticket(1)) == {"id": 1}
    assert seen == ["Bearer token-1", "Bearer token-2"] and len(logins) == 2


def test_credentials_the_api_keeps_rejecting_are_an_upstream_error_not_a_loop():
    def handler(request):
        return httpx.Response(200, json={"access_token": "t"}) if request.url.path == "/auth/login" else httpx.Response(401)

    with pytest.raises(ToolFailure) as failure:
        run(client_with(handler).get_ticket(1))
    assert failure.value.code == errors.UPSTREAM_UNAVAILABLE


def test_a_failed_login_is_an_upstream_error():
    with pytest.raises(ToolFailure) as failure:
        run(client_with(lambda request: httpx.Response(401, json={"detail": "bad"})).get_ticket(1))
    assert failure.value.code == errors.UPSTREAM_UNAVAILABLE and "sign in" in failure.value.detail


def test_a_ready_made_service_token_is_used_without_logging_in():
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("authorization")))
        return httpx.Response(200, json={"id": 1})

    client = IncidentsClient(load_settings({"MCP_PUBLIC_URL": "http://localhost:8765/mcp", "OAUTH_ISSUER": "http://localhost:9000", "API_SERVICE_TOKEN": "ready-token"}), transport=httpx.MockTransport(handler))
    run(client.get_ticket(1))
    assert seen == [("/api/incidents/1", "Bearer ready-token")]


def test_the_status_change_uses_the_lifecycle_endpoint_with_the_status_in_the_body():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.content))
        return httpx.Response(200, json={"id": 5, "status": "resolved"})

    run(client_with(login_then(handler)).update_status(5, "resolved"))
    method, path, body = seen[0]
    assert (method, path, json.loads(body)) == ("PATCH", "/api/incidents/5/status", {"status": "resolved"})
