# TrackFlow Backend Architecture Proposal

**Status:** Initial proposal for team discussion
**Scope:** Backend organization and frontend/backend boundary; this is not an API contract or an implementation plan.

## 1. Context and goals

TrackFlow operates warehouses in Los Angeles and Zaragoza and coordinates inventory, orders, carriers, deliveries, returns, and customer support across the United States and Spain. Its existing systems are fragmented: the warehouses use different WMS tools, the ERP is legacy, and integrations have grown without consistent documentation. The technology group is small, so the backend should make business rules understandable and changes safe without creating an operational burden of many independently deployed services.

The repository is already a monorepo with separate UI and backend areas. It also contains an initial FastAPI authentication and inventory API. This proposal treats that API as a starting vertical slice, not as proof that every future domain or contract has already been decided. The existing routes are currently unversioned.

The architecture should:

- Keep one dependable API boundary between user interfaces and company data/integrations.
- Separate HTTP concerns, business rules, persistence, and external-system translation.
- Let teams work by business capability without duplicating shared infrastructure.
- Leave room to extract a component later if scale, reliability, or team ownership makes that worthwhile.
- Avoid inventing country-, carrier-, or client-specific policies before Operations confirms them.

## 2. Recommended pattern

Adopt a **layered modular monolith**, organized around TrackFlow business domains.

“Monolith” here means one deployable FastAPI application and one API contract, not one large source file. “Layered” means each request moves through clear responsibilities: an API router validates and translates HTTP input; a domain service applies business rules; a repository or integration adapter performs I/O; and a response schema controls what leaves the system. “Modular” means inventory, shipments, returns, and other capabilities have explicit ownership and do not reach into one another's internal implementation.

This fits TrackFlow now for several concrete reasons:

- Inventory, order movements, shipment handoff, tracking, and returns form connected operational workflows. A single API can coordinate these consistently while the team establishes shared data and integration contracts.
- The seven-person technology team is also responsible for legacy integration and operational reliability. Microservices would add deployment, network-failure, data-consistency, and monitoring work before independent scaling or team ownership has been demonstrated.
- Domain modules address the existing risk of undocumented point-to-point integrations. Vendor-specific formats can be isolated behind adapters instead of spreading throughout endpoints and UI code.
- A modular boundary preserves an evolution path: a domain can be extracted into a separate service if it later needs independent scaling, release cadence, or ownership. That should follow evidence, not precede it.

This is not an MVC application serving server-rendered pages. FastAPI exposes an HTTP API to separate user interfaces, so domain modules and API routers are the more useful organizing boundaries. MVC ideas such as keeping presentation separate from business logic still apply, but MVC is not the primary system boundary. A serverless design is also not the default recommendation for the core API: the current need is a coherent operational API with persistent business data and multiple integrations. Event-triggered or scheduled work can be evaluated separately when a concrete workload requires it.

## 3. Proposed backend structure

Keep the backend in the existing `services/` area and retain one application entry point. The following is a target structure to grow toward; it does not require moving the current files as part of this documentation task.

```text
services/
  main.py                         # Create the FastAPI app, lifecycle, middleware, router inclusion
  core/
    config.py                     # Validated settings loaded from environment
    security.py                   # Shared authentication/security helpers
  api/
    dependencies.py               # Request-scoped database and authenticated-user dependencies
    routers/
      auth.py                     # Authentication HTTP endpoints
      inventory.py                # SKU and stock-movement HTTP endpoints
      shipments.py                # Shipment and carrier HTTP endpoints
      returns.py                  # Return HTTP endpoints
      support.py                  # Customer-support HTTP endpoints
      reports.py                  # Operational reporting HTTP endpoints
  domains/
    identity/
      service.py                  # Account and identity use cases
      schemas.py                  # Identity request/response models
    inventory/
      service.py                  # Stock and warehouse rules
      repository.py               # Inventory persistence queries
      models.py                   # Inventory database models
      schemas.py                  # Inventory request/response models
    shipments/
      service.py                  # Shipment and carrier-selection rules
      repository.py               # Shipment persistence queries
      models.py
      schemas.py
    returns/
      service.py                  # Return workflow and decision rules
      repository.py
      models.py
      schemas.py
    customer_experience/
      service.py                  # Ticket and support use cases, when approved
      repository.py
      models.py
      schemas.py
  integrations/
    carriers/                     # Carrier-specific clients and payload translation
    warehouse_systems/            # WMS-specific clients and payload translation
    erp/                           # ERP integration boundary
  database.py                     # Engine/session creation and shared database setup
  tests/
    unit/                          # Domain rules tested without external systems
    integration/                   # API, persistence, and adapter interaction tests
```

The tree illustrates ownership, not a requirement to create empty folders for every future capability. Add a domain package when that domain has real use cases. Use the same pattern within each active domain: keep request/response schemas separate from persistence models; keep business decisions in domain services rather than route handlers; and put database queries behind repositories when that makes the domain logic easier to test or change. Shared dependencies, configuration, and database setup belong in shared backend modules, not copied into each router.

The current `services/main.py`, `services/auth.py`, `services/database.py`, `services/models.py`, `services/schemas.py`, and `services/routers/inventory.py` already demonstrate a FastAPI entry point, router separation, persistence models, and separate request/response schemas. They are a useful first slice. As new domains arrive, the team can move logic toward the target boundaries incrementally; a broad refactor is not needed just to adopt the proposal.

### Responsibility boundaries

| Responsibility | Owns | Should not own |
|---|---|---|
| API router | URL, HTTP method, request/response schema, status code, auth dependency | Stock, return-approval, or carrier-selection business policy |
| Domain service | Business use cases, validations across entities, transaction-level decisions | FastAPI request parsing or vendor-specific wire formats |
| Repository/database | Persistence queries, sessions, database models and transaction mechanics | HTTP responses or frontend-specific presentation |
| Integration adapter | Translation to/from a carrier, WMS, or ERP contract; integration-specific failures | The canonical TrackFlow domain contract |
| Core configuration/security | Typed environment configuration and shared authentication primitives | Per-domain business rules or hard-coded deployment secrets |

## 4. Router and endpoint organization

Use one `APIRouter` per domain or cohesive HTTP capability, with a stable prefix and OpenAPI tags. Register those routers in `main.py`. This keeps the app entry point focused on composition and gives the team a discoverable API surface in FastAPI's generated OpenAPI documentation.

The route examples below describe resource groupings, not a promise that every route will be implemented. The existing routes are marked as implemented in the starter API; future groups depend on product requirements and integration contracts. Before exposing a stable API to more clients, agree on a version prefix such as `/api/v1`. The current starter endpoints do not yet have that prefix, so any migration needs to be coordinated with their consumers.

| Domain/router | Route examples | Scope and access considerations |
|---|---|---|
| Health | `GET /health` | Basic service health for deployment checks; do not return secrets or infrastructure details. Exists in the current API. |
| Authentication | `POST /auth/register`, `POST /auth/login` | Current starter endpoints. Account creation should be controlled onboarding, not unrestricted public registration, unless the business approves that policy. |
| Inventory and warehouse operations | `GET/POST /inventory/products`, `GET /inventory/products/{sku_id}`, `GET /inventory/orders`, `POST /inventory/orders/inbound`, `POST /inventory/orders/outbound` | Existing starter API. Protect operative actions; validate warehouse ownership and movement rules in the domain layer. Stock is derived from movements in the current design rather than independently edited in multiple systems. |
| Shipments and carriers | Examples: `GET /api/v1/shipments/{shipment_id}`, `POST /api/v1/carriers/recommendations`, `GET /api/v1/carriers/performance` | Proposed. Keep carrier-specific payloads behind adapters; finalize carrier selection criteria and metrics with Operations. |
| Public tracking | Example: `GET /api/v1/tracking/{public_token}` | Proposed read-only surface for recipients. Return only the minimum approved tracking details; define token, retention, and privacy rules before implementation. |
| Returns | Examples: `POST /api/v1/returns`, `GET /api/v1/returns/{return_id}`, `POST /api/v1/returns/{return_id}/decisions` | Proposed. Model approval, collection, inspection, and disposition as explicit state transitions; policy must be configurable and approved per client. |
| Customer experience | Examples: `POST /api/v1/support/tickets`, `GET /api/v1/support/tickets/{ticket_id}` | Proposed. Separate customer-facing access from staff access and avoid exposing another client's or recipient's data. |
| Operational reporting | Example: `GET /api/v1/reports/operations` | Proposed read-oriented aggregates for dashboards. Define KPI meanings, filters, and freshness guarantees with the teams that own the data. |

Group routes by domain, not by HTTP verb or by one giant `routes.py`. Keep routers thin: they should call the domain use case and map its result to an HTTP response. Use shared auth dependencies for protected groups and explicit public access only for endpoints that genuinely need it. Do not let the frontend call carrier or legacy-system APIs directly; the backend integration boundary should normalize those systems for TrackFlow consumers.

## 5. FastAPI conventions and research

FastAPI's official **Bigger Applications - Multiple Files** guide demonstrates a Python package with a small `main.py`, separate modules under `routers/`, reusable dependencies, and `APIRouter` instances that are included by the main app. Router prefixes, tags, dependencies, and common responses can be applied at router inclusion so related endpoints remain grouped without duplicating configuration. This proposal applies that convention inside the existing `services/` package instead of creating a second app package at the repository root.

FastAPI's **Settings and Environment Variables** guide recommends putting settings in a separate module, reading variable configuration from the environment, validating typed values, and injecting settings where useful for testing. Accordingly, configuration belongs in `core/config.py`, while secrets and per-deployment URLs stay outside source code. The current service already reads values such as `DATABASE_URL`, `JWT_SECRET`, and `CORS_ORIGINS` from the environment; the target structure makes this responsibility more explicit.

These conventions inform the module boundaries above: `main.py` composes the app, `api/routers/` owns path operations, dependencies are reusable, schemas describe HTTP input/output, and domain code is kept out of the route modules. This is a proposal based on the official framework guidance, not a claim that FastAPI mandates one folder layout.

## 6. Frontend and backend as separate systems

The monorepo is appropriate for the current project: the frontend and API are developed against shared company requirements by one small team, and keeping them together makes coordinated changes and documentation easy. They should still be treated as separate applications. The UI should own rendering and user interaction; the FastAPI service should own authentication, business rules, persistence, and integration with WMS, ERP, and carriers. Each can have its own dependencies, run command, configuration, tests, and deployment lifecycle. Separate repositories can be reconsidered if team ownership or release independence becomes a demonstrated need; repository separation is not required to obtain runtime separation.

Browser-based UIs should communicate with the backend through a documented HTTP/JSON API, using `fetch` or an equivalent client and the API's base URL from frontend configuration. The existing backoffice is a static HTML/CSS/JavaScript application and currently uses illustrative local data; a static page does not automatically read a `.env.local` file. When connecting it to the API, supply the base URL through a configuration mechanism supported by the chosen frontend delivery setup. A browser-visible API URL is public configuration, not a secret. Never place database credentials, signing keys, or carrier credentials in frontend code or variables exposed to browser bundles.

The backend should receive deployment-specific settings separately, for example database URL, JWT signing secret, and allowed frontend origins. Use ignored local environment files during development and the deployment platform's secret/configuration store outside development. Validate required values at startup and fail with a safe configuration error; never commit credentials.

When frontend and API use different scheme, host, or port, browser requests are cross-origin. Configure FastAPI `CORSMiddleware` with the exact development and deployed frontend origins, required methods, and headers such as `Authorization` and `Content-Type`. Avoid a wildcard origin, especially when credentialed browser requests are used. Keep cookie credentials disabled unless the chosen authentication flow needs cookies; CORS permission is not authentication or authorization. The current API already reads an origin allowlist from `CORS_ORIGINS`, which is the right configuration boundary to retain and validate for each deployment.

## 7. Initial technical decisions

1. **One FastAPI application:** use the existing centralized `services/` API; add routers by domain rather than launching one service per department.
2. **Layered modular structure:** router to domain service to repository or adapter, with request/response schemas separate from database models.
3. **REST over HTTP with JSON:** expose stable resource-oriented contracts to the UIs and use FastAPI's OpenAPI output as the shared API reference. Agree on `/api/v1` before external clients depend on the contract; coordinate any change to the existing unversioned routes.
4. **PostgreSQL for operational business data:** the current inventory slice uses Supabase/PostgreSQL and SQLModel. Keep inventory movements auditable and compute stock from the movement ledger unless a measured performance need justifies a derived cache with reconciliation.
5. **Authentication boundary:** authenticate operative actions centrally and apply authorization at the endpoint/use-case boundary. Review the current TinyDB account store as a learning/prototype choice; before multi-instance or production operation, select a durable shared identity store and document onboarding, roles, and account lifecycle.
6. **Integration adapters:** isolate each WMS, ERP, and carrier's credentials, payload mapping, timeouts, and error handling. TrackFlow's internal API should not expose vendor schemas as its public contract.
7. **Configuration by environment:** keep secrets out of Git and browser bundles; use separate development, test, and production configuration and validate required settings at startup.
8. **Incremental delivery:** make inventory/auth the first supported vertical slice, then add other domains only when their user flows, data ownership, and operational policies are confirmed.

## 8. Risks and points of attention

| Risk if boundaries are ignored | Likely consequence | Architectural response |
|---|---|---|
| Business rules accumulate in routers or one shared module | A stock, return, or carrier-policy change can break unrelated endpoints; rules become hard to test and audit | Keep routers thin and assign each rule to an owning domain service with focused tests. |
| Every domain directly reads or writes shared tables and vendor payloads | Inventory, WMS, ERP, and carrier schemas become tightly coupled; changing one integration spreads changes through the API | Keep persistence and vendor translation behind domain repositories and adapters; agree on canonical TrackFlow models. |
| Frontend configuration, CORS, or secrets are handled casually | Browser requests fail across origins, or secrets are exposed in public assets; a permissive CORS rule may be mistaken for access control | Use a per-environment exact origin allowlist; expose only the API URL to the browser and keep credentials server-side. Authenticate and authorize independently of CORS. |
| TinyDB remains the shared identity store when the API is deployed with multiple workers or instances | Local file state can diverge or fail to be shared, causing inconsistent account and token checks | Treat the current store as a prototype boundary and decide on durable shared identity storage before production scale-out. |
| Proposed endpoints are treated as approved business policy | A route may encode incorrect return eligibility, carrier ranking, retention, or country-specific behavior | Validate rules, data ownership, privacy, and country requirements with the domain owners before freezing contracts. |
| Microservices are introduced before teams and operations can support them | More deployments, network failure modes, duplicated data, and observability work slow delivery without proven benefits | Keep domain boundaries in the modular monolith and extract only when measurable scale or ownership needs justify it. |

## 9. Open decisions before implementation

- Which identity provider and role model will replace or validate the current prototype account storage?
- Which system is authoritative for each shipment, inventory movement, client, and return record, and how will WMS/ERP changes be reconciled?
- Which tracking fields may be exposed to recipients, and what token, retention, and privacy rules apply?
- What are the approved per-client and country-specific return rules, carrier-selection criteria, and reporting KPI definitions?
- Which frontend applications will call the API, and how will the static backoffice receive public runtime/build configuration in each deployment?
- What availability, latency, audit, and integration retry requirements should guide production deployment and background processing?

## 10. References

- FastAPI, [Bigger Applications - Multiple Files](https://fastapi.tiangolo.com/tutorial/bigger-applications/): package layout, `APIRouter`, dependencies, and app composition.
- FastAPI, [Settings and Environment Variables](https://fastapi.tiangolo.com/advanced/settings/): typed settings, environment configuration, and settings dependencies.
- FastAPI, [CORS (Cross-Origin Resource Sharing)](https://fastapi.tiangolo.com/tutorial/cors/): origins, `CORSMiddleware`, methods, headers, and credentials.
- MDN, [Cross-Origin Resource Sharing (CORS)](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS): browser cross-origin requests and preflight behavior.
- Twelve-Factor App, [Config](https://12factor.net/config): separating deployment-specific configuration from code.
- Repository context: [`CONTEXT.md`](../CONTEXT.md), [`services/README.md`](../services/README.md), [`services/main.py`](../services/main.py), and [`uis/backoffice/README.md`](../uis/backoffice/README.md).