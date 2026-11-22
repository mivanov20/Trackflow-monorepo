<!-- GENERATED FILE. Do not edit telemetry-plan.md by hand: edit plan.template.md or events.py, then run `python docs/telemetry/build.py`. -->

# TrackFlow Telemetry Plan

Version {{SCHEMA_VERSION}} · {{EVENT_COUNT}} events · companion file: [`event-schemas.json`](event-schemas.json)

This is a design document. It describes what TrackFlow should measure, why, and exactly how each event looks. It contains no instrumentation code. Another developer should be able to instrument every event from this document and the JSON file alone.

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

These are prerequisites, listed again in section 9. They are not hidden: an event that cannot fire today is marked as such in its notes.

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

## 9. Prerequisites to make the plan implementable

These are gaps in the application that this plan depends on. They are not part of this deliverable, which is design only.

1. A configurable minimum stock per client and SKU (for `stock_threshold_triggered`).
2. A stock-count or audit flow that reconciles through adjustment orders (for `inventory_discrepancy_detected`).
3. Reject stock fields in product payloads with 400 instead of dropping them silently (for `direct_stock_edit_rejected`).
4. Validate the order warehouse against the SKU's warehouse (the integrity rule behind `stock_integrity_violation_detected`).
5. A `client_id` on SKU, and a warehouse on the user, replacing the slug and the product warehouse proxy.
6. A request middleware that assigns and propagates `X-Request-ID`.
7. A `TELEMETRY_PEPPER` secret, supplied by the environment.
8. A collector endpoint and a queue, with a place to store events. The plan fixes the contract, not the product.

## 10. Maintaining this document

`telemetry-plan.md` and `event-schemas.json` are generated from one source, `events.py`, so they cannot disagree. After changing an event:

```powershell
python docs/telemetry/build.py
python -m pytest packages/shared/tests/test_telemetry_docs.py
```

The test validates every example against its schema, checks that the five mandatory metrics are present and exact, that every event has a hypothesis and a decision, and that the committed files are up to date. To add an event: add it to `events.py` with its hypothesis, decision, allowlist, personal-data note, delivery mode and example, run the build, and review the diff.
