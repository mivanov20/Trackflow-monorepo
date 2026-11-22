<!-- GENERATED FILE. Do not edit telemetry-plan.md by hand: edit plan.template.md or events.py, then run `python docs/telemetry/build.py`. -->

# TrackFlow Telemetry Plan

Version {{SCHEMA_VERSION}} · {{EVENT_COUNT}} events · companion file: [`event-schemas.json`](event-schemas.json)

This is a design document. It describes what TrackFlow should measure, why, and exactly how each event looks. It contains no instrumentation code. Another developer should be able to instrument every event from this document and the JSON file alone.

### Where to start if you are going to instrument

1. Read section 4.2 (the envelope) and section 9 (transport).
2. Use the **instrumentation quick reference** in section 3.6: one row per event with the file and function, the exact moment, and the delivery mode.
3. For the property list, the source of each value and an example, open the event in section 4.5.
4. Check section 10 first for the events that depend on a missing feature.

## 1. Purpose and scope

TrackFlow replaces two separate warehouse systems with one inventory platform. Telemetry is how the company will learn whether the platform works, and whether the business behind it is healthy, without waiting for the Sunday-evening spreadsheet.

Each event answers one rule:

> We capture `[event_type]` because we need to know `[hypothesis]`, which allows us to make the decision `[decision]`.

An event with no decision attached is not collected. This keeps the plan small enough to maintain and every event defensible.

### Who the data is for

| Persona | Role | What they decide with telemetry |
|---|---|---|
| Ana Whitfield | Warehouse Operations | Capacity and staffing, bottlenecks, which SKUs need a recount, training |
| Miguel Torres | Commercial and Client Relations | Stockout alerts to clients, catalogue growth per client |
| Thomas Harry | CEO | Volume and SLA by country, one consolidated view |
| Valentina Cruz | Customer Experience | Incident volume, time to resolve, data quality of the helpdesk export |
| Andrés Kim and the tech team | CTO, engineering | Reliability, performance, security, what to build next |

### Business rules the events must respect

- Stock is never edited directly. It changes only through an inbound or an outbound order. A direct attempt is itself an event.
- Each SKU belongs to exactly one client.
- Every inventory event can be aggregated by **warehouse**, **client** and **country**.
- No end-consumer, carrier or recipient data is ever captured.

### Vocabulary used in every inventory event

{{VOCAB}}

## 2. How this plan was grounded in the real application

The catalogue is not theoretical. The API and the backoffice were read, and the inventory routes were probed, before writing this plan. What was found shapes the plan:

| Finding in the current application | Consequence for telemetry |
|---|---|
| `PUT`, `PATCH` and `DELETE` on `/inventory/products/{id}` answer 405. `POST /inventory/products` with `current_stock: 500` answers 201 and silently ignores the value. | Direct edit attempts leave no trace today. The plan asks for the second case to be rejected with 400 and counted in `direct_stock_edit_rejected`. |
| There is no configurable minimum stock per client and SKU. The backoffice flags 5 units or fewer with a fixed number. | `stock_threshold_triggered` needs a minimum-stock setting before it can fire. |
| There is no physical count or audit capability. | `inventory_discrepancy_detected` needs a stock-count flow. Reconciliation must go through an adjustment order, never a direct edit. |
| Warehouses are stored as `LA` and `ZGZ`, and the SKU has `client_name` text but no client id. | Telemetry maps `LA` to `los_angeles`, `ZGZ` to `zaragoza`, and derives `client_id` as a slug of `client_name` until a clients table exists. |
| An inbound order for `ZGZ` against a SKU registered in `LA` is accepted (201). The payload warehouse is not compared with the SKU's. | The data could contradict itself. `stock_integrity_violation_detected` exists to catch this. |
| Stock is computed per row in the list endpoints, which runs several queries per row returned. | `query_slow_detected` carries a per-request query counter to expose the pattern. |
| There is no request id and no mapping from a user to a warehouse. | The envelope adds `requestId` (created by a middleware). Until the user's warehouse is stored, the warehouse of the product is the proxy for "where it happens". |

These are prerequisites, specified in section 10. They are not hidden: an event that cannot fire today is marked as such in its notes.

## 3. Phase 1: the catalogue of data opportunities

### 3.1 Classification

- **Mandatory:** the five metrics the business has asked for by name. Their names are fixed and exact.
- **Identified opportunity:** an event this plan adds, because a decision depends on it.

### 3.2 Coverage

{{COVERAGE_TABLE}}

Coverage is deliberately broad: the inventory flow (the business core), authentication and access, incidents, navigation and abandoned flows, performance, and errors and platform health.

### 3.3 Event index

{{CATALOGUE_TABLE}}

### 3.4 Why each event exists

{{STATEMENTS}}

### 3.5 Instrumentation points in the inventory flow

The inventory flow has ten places worth instrumenting. Several are not the happy path, because the failures carry the most information: rejected direct stock edits, failed validations, threshold activations and integrity violations.

{{INVENTORY_POINTS}}

### 3.6 Instrumentation quick reference

One row per event. "Ready" says whether it can be instrumented today or waits for a prerequisite from section 10.

{{QUICKREF}}

## 4. Phase 2: the event contract

### 4.1 Naming: `entity_action`

Every `event_type` is the entity first, then a verb from a controlled list: lowercase, snake case, past tense, for example `inbound_order_created`. A new verb requires updating this table.

{{VERB_TABLE}}

Two rules keep the names honest. A refusal because of a rule is `rejected`; a refusal because of permissions is `denied`. And `created` means the record is stored, so it is emitted after the commit, never before.

### 4.2 The Event Envelope

Every event, from the browser or from the API, has the same outer shape. Only `properties` changes.

{{ENVELOPE_TABLE}}

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "outbound_order_created",
  "schemaVersion": "{{SCHEMA_VERSION}}",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": { "...": "see the event" }
}
```

### 4.3 Versioning

`schemaVersion` follows semantic versioning. Adding an optional property is a minor version. Renaming, removing or changing the type or meaning of a property is a major version, and the old version keeps being accepted for a transition period. The pipeline routes each event by `event_type` and `schemaVersion` to the schema in `event-schemas.json`.

### 4.4 Property allowlists

Every event has an allowlist: the only keys that may appear in `properties`. In `event-schemas.json` this is `additionalProperties: false` on each event's `properties` object, so an unknown key makes the event invalid and the producer drops it. Free text is never allowed: where a closed list exists, the schema uses an `enum` or a `pattern`. An allowlist is also how a developer cannot accidentally leak a field by logging a whole object.

{{ALLOWLIST_SUMMARY}}

### 4.5 Schemas, event by event

Each entry has the hypothesis and decision, when it fires, where to instrument it, the delivery mode, the personal-data treatment, every property with its type, whether it is required, and a full example. Mandatory metrics come first in each category.

{{EVENT_DETAIL}}

## 5. Aggregation: warehouse, client, country

All five mandatory metrics, and every inventory event that has a product, carry `warehouse`, `country`, `client_id`, `product_id` and `product_category`. This means a dashboard can group by warehouse, by client or by country with a plain `GROUP BY`, without joining another table. `country` is redundant on purpose: it is derived from `warehouse` (`los_angeles` is `US`, `zaragoza` is `ES`) so Thomas's view by country is one column away.

| View | Group by | Typical question |
|---|---|---|
| Warehouse capacity (Ana) | `warehouse`, hour | How much is coming in compared with last week? |
| Client health (Miguel) | `client_id` | Which brands are running low or growing? |
| Country view (Thomas) | `country` | Volume and incidents, US compared with Spain |
| Counting priority (Ana) | `product_id`, `warehouse` | Which SKUs have the highest discrepancy rate? |

## 6. Phase 3: delivery strategy

### 6.1 Stream or batch

The rule is the urgency of the decision, not the importance of the event.

- **Stream** (delivered within 60 seconds): someone can still change the outcome if they know now. A stockout alert, a failed login burst, a crashing release, a broken email provider.
- **Batch** (hourly or daily): the decision is taken over a shift, a day or a week. Batching is cheaper, can be retried safely and tolerates a client that was offline.

Nine events stream. Everything else is batched. `eventId` makes both paths idempotent, so a retried batch cannot double-count.

{{DELIVERY_TABLE}}

### 6.2 High-frequency events: throttle, debounce, sample

Some events fire constantly. Without a rule, they would outnumber everything else and hide it.

{{VOLUME_TABLE}}

General rules: threshold events are **edge-triggered** (fire when the condition becomes true, re-arm when it clears); noisy UI events are **debounced** and send only the final state; very frequent technical events are **sampled** and carry their `sample_rate` so counts can be re-weighted; duplicates are **folded** into one event with an `occurrences` count. Security events (`login_failed`) and the mandatory business metrics are never sampled.

## 7. Personal data and sensitive information

The principle is to capture the least that answers the question. Most events carry only closed lists, counts and internal identifiers.

### 7.1 Never captured

- Email addresses, names, phone numbers, postal addresses, free text of any kind (incident titles and descriptions, form fields, filter text).
- Passwords, reset tokens, API keys, session tokens, any `Authorization` header or cookie.
- Tracking numbers, carrier data, and anything about the end consumer or recipient.
- Query strings and request or response bodies. Routes are recorded as templates (`/inventory/products/{sku_id}`), because a query string can carry a reset token.
- Error messages and stack traces: they can contain values. Only the error class and a hash are kept.
- The raw IP address and the full user agent. Only a coarse device class is kept.
- File names and file contents of the incident export.

### 7.2 Captured in protected form

| Data | Protection |
|---|---|
| Account | `userId` is the internal UUID, which is pseudonymous. The mapping to a person lives only in the accounts store. |
| Email in security events | Never stored. A keyed hash is stored instead: the first 16 hex characters of `HMAC-SHA256(lowercase email, TELEMETRY_PEPPER)`. The pepper is a secret held in the environment, never in the repository, and rotating it breaks linkage on purpose. A plain hash would be reversible by trying known addresses. |
| Source network | IPv4 truncated to /24 and IPv6 to /48, then keyed-hashed like the email. Retained 30 days. |
| Account existence | `account_found` in `password_reset_requested` reveals whether an email is registered. It lives only in the restricted security dataset, never on a dashboard, never returned to a client. |

{{PII_TABLE}}

### 7.3 Legal context

Zaragoza is in the European Union, so the GDPR applies to staff data. The basis is legitimate interest in operating and securing the platform, the data is minimised as above, access to the security dataset is limited to engineering, and `userId` can be erased by deleting the account mapping. Retention: business events are kept for the life of the reporting need; security events containing hashes for 30 days; performance and error events for 90 days. These periods are proposals to confirm with the company's data-protection owner.

### 7.4 Validation at the edge

Sanitisation is enforced by the producer, not left to analysts. The producer builds each event from an allowlist, validates it against `event-schemas.json` before sending, and drops and counts any event that fails. A `pattern` or `enum` on every string means a free-text value cannot pass validation.

## 8. Risks and exclusions

### 8.1 What is deliberately not measured

| Excluded | Why |
|---|---|
| Keystrokes, mouse movement, session replay | Captures personal data by accident, and no decision depends on it. |
| Every field a user types | Same risk. `form_validation_failed` records field names only. |
| End-consumer, carrier and recipient data | Out of scope for this platform and forbidden by the briefing. |
| Successful reads of every record | Volume with no decision attached. Latency and errors cover the health question. |
| Individual employee productivity (orders per person) | The data could be used to rank people, which would turn a platform metric into surveillance and encourage gaming. Aggregation is by warehouse, not by operator. |
| Raw incident text | Free text contains names and emails. Only closed categories are kept. |

### 8.2 Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Telemetry code throws or slows a request | An outage caused by the measuring tool | Emit after the commit, never inside the transaction; send asynchronously; catch and drop on failure; never let telemetry raise. |
| Losing events (browser closed, network down, producer crash) | Undercounted metrics | Frontend uses `sendBeacon` and a small local buffer; backend events go to a queue after commit. Treat counts as accurate to about one percent and say so on dashboards. Business totals must be reconciled against the database. |
| Double counting after a retry | Inflated volumes | Idempotent by `eventId`. |
| Events emitted before the commit | A rolled-back order counted | Rule: emit only after a successful commit. |
| Schema drift between producers | Broken dashboards | One schema file, validation before sending, `schemaVersion`, and a test that fails when the Markdown and JSON disagree. |
| Alert fatigue from streamed events | Real alerts ignored | Edge-triggered thresholds, grouping and caps on streamed errors (section 6.2). |
| High-cardinality values (`product_id`, `client_id`) in a metrics store | Cost and slowness | Keep them as event dimensions in the analytics store, and do not use them as time-series labels. |
| Client clock wrong | Misordered events | `timestamp` is the producer's, but the pipeline also stamps arrival time and flags a skew larger than five minutes. |
| `client_id` derived from a free-text name | The same brand under two spellings | Derived as a slug for now; replace with a real client key when a clients table exists. |
| Re-identification through small groups | A dashboard showing one user's actions | Do not show a breakdown of fewer than five users. |
| Analysts treat telemetry as the source of truth | Wrong business numbers | Inventory truth stays in the database. Telemetry explains behaviour and trends. |
| Cost of streaming | Spending on events nobody acts on within the hour | Only events with an urgent decision stream, and the list is reviewed every quarter. |

## 9. Transport: how events travel from producer to store

This section fixes the contract between producers (API, browser) and the collector, so the two sides can be built independently. It specifies behaviour, not a product.

### 9.1 Producer rules (both API and browser)

1. Build the event from the allowlist of its `event_type`; never serialise a whole object.
2. Fill the envelope: `eventId` (new UUID v4), `timestamp` (producer clock, UTC, `Z`), `schemaVersion`, `requestId`, and `sessionId` and `userId` when known.
3. Validate against `event-schemas.json`. On failure, drop the event and increment a local counter `telemetry_dropped_total{reason="invalid"}`. Never raise.
4. Hand the event to a buffer and return immediately. Telemetry must add no more than 1 ms to a request and never run inside a database transaction.
5. For business events, emit only after the commit succeeded.

### 9.2 Collector endpoint

| Item | Contract |
|---|---|
| Route | `POST /telemetry/events` on the API. A separate path from business routes so it can be rate-limited and switched off on its own. |
| Body | `{"events": [<envelope>, ...]}`, JSON, at most 100 events and 256 KB per request. |
| Authentication | Browser: the user's normal bearer token (events carry the real `userId`; the collector overwrites `userId` with the token's subject, so a client cannot impersonate another user). Before sign-in (failed login, login page views): no token, `userId` is `null`, and the route accepts only the events marked `frontend` that are allowed anonymously (`page_viewed`, `frontend_error_caught`, `network_request_failed`, `page_load_recorded`, `flow_started`, `flow_abandoned`, `form_validation_failed`), with a stricter rate limit. Backend events do not use HTTP: the API writes them directly to the queue in-process. |
| CORS | Same origin as the backoffice (the existing `/backend` proxy), so no cross-origin allowance is added. |
| Rate limit | 60 requests per minute per user, 20 per minute per network for anonymous calls. Over the limit: `429` with `Retry-After`; the producer keeps events in its buffer. |
| Validation | Each event is validated on its own against its schema (see section 13). The collector replies `200` with `{"received": N, "stored": M, "rejected": R}`; the reasons are logged on the server, never echoed. A rejected event is never retried (it would fail again). |
| Server-set fields | The collector adds `receivedAt` (arrival time) outside the envelope in storage. It flags any event whose `timestamp` differs from `receivedAt` by more than 5 minutes. |
| Failures | `5xx` and network errors are retried by the producer; `4xx` other than `429` are not. |

### 9.3 Browser producer

- **Buffer:** in memory, flushed every 10 s, when it reaches 20 events, and on `visibilitychange` to hidden using `navigator.sendBeacon` (which cannot carry an `Authorization` header, so the beacon path uses a short-lived telemetry token issued at sign-in).
- **Streamed events** (`frontend_error_caught`) are flushed immediately instead of waiting for the timer.
- **Offline:** keep at most 200 events (oldest dropped first) and retry with exponential backoff, starting at 2 s and capped at 5 minutes, with jitter.
- **`requestId`:** created per user action in the browser and sent as the `X-Request-ID` header on the API call it triggers, so frontend and backend events of one action join.
- **`sessionId`:** created at sign-in, kept in `sessionStorage` (never in `localStorage`, so it dies with the tab), discarded at sign-out.
- **Opt-out:** if `navigator.doNotTrack` is `1`, only the events required for security and reliability (`login_failed` is server-side anyway, `frontend_error_caught`, `network_request_failed`) are sent.

### 9.4 Backend producer

- A middleware assigns `requestId` (reuses a valid `X-Request-ID`, otherwise creates one), stores it in request context and returns it in the `X-Request-ID` response header.
- Events are pushed to an in-process queue after the commit. A worker flushes them: **stream** events within 5 s, **batch** events every 60 s or 500 events, whichever comes first. The delivery label of each event in section 6 is the target for the analytics store, not for this local flush.
- If the queue is full (10,000 events), drop `api_latency_recorded` first, then other `batch` events; never drop a mandatory metric or a `stream` security event.

### 9.5 Storage and retention

- **Hot store (stream):** queryable within 60 s, 7 days of retention, used for alerts and the live operations view.
- **Warehouse (batch and stream):** one table per `event_type` (the allowlist makes columns known), partitioned by day. Retention: business events for the life of the reporting need, security events containing hashes 30 days, performance and error events 90 days.
- **Access:** the security dataset (`login_failed`, `password_reset_requested`, `access_denied`, `user_role_changed`) is readable only by engineering. Dashboards read the other tables.

## 10. Prerequisites to make the plan implementable

These are gaps in the application that the plan depends on. Each one is specified here precisely enough to be built, because three of the five mandatory metrics cannot fire without them. The gaps themselves are outside this design-only deliverable.

| # | Prerequisite | Required behaviour | Unlocks |
|---|---|---|---|
| 1 | Minimum stock per client and SKU | New column `SKU.min_stock` (integer, default 0, set at product creation and changed only by an admin or manager through `PATCH /inventory/products/{id}/min-stock`). The comparison runs after every outbound commit using stock before and after. 0 means the rule is off for that SKU. | `stock_threshold_triggered` |
| 2 | Stock-count flow | New `StockCount` record: `count_id`, `warehouse`, `product`, `counted_quantity`, `system_quantity` (frozen when the count is saved), `detection_source`, `counted_by`. When `counted_quantity` differs, create an adjustment inbound (surplus) or outbound of type `loss` (shortage) that references `count_id`, so stock still changes only through an order. | `inventory_discrepancy_detected` |
| 3 | Reject stock fields in payloads | `POST /inventory/products` and any inventory body containing `current_stock`, `stock` or `quantity_on_hand` answers `400` with `{"error": "stock_is_read_only"}`. The 405 on `PUT`, `PATCH` and `DELETE` of product routes stays. | `direct_stock_edit_rejected` (channel `stock_field_in_payload`) |
| 4 | Warehouse consistency check | An order whose `warehouse` differs from its SKU's warehouse answers `400` with `{"error": "warehouse_mismatch"}`. A periodic scan also reports negative stock and orphan movements. | `stock_integrity_violation_detected` |
| 5 | Client id and user warehouse | `SKU.client_id` (foreign key to a clients table; until then the slug of `client_name`), and `User.warehouse` (nullable). When `User.warehouse` exists, `direct_stock_edit_rejected.warehouse` uses the user's warehouse instead of the product's. | All inventory events, `client_id` quality |
| 6 | Request middleware | Assigns and propagates `X-Request-ID` as in section 9.4. | `requestId` in every event |
| 7 | `TELEMETRY_PEPPER` | A random secret of at least 32 bytes, read from the environment, listed in `.env.example` without a value, never committed. | `email_hash`, `source_network_hash` |
| 8 | Collector, queue and store | As specified in section 9. | Everything |

Until prerequisites 1, 2 and 5 exist, the events they unlock are defined but cannot fire. Every other event can be instrumented today.

## 11. Maintaining this document

`telemetry-plan.md` and `event-schemas.json` are generated from one source, `events.py`, so they cannot disagree. After changing an event:

```powershell
python docs/telemetry/build.py
python -m pytest packages/shared/tests/test_telemetry_docs.py
```

The test validates every example against its schema, checks that the five mandatory metrics are present and exact, that every event has a hypothesis and a decision, and that the committed files are up to date. The build also writes `uis/backoffice/lib/telemetry-schema.generated.ts`, the allowlists the browser checks every event against before queueing it, and `services/telemetry_catalogue.py`, the same allowlists plus the storage mapping for the collector. To add an event: add it to `events.py` with its hypothesis, decision, allowlist, personal-data note, delivery mode and example, run the build, and review the diff.

## 12. Phase 2 implementation status

Phase 2 builds the capture half in the backoffice and a stub receiver in the API. The stub, `POST /telemetry/events`, validates the envelope with the `TelemetryEvent` model, logs the batch, answers `200` with `{"received": N}` and stores nothing. It is open (no authentication) because `sendBeacon` cannot carry an `Authorization` header. Phase 3 (section 13) replaced the stub with the persistent collector without touching the browser, because the browser reads the address from `NEXT_PUBLIC_TELEMETRY_ENDPOINT` and only looks at the HTTP status. Authentication and rate limits remain to be added.

Every event passes through one function, `track(eventType, properties)` in `uis/backoffice/lib/telemetry.ts`. It adds the envelope, validates the event against the generated allowlist (an unknown event, a missing required property or an unknown property drops the event), queues it, and sends batches every 10 seconds or at 20 events, with `sendBeacon` when the tab is hidden and up to three retries with exponential waiting.

Until the backend producer exists, the backoffice also produces the backend-sourced events whose moment it can observe from an API response. When Phase 3 moves a producer to the server, remove that event from the browser, because the two producers would count it twice (each has its own `eventId`).

{{FRONTEND_STATUS}}

## 13. Phase 3: storage in Supabase

The collector at `POST /telemetry/events` validates every event of a batch on its own and stores the valid ones with a single bulk `INSERT`. The browser did not change.

### 13.1 How a batch is handled

1. The body is parsed loosely: `{"events": [...]}` where each item is a raw object. It is deliberately not typed as `list[TelemetryEvent]`, because a typed body would answer `422` for the whole batch when one event is wrong.
2. Each item is checked inside the handler: it must be an object, pass the `TelemetryEvent` envelope model, name an event of this plan, carry the same major `schemaVersion` as the plan, and have properties inside that event's allowlist (no unknown key, every required key present, every value inside its `enum`, `pattern` and bounds).
3. Valid events become rows and are inserted together: one statement, one transaction, whatever the batch size.
4. The answer is `200` with `{"received": N, "stored": M, "rejected": R}`. A request is refused as a whole (`422`) only when the envelope itself is unusable: no `events` array, or more than 100 events. A database failure answers `503`, which the browser retries.

Rejected events are counted and logged server-side as position and reason code only (`unknown_event_type`, `invalid_envelope`, `unknown_property`, `missing_property:<name>`, `invalid_value:<name>`, `unsupported_schema_version`, `not_an_object`), never with the values, which may be personal.

### 13.2 The table

`telemetry_events` is append-only: no code updates or deletes it, and on PostgreSQL a trigger refuses both. Row level security is enabled with no policy, so Supabase's public API cannot read or write it; the API connects with a role that bypasses it.

| Column | Type | Source |
|---|---|---|
| `id` | `uuid` primary key, default `gen_random_uuid()` | generated |
| `timestamp` | `timestamptz` not null | `event.timestamp`, the moment of capture in the browser (UTC) |
| `service` | `text` not null | `backoffice`: the browser collector is the only producer in this phase. Events written by the API itself will use `api` |
| `event_type` | `text` not null | `event.event_type` |
| `level` | `text` not null, default `info`, one of `info`, `warn`, `error` | by event, see 13.3 |
| `value` | `numeric` | the numeric property named in 13.3, or null |
| `message` | `text` | the event's description in this plan (static text, no user data) |
| `tags` | `jsonb` not null, default `{}` | `event.properties` (allowlist keys only) plus the reserved key `_envelope` |

Indexes: `timestamp` (B-tree), `event_type` (B-tree) and `tags` (GIN, for containment searches such as `tags @> '{"warehouse": "zaragoza"}'`). The SQL for Supabase is in `services/sql/telemetry_events.sql`; the API also creates the same table on startup.

### 13.3 Level, value and message per event

{{STORAGE_MAPPING}}

### 13.4 What `tags` holds

`tags` is the event's `properties` object, key for key, so the dimensions the plan requires (`warehouse`, `country`, `client_id`, `product_id`, `product_category`, `quantity` and the rest of each allowlist) are searchable without a join. The envelope fields that analytics needs are kept under one reserved key, `_envelope`: `eventId`, `sessionId`, `userId`, `schemaVersion` and `requestId`. No allowlisted property starts with an underscore, so the two never collide.

```json
{
  "warehouse": "los_angeles", "country": "US", "client_id": "fashion-co", "product_id": "FSH-TEE-M",
  "product_category": "fashion", "order_id": "2210", "exit_type": "dispatch", "quantity": 3, "stock_after": 47,
  "_envelope": {
    "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21", "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
    "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629", "schemaVersion": "{{SCHEMA_VERSION}}",
    "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84"
  }
}
```

### 13.5 Known limits

- **No de-duplication yet.** The browser can send an event twice (a batch handed to `sendBeacon` while it was mid-retry). The `eventId` is stored in `tags._envelope`, but enforcing uniqueness would need a fourth index beyond the three the tech lead specified, so analytics should count distinct `tags->'_envelope'->>'eventId'` until it is added.
- **No authentication or rate limit** on the endpoint (section 9.2).
- **`service` is a constant** while the browser is the only producer.
- **`timestamp` is the browser's clock.** A wrong clock misplaces the event; the arrival time is not stored.

## 14. Phase 4: the operational report

A technical radar for the engineering team, calculated from the rows of `telemetry_events`: how healthy the system is, how fast it answers, where it fails and which events dominate. It is not a business report: no conversion, no revenue. Those belong to the data pipelines that come later.

### 14.1 The pipeline

`services/telemetry/analysis.py` has one independent function per operational question. Each takes the period (`start_date` inclusive, `end_date` exclusive, UTC) and follows the same order:

1. **Load (SQL):** only the columns and event types the metric needs, filtered by `timestamp >= start AND timestamp < end` and, when it needs specific types, `event_type IN (...)` in the query. Never the whole table, and a row cap as a safety net.
2. **Refine (Pandas):** extract the `tags` dimensions the metric needs, drop rows whose dimension is null.
3. **Convert types:** `pd.to_datetime(..., utc=True)` before any grouping. Grouping date-looking strings gives wrong groups with no error.
4. **Group and aggregate:** `groupby` with `size`, `sum`, `mean`, `quantile`. No loops.
5. **Serve:** a list of plain dicts (`NaN` becomes `null`, days are `YYYY-MM-DD`).

Every metric has a grouping dimension (day, event type, route, section). None is a global number.

| Key in the report | Question | Grouped by | Events it reads |
|---|---|---|---|
| `events_per_day` | How much telemetry does the system produce, and of what kind? | day, event type | all |
| `error_rate_by_day` | Is the system getting worse? | day | all (`level`) |
| `error_rate_by_type` | Which failures dominate? | event type, level | all (`level` warn or error) |
| `page_load_latency_by_day` | How fast do screens load? | day, section | `page_load_recorded` (`ttfb_ms`, `lcp_ms`) |
| `network_failures_by_route` | Where does the browser lose the API? | day, API route, failure kind | `network_request_failed` |
| `frontend_errors_by_route` | Which screens crash? | day, route, error class | `frontend_error_caught` |
| `auth_failure_rate` | Can people sign in? | day | `login_attempt_failed`, `login_succeeded` |

`auth_failure_rate` is the daily share `login_attempt_failed / (login_attempt_failed + login_succeeded)`. The browser produces both events in this phase, so `login_succeeded.is_first_login`, which only the server can know, became optional in schema 1.2.0.

### 14.2 The endpoint

`GET /telemetry/report?start_date=...&end_date=...` (signed-in users only):

- Both parameters are ISO 8601 and optional. A date without a zone is UTC. With none, the period is the last 7 days (`now - 7d` to `now`). Only `end_date` gives the 7 days before it; only `start_date` runs until now. A reversed or empty period, or one longer than 90 days, is a `422`.
- The period is resolved once and passed to every metric function; the functions never apply a default window of their own.
- The answer is `{"period": {"from", "to"}, "metrics": {<key>: [...]}}`.
- The result is cached in memory for 60 seconds per period, so the pipeline does not run on every request. Requests without parameters share one entry. The header `X-Cache` says `HIT` or `MISS`.

### 14.3 Limits

- The cache is per process: with several API workers each keeps its own, and a restart empties it.
- Events can be duplicated (section 13.5), which slightly inflates counts; the report does not de-duplicate yet.
- Any signed-in user can read the report. Restricting it to an engineering role needs a role for that, which the accounts do not have.
- `page_load_latency_by_day` reports time to first byte, which every load has. Largest paint is only measured in some browsers and loads, and is `null` when absent.
