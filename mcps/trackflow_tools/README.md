# TrackFlow MCP server (`mcps/trackflow_tools`)

An independent, reusable MCP server that exposes TrackFlow's company tools, protected by OAuth:

| Tool | Does | Scope it needs | Reads / writes |
|---|---|---|---|
| `get_ticket` | status and details of one ticket of the Incidents Manager | `tickets:read` | reads |
| `create_ticket` | opens a ticket | `tickets:write` | writes |
| `update_ticket_status` | moves a ticket along its lifecycle (`PATCH /api/incidents/{id}/status`) | `tickets:write` | writes |
| `query_inventory` | products and current stock (`search`, `get`, `list`) | `inventory:read` | **reads only** |

Any MCP client that can get an OAuth token can use it: the support agent (see `services/agent/`), other agents, other teams, a browser tool such as MCP Playground. The data is the real data: every tool calls the real TrackFlow API (`/api/incidents`, `/inventory/products`), there is no copy of it here.

## Decisions

**Transport: Streamable HTTP, not stdio.** stdio is for a server that one client starts as its own child process on the same machine; the MCP authorization spec does not apply to it (it takes credentials from the environment). This server is meant to be reused by several clients, remote ones included, and it protects company data, so it needs HTTP, where every request carries an OAuth bearer token that the server validates. It runs stateless (no session to keep or to hijack: each request is checked on its own) and answers with plain JSON. Consequence for authentication: the server is an OAuth *resource server*: it never sees a password or a secret of a client, only access tokens.

**Authentication: MCP Auth (`mcpauth`), not FastMCP's built-in auth.** `GET /.well-known/oauth-protected-resource` (RFC 9728) says which authorization server issues tokens for this server and which scopes exist; the MCP Auth bearer middleware verifies each JWT against the provider's keys (JWKS, discovered from the issuer's metadata), checks the issuer and the audience, and rejects anything else *before* any tool is reachable. A 401 carries `WWW-Authenticate: Bearer resource_metadata="..."` so a client finds the provider on its own. Any OAuth 2.1 / OIDC provider works (Auth0, Keycloak, Logto, Entra ID, Supabase Auth...): the server only needs its issuer URL.

**Least privilege: one scope per kind of operation, checked inside each tool.** The server only lets in a valid token; each tool then requires its own scope from that validated token. A client with only `tickets:read` can look at a ticket and nothing else; the support agent's client has `tickets:read inventory:read`: it can read and it **cannot** create or change anything, even if its code tried (`[INSUFFICIENT_SCOPE]`). The scope check is per tool, not per server (`required_scopes` of MCP Auth applies to the whole endpoint, and the three tools need different scopes), so MCP Auth does the authentication, audience and issuer checks and the tools enforce the scopes from `mcp_auth.auth_info`.

**The inventory tool is read-only by design, and rejects writes explicitly.** Three layers: (1) the tool's only operations are `search`, `get` and `list`; (2) a write operation asked of the tool (`create`, `update`, `adjust`, `delete`, `receive`, `dispatch`, `set quantity`... any wording) is *rejected* with `[INVENTORY_READ_ONLY]`, not ignored and not mistaken for a typo (an unknown operation that is not a write is `[VALIDATION_ERROR]`); (3) the HTTP client the tool uses (`upstream.InventoryClient`) refuses every method but GET before any request leaves the process. A test sends 13 write attempts and checks that no request reached the inventory API and that stock is unchanged.

**Ticket status changes go through the lifecycle endpoint.** `update_ticket_status` calls `PATCH /api/incidents/{id}/status`, never a generic PATCH on the incident, so the Incidents Manager's lifecycle rules apply (open → in_progress → resolved; open or in_progress → discarded; resolved and discarded are final). A test records the HTTP requests and checks that the only PATCH is the status one, and that an illegal move comes back as `[VALIDATION_ERROR]`.

## Discovery: what a client learns with no human context

* `initialize` returns `instructions`: the systems, the four tools, the scopes, the error codes and the ticket lifecycle.
* `tools/list` returns for each tool a description (what it does, whether it reads or writes, which scope it needs, which errors it returns), an `inputSchema` (required fields, and an `enum` of the allowed values for `category`, `origin`, `branch`, `status` and `operation`), an `outputSchema` (the ticket or product fields, from the incident and inventory APIs) and `annotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`).
* `GET /.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server` describe the authorization side.

Tests check all of this from the server's own responses, without reading the source.

## Errors and exit codes

| Where | Failure | HTTP | Code |
|---|---|---|---|
| transport (MCP Auth) | no `Authorization` header | 401 | `missing_auth_header` |
| transport | header is not `Bearer <token>` | 401 | `invalid_auth_header_format` / `missing_bearer_token` |
| transport | bad signature, expired, malformed JWT | 401 | `invalid_token` |
| transport | token from another issuer / for another server | 401 | `invalid_issuer` / `invalid_audience` |
| tool | token valid but lacks the scope the tool needs | 200, `isError` | `[INSUFFICIENT_SCOPE]` |
| tool | invalid argument, unknown value, illegal lifecycle move | 200, `isError` | `[VALIDATION_ERROR]` |
| tool | that ticket or product does not exist | 200, `isError` | `[NOT_FOUND]` |
| tool | a write attempted through the inventory tool | 200, `isError` | `[INVENTORY_READ_ONLY]` |
| tool | the TrackFlow API did not answer or failed | 200, `isError` | `[UPSTREAM_UNAVAILABLE]` |
| process | required setting missing or invalid | exit code **2** | message on stderr |
| process | the OAuth provider's metadata cannot be read | exit code **3** | message on stderr |

A tool failure is reported the MCP way (a result with `isError: true`), and its text starts with `[CODE]`. The message never contains a token, a password or an internal address.

## Audit log

Every tool invocation writes one JSON line to the logger `trackflow.mcp.audit`: `tool`, `client_id` (the OAuth client the token was issued to), `subject`, `scopes`, `outcome` (`ok` / `error`), `code`, `duration_ms` and `arguments` (long text shortened, empty values left out). The access token is never logged.

```json
{"event": "tool_call", "tool": "create_ticket", "client_id": "agent", "subject": "agent", "scopes": ["tickets:read", "inventory:read"], "outcome": "error", "code": "INSUFFICIENT_SCOPE", "duration_ms": 3, "arguments": {"title": "Lost parcel", ...}}
```

## Running it

```powershell
pip install -r mcps/requirements.txt
```

1. **An OAuth provider.** In production, your real provider. For development there is a small stand-in, `mcps/dev_auth_server` (NOT for production: it approves the authorization-code flow without asking anyone). Register clients in `DEV_IDP_CLIENTS` (JSON, no built-in secrets) and start it:

```powershell
$env:DEV_IDP_CLIENTS = '{"ops-bot": {"secret": "<choose>", "scopes": ["tickets:read", "tickets:write", "inventory:read"]}, "agent": {"secret": "<choose>", "scopes": ["tickets:read", "inventory:read"]}}'
python -m mcps.dev_auth_server
```

2. **The TrackFlow API** (incident manager and inventory) running, and an account for the server to sign in with (register one through `POST /auth/register`).

3. **The server:**

```powershell
$env:MCP_PUBLIC_URL = "http://localhost:8765/mcp"
$env:OAUTH_ISSUER = "http://localhost:9000"
$env:INCIDENTS_API_URL = "http://localhost:8000"
$env:API_SERVICE_EMAIL = "<the service account>"
$env:API_SERVICE_PASSWORD = "<its password>"
python -m mcps.trackflow_tools
```

All settings are in `config.py` (`MCP_PUBLIC_URL` is also the token audience; `MCP_ALLOWED_ORIGINS` lists the browser origins allowed, MCP Playground by default).

4. **Try it** with a token (the `resource` parameter makes it a token *for this server*):

```powershell
$token = (Invoke-RestMethod -Method Post -Uri http://localhost:9000/token -Body @{grant_type="client_credentials"; client_id="ops-bot"; client_secret="<secret>"; resource="http://localhost:8765/mcp"}).access_token
$env:MCP_TOKEN = $token
python -m mcps.trackflow_tools.smoke_client
```

`smoke_client` connects, lists the tools and runs one complete flow per tool, including the inventory write attempt that must fail.

## MCP Playground (browser)

[MCP Playground](https://www.mcpplayground.tech/playground) runs in the browser, so it cannot reach `localhost`. From GitHub Codespaces:

1. Start the three processes above in the Codespace.
2. In the **Ports** tab, forward port `8765` and set its visibility to **Public**; copy the forwarded URL (`https://<codespace>-8765.app.github.dev`).
3. Start the server with `MCP_PUBLIC_URL=https://<codespace>-8765.app.github.dev/mcp` (and the provider's `DEV_IDP_ISSUER` / `OAUTH_ISSUER` with its own forwarded URL), so the token audience and the metadata name the public address.
4. Paste `https://<codespace>-8765.app.github.dev/mcp` into Playground. Either let it run the OAuth flow (the dev provider supports dynamic client registration and the authorization-code flow with PKCE), or paste a bearer token from step 4 above as the `Authorization` header.
5. Run one flow per tool and the inventory write attempt; the expected results are in the tests (`tests/mcps/test_mcp_server.py`).

> **Status: not exercised yet.** This repository was developed on a local machine with no Codespace, so MCP Playground has **not** been run against a public forwarded URL; the steps above are the procedure, not a result. What *has* been run is the same flow with a real MCP client against the real stack (`tests/mcps`): a server on a real port, a real OAuth provider and a real API.

## How the agent uses it

`services/agent/` is an MCP client of this server (`mcp_client.py`, `langchain-mcp-adapters`): it gets a token with the client-credentials grant for the scopes `tickets:read inventory:read`, loads the server's tools from discovery and calls `get_ticket` and `query_inventory`. The agent's previous direct access to the incident and inventory data was removed: there is one path. See `docs/agent/agent-tools-and-routing.md`.

## Tests

```powershell
python -m pytest tests/mcps
```

They start the provider and the server on real ports in front of the real TrackFlow API (in-process, over an in-memory database): authentication failures (401, one code each), discovery, scopes, ticket create / status / check against the real incident manager, the inventory read-only guarantee, the audit log, configuration errors and exit codes, the authorization-code flow with PKCE, and the agent as a client.
