<!-- GENERATED FILE. Do not edit telemetry-plan.md by hand: edit plan.template.md or events.py, then run `python docs/telemetry/build.py`. -->

# TrackFlow Telemetry Plan

Version 1.1.0 · 38 events · companion file: [`event-schemas.json`](event-schemas.json)

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

- Warehouses: `los_angeles`, `zaragoza` (country `US` and `ES`).
- Product categories: `fashion`, `electronics`, `cosmetics`.
- Roles: `admin`, `manager`, `user`.

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

| Category | Events | Mandatory | Opportunities | Stream | Batch |
|---|---|---|---|---|---|
| Inventory (business) | 9 | 5 | 4 | 3 | 6 |
| Authentication and access | 12 | 0 | 12 | 1 | 11 |
| Incident management (business) | 4 | 0 | 4 | 0 | 4 |
| Navigation and engagement | 5 | 0 | 5 | 0 | 5 |
| Performance | 3 | 0 | 3 | 1 | 2 |
| Errors and platform health | 5 | 0 | 5 | 4 | 1 |
| **Total** | **38** | **5** | **33** | **9** | **29** |

Coverage is deliberately broad: the inventory flow (the business core), authentication and access, incidents, navigation and abandoned flows, performance, and errors and platform health.

### 3.3 Event index

| # | Event | Class | Category | Delivery | Persona or owner |
|---|---|---|---|---|---|
| 1 | `inbound_order_created` | Mandatory | Inventory (business) | batch (hourly) | Ana Whitfield (Warehouse Operations) |
| 2 | `outbound_order_created` | Mandatory | Inventory (business) | stream (within 60 seconds) | Ana Whitfield (Warehouse Operations) |
| 3 | `stock_threshold_triggered` | Mandatory | Inventory (business) | stream (within 60 seconds) | Miguel Torres (Commercial and Client Relations) |
| 4 | `direct_stock_edit_rejected` | Mandatory | Inventory (business) | batch (hourly) | Ana Whitfield (Warehouse Operations) |
| 5 | `inventory_discrepancy_detected` | Mandatory | Inventory (business) | batch (daily) | Ana Whitfield (Warehouse Operations) |
| 6 | `inbound_order_rejected` | Identified opportunity | Inventory (business) | batch (hourly) | Ana Whitfield (Warehouse Operations) |
| 7 | `outbound_order_rejected` | Identified opportunity | Inventory (business) | batch (hourly) | Ana Whitfield (Warehouse Operations) |
| 8 | `product_created` | Identified opportunity | Inventory (business) | batch (daily) | Miguel Torres (Commercial and Client Relations) |
| 9 | `stock_integrity_violation_detected` | Identified opportunity | Inventory (business) | stream (within 60 seconds) | TrackFlow Tech (engineering) |
| 10 | `login_succeeded` | Identified opportunity | Authentication and access | batch (hourly) | Ana Whitfield |
| 11 | `login_failed` | Identified opportunity | Authentication and access | stream (within 60 seconds) | TrackFlow Tech (security) |
| 12 | `login_attempt_failed` | Identified opportunity | Authentication and access | batch (hourly) | TrackFlow Tech |
| 13 | `session_expired` | Identified opportunity | Authentication and access | batch (hourly) | TrackFlow Tech |
| 14 | `user_registered` | Identified opportunity | Authentication and access | batch (daily) | TrackFlow Tech |
| 15 | `password_reset_requested` | Identified opportunity | Authentication and access | batch (hourly) | TrackFlow Tech (security) |
| 16 | `password_reset_completed` | Identified opportunity | Authentication and access | batch (daily) | TrackFlow Tech |
| 17 | `password_reset_rejected` | Identified opportunity | Authentication and access | batch (daily) | TrackFlow Tech |
| 18 | `password_changed` | Identified opportunity | Authentication and access | batch (daily) | TrackFlow Tech (security) |
| 19 | `password_change_rejected` | Identified opportunity | Authentication and access | batch (daily) | TrackFlow Tech (security) |
| 20 | `access_denied` | Identified opportunity | Authentication and access | batch (hourly) | TrackFlow Tech (security) |
| 21 | `user_role_changed` | Identified opportunity | Authentication and access | batch (daily) | TrackFlow Tech (security) |
| 22 | `incident_created` | Identified opportunity | Incident management (business) | batch (hourly) | Valentina Cruz (Customer Experience) |
| 23 | `incident_status_changed` | Identified opportunity | Incident management (business) | batch (hourly) | Valentina Cruz (Customer Experience) |
| 24 | `incident_file_analyzed` | Identified opportunity | Incident management (business) | batch (daily) | Valentina Cruz (Customer Experience) |
| 25 | `incident_file_rejected` | Identified opportunity | Incident management (business) | batch (daily) | Valentina Cruz (Customer Experience) |
| 26 | `page_viewed` | Identified opportunity | Navigation and engagement | batch (hourly) | TrackFlow Tech |
| 27 | `flow_started` | Identified opportunity | Navigation and engagement | batch (hourly) | TrackFlow Tech |
| 28 | `flow_abandoned` | Identified opportunity | Navigation and engagement | batch (hourly) | TrackFlow Tech |
| 29 | `filter_applied` | Identified opportunity | Navigation and engagement | batch (daily) | Valentina Cruz (Customer Experience) |
| 30 | `form_validation_failed` | Identified opportunity | Navigation and engagement | batch (daily) | TrackFlow Tech |
| 31 | `api_latency_recorded` | Identified opportunity | Performance | stream (within 60 seconds) | TrackFlow Tech |
| 32 | `page_load_recorded` | Identified opportunity | Performance | batch (hourly) | TrackFlow Tech |
| 33 | `query_slow_detected` | Identified opportunity | Performance | batch (daily) | TrackFlow Tech |
| 34 | `frontend_error_caught` | Identified opportunity | Errors and platform health | stream (within 60 seconds) | TrackFlow Tech |
| 35 | `api_error_returned` | Identified opportunity | Errors and platform health | stream (within 60 seconds) | TrackFlow Tech |
| 36 | `network_request_failed` | Identified opportunity | Errors and platform health | batch (hourly) | Ana Whitfield |
| 37 | `email_delivery_failed` | Identified opportunity | Errors and platform health | stream (within 60 seconds) | TrackFlow Tech |
| 38 | `service_started` | Identified opportunity | Errors and platform health | stream (within 60 seconds) | TrackFlow Tech |

### 3.4 Why each event exists

#### Inventory (business)

- **Mandatory.** We capture `inbound_order_created` because we need to know `how much volume comes in, by client and by warehouse`, which allows us to make the decision `plan warehouse capacity and staffing based on incoming volume (Ana)`.
- **Mandatory.** We capture `outbound_order_created` because we need to know `how many orders are processed, by client and warehouse, and at what rate`, which allows us to make the decision `detect operational bottlenecks before they affect the delivery SLA (Ana)`.
- **Mandatory.** We capture `stock_threshold_triggered` because we need to know `how often a client runs out of available stock for a SKU`, which allows us to make the decision `alert the client and the commercial team before a stockout (Miguel)`.
- **Mandatory.** We capture `direct_stock_edit_rejected` because we need to know `if warehouse staff are attempting to bypass the traceability controls`, which allows us to make the decision `reinforce training or permissions at the warehouse where this happens most`.
- **Mandatory.** We capture `inventory_discrepancy_detected` because we need to know `which SKUs and warehouses see the most discrepancies`, which allows us to make the decision `prioritise inventory audits on the SKUs with the highest discrepancy rate (Ana)`.
- Opportunity. We capture `inbound_order_rejected` because we need to know `which SKUs and warehouses accumulate failed receipts, and why`, which allows us to make the decision `fix the SKU master data or retrain the receiving staff on the SKUs that fail most`.
- Opportunity. We capture `outbound_order_rejected` because we need to know `which products block dispatch because the stock the system shows cannot cover the order, or because of data entry mistakes`, which allows us to make the decision `order a recount for the products that repeatedly block dispatch, and fix the data entry problems behind the rest`.
- Opportunity. We capture `product_created` because we need to know `how fast each client's catalogue grows, and in which warehouse and category`, which allows us to make the decision `plan shelf space per client and follow up with clients whose catalogue has stopped growing (Miguel)`.
- Opportunity. We capture `stock_integrity_violation_detected` because we need to know `whether the rules the business relies on (stock never negative, movement in the SKU's warehouse) are being broken by a bug or by bad data`, which allows us to make the decision `stop the line: block the affected flow and repair the data before more movements pile on top of it`.

#### Authentication and access

- Opportunity. We capture `login_succeeded` because we need to know `how many operators sign in per hour and per day, and how often they need several attempts`, which allows us to make the decision `size support coverage and shift schedules, and tune the lockout and rate limits with real data`.
- Opportunity. We capture `login_failed` because we need to know `whether failed sign-ins are forgotten passwords or an attack (many attempts, or many different accounts)`, which allows us to make the decision `enable rate limiting or account lockout, block abusive sources, or force password resets`.
- Opportunity. We capture `login_attempt_failed` because we need to know `whether people fail to sign in because the credentials are wrong or because the service cannot be reached, as the user experiences it`, which allows us to make the decision `put effort into the password-help path when wrong credentials dominate, and escalate to engineering when network or service failures dominate`.
- Opportunity. We capture `session_expired` because we need to know `whether the 8-hour session matches real shift length, and how often operators are cut off mid-task`, which allows us to make the decision `change the session lifetime, add silent renewal, or warn the user before it ends`.
- Opportunity. We capture `user_registered` because we need to know `how many accounts are created, how much optional profile data people give, and whether the legacy register route is still used`, which allows us to make the decision `simplify or extend the sign-up form, and retire `/auth/register` once nobody uses it`.
- Opportunity. We capture `password_reset_requested` because we need to know `how many people forget their password, and whether unknown addresses are being probed`, which allows us to make the decision `review the password policy or add single sign-on, and spot account-enumeration attempts`.
- Opportunity. We capture `password_reset_completed` because we need to know `how many requested resets are completed and how long people take`, which allows us to make the decision `adjust the 30-minute link lifetime and the email wording, and judge email deliverability`.
- Opportunity. We capture `password_reset_rejected` because we need to know `why reset links fail, and whether the link lifetime is too short`, which allows us to make the decision `lengthen or shorten the link lifetime, or make the 'request a new link' path easier to find`.
- Opportunity. We capture `password_changed` because we need to know `how often people rotate their password on their own`, which allows us to make the decision `decide whether to promote or enforce periodic password changes`.
- Opportunity. We capture `password_change_rejected` because we need to know `whether users struggle with the password rules, or someone with a stolen session is guessing the current password`, which allows us to make the decision `improve the password hints in the form, and investigate accounts with many wrong current passwords`.
- Opportunity. We capture `access_denied` because we need to know `whether users probe accounts or administrator functions they should not reach`, which allows us to make the decision `review roles and permissions, and investigate users who keep trying`.
- Opportunity. We capture `user_role_changed` because we need to know `who gains or loses privileges, and when`, which allows us to make the decision `audit privileged access every quarter and spot unexpected promotions`.

#### Incident management (business)

- Opportunity. We capture `incident_created` because we need to know `how many incidents are reported, by category, origin and branch`, which allows us to make the decision `staff customer-experience coverage by branch and prioritise root-cause work on the most frequent categories (Valentina)`.
- Opportunity. We capture `incident_status_changed` because we need to know `how long incidents take to move through their lifecycle and where they stall`, which allows us to make the decision `escalate or rebalance staffing when the time to resolve grows (Valentina)`.
- Opportunity. We capture `incident_file_analyzed` because we need to know `how often the helpdesk export is analysed, how clean it is, and how satisfaction evolves`, which allows us to make the decision `agree data-quality fixes with the team that owns the helpdesk export, and track the satisfaction trend (Valentina)`.
- Opportunity. We capture `incident_file_rejected` because we need to know `why uploads fail, and whether the size limit or the export instructions are the problem`, which allows us to make the decision `improve the export instructions, or raise the upload limit if large files are the cause`.

#### Navigation and engagement

- Opportunity. We capture `page_viewed` because we need to know `which sections operators actually use, and in what order`, which allows us to make the decision `reorder the menu, retire sections nobody opens, and put engineering time into the most visited screens`.
- Opportunity. We capture `flow_started` because we need to know `how many times each guided task is started; this is the denominator of the abandonment rate`, which allows us to make the decision `choose which flow to simplify first`.
- Opportunity. We capture `flow_abandoned` because we need to know `which flows are left unfinished and at which step`, which allows us to make the decision `redesign the step where people drop out`.
- Opportunity. We capture `filter_applied` because we need to know `which filters operators use and how narrow the results get`, which allows us to make the decision `set sensible default filters and add saved views for the most common ones`.
- Opportunity. We capture `form_validation_failed` because we need to know `which fields cause the most mistakes`, which allows us to make the decision `reword labels, add hints, or relax constraints on those fields`.

#### Performance

- Opportunity. We capture `api_latency_recorded` because we need to know `whether the API slows down during shifts and which routes are slow`, which allows us to make the decision `optimise or scale the slow routes, and escalate when the 95th percentile breaches the target`.
- Opportunity. We capture `page_load_recorded` because we need to know `how fast pages load on the devices and connections used in each warehouse`, which allows us to make the decision `prioritise frontend optimisation, and decide whether warehouse tablets on slow connections need a lighter interface`.
- Opportunity. We capture `query_slow_detected` because we need to know `which statements dominate response time (the list endpoints run several queries per row today)`, which allows us to make the decision `add indexes, pagination or joins to the worst offenders`.

#### Errors and platform health

- Opportunity. We capture `frontend_error_caught` because we need to know `whether a release introduced errors, and which screens crash`, which allows us to make the decision `roll back or hotfix, and prioritise the screens that crash most`.
- Opportunity. We capture `api_error_returned` because we need to know `how often and where the API fails with server errors`, which allows us to make the decision `page the engineer on duty and open an incident for the failing route`.
- Opportunity. We capture `network_request_failed` because we need to know `whether operators lose connectivity, and at which times`, which allows us to make the decision `invest in the network at the affected warehouse, and add an offline queue for orders if it is frequent`.
- Opportunity. We capture `email_delivery_failed` because we need to know `whether password-reset emails are failing to leave the system`, which allows us to make the decision `fix the provider key or its configuration, or switch provider, before people are locked out`.
- Opportunity. We capture `service_started` because we need to know `how often services restart and with which configuration`, which allows us to make the decision `detect crash loops and configuration drift between environments`.

### 3.5 Instrumentation points in the inventory flow

The inventory flow has ten places worth instrumenting. Several are not the happy path, because the failures carry the most information: rejected direct stock edits, failed validations, threshold activations and integrity violations.

| # | Instrumentation point | Event | Why there |
|---|---|---|---|
| 1 | `create_stock_entry()` after commit | `inbound_order_created` | Count a receipt only once it is stored; a rolled-back receipt must not inflate volume. |
| 2 | `create_stock_exit()` after commit | `outbound_order_created` | Same rule for dispatches and losses; also the moment the new stock level is known. |
| 3 | `create_stock_exit()` after the movement, comparing stock before and after | `stock_threshold_triggered` | The only place where a crossing of the minimum is observable; edge-triggered. |
| 4 | Handler of the 405 on `PUT`, `PATCH`, `DELETE /inventory/products/{sku_id}` | `direct_stock_edit_rejected` | Direct edit attempt on the product routes: today refused but invisible. |
| 5 | Payload guard in `create_product()` (stock fields in the body) | `direct_stock_edit_rejected` | Today the stock value is silently dropped (201). Rejecting it with 400 makes the attempt visible. |
| 6 | `_sku_or_404()` and the 422 handler of the inbound route | `inbound_order_rejected` | Failed validations on receipts, by SKU and warehouse. |
| 7 | The 400, 404 and 422 paths of `create_stock_exit()` | `outbound_order_rejected` | Failed validations on dispatches, including insufficient stock. |
| 8 | Warehouse-versus-SKU check and negative-stock check in the movement routes, plus a periodic scan | `stock_integrity_violation_detected` | Protect the invariants the stock computation relies on. |
| 9 | New stock-count flow (to be built) | `inventory_discrepancy_detected` | Compare physical count with system stock; reconcile through an adjustment order. |
| 10 | `create_product()` after commit | `product_created` | Catalogue growth per client, warehouse and category. |

### 3.6 Instrumentation quick reference

One row per event. "Ready" says whether it can be instrumented today or waits for a prerequisite from section 10.

| Event | Produced by | Instrument at | Fires when | Mode | Ready |
|---|---|---|---|---|---|
| `inbound_order_created` | backend | `services/routers/inventory.py` -> `create_stock_entry()`, right after `commit_session()` succeeds. Never before the commit: a rolled-back receipt must not be counted. | A goods receipt (StockEntry) was saved successfully. | batch | Yes |
| `outbound_order_created` | backend | `services/routers/inventory.py` -> `create_stock_exit()`, right after `commit_session()` succeeds. The stock-threshold check runs right after (see `stock_threshold_triggered`). | A stock exit (StockExit) of type `dispatch` or `loss` was saved successfully. | stream | Yes |
| `stock_threshold_triggered` | backend | `services/routers/inventory.py` -> `create_stock_exit()`, after `outbound_order_created`. Compare stock before and after the movement against the SKU's minimum. | After an outbound order, the SKU's stock crossed from at or above its minimum to below it (edge-triggered), or reached 0. | stream | After prerequisite 1 |
| `direct_stock_edit_rejected` | backend | `services/routers/inventory.py`: a handler for the `405` of the product routes (a), and a payload guard in `create_product()` (b). | A request that would change stock without an order is refused. Two detection points: (a) `PUT`, `PATCH` or `DELETE` on `/inventory/products/{sku_id}`; (b) a stock value (`current_stock`, `stock`, `quantity_on_hand`) inside any inventory payload. | batch | Partly: the `405` channel now, the payload channel after prerequisite 3 |
| `inventory_discrepancy_detected` | backend | A new stock-count flow (to be built). The reconciliation must still go through an adjustment inbound or outbound order that references `count_id`, so stock is never edited directly. | A stock count is saved and the counted units differ from the system stock. | batch | After prerequisite 2 |
| `inbound_order_rejected` | backend | `services/routers/inventory.py` (the 404 of `_sku_or_404()`) and a validation-error handler for the 422 of the inbound route. | `POST /inventory/orders/inbound` answers 400, 404 or 422. | batch | Yes |
| `outbound_order_rejected` | backend | `services/routers/inventory.py` -> `create_stock_exit()` (the 400 'Insufficient stock' and the 404) and a validation-error handler for the 422. | `POST /inventory/orders/outbound` answers 400, 404 or 422. | batch | Yes |
| `product_created` | backend | `services/routers/inventory.py` -> `create_product()`, after `commit_session()`. | `POST /inventory/products` succeeded. | batch | Yes |
| `stock_integrity_violation_detected` | backend | `services/routers/inventory.py` -> `create_stock_entry()` and `create_stock_exit()` (request-time check) plus a periodic scan job (to be built). | A check finds computed stock below zero, a movement whose warehouse differs from its SKU's warehouse, or a movement whose SKU no longer exists. | stream | After prerequisite 4 |
| `login_succeeded` | backend | `services/auth.py` -> `login()` after `authenticate()` returns. | `POST /auth/login` (or `/auth/token`) issued a token. | batch | Yes |
| `login_failed` | backend | `services/auth.py` -> `authenticate()`, at each of its three refusals (unknown account, wrong password, disabled account). | `POST /auth/login` (or `/auth/token`) answered 401 or 403. | stream | Yes |
| `login_attempt_failed` | frontend | `uis/backoffice/lib/auth.ts` -> `signIn()`, in the failure branch. It is an authentication hook, so no page tracks it. | The sign-in form submitted and `signIn()` threw. | batch | Yes |
| `session_expired` | frontend | `uis/backoffice/lib/api-client.ts` -> `apiRequest()`, in the 401 branch before `redirectToLogin()`. | A protected call answered 401 and the session was cleared. | batch | Yes |
| `user_registered` | backend | `services/routers/users.py` -> `register_user()` and `services/auth.py` -> `register_alias()`, after the account is stored. | `POST /users` or the deprecated `POST /auth/register` created an account. | batch | Yes |
| `password_reset_requested` | backend | `services/auth.py` -> `forgot_password()`, after the account lookup. | `POST /auth/forgot-password` was received (it always answers 200). | batch | Yes |
| `password_reset_completed` | backend | `services/auth.py` -> `reset_password()`, after `complete_password_reset()` succeeds. | `POST /auth/reset-password` accepted the link and changed the password. | batch | Yes |
| `password_reset_rejected` | backend | `services/auth.py` -> `reset_password()`, at its two refusals. | `POST /auth/reset-password` answered 400. | batch | Yes |
| `password_changed` | backend | `services/auth.py` -> `change_password()`, after `update_user()`. | `POST /auth/change-password` succeeded. | batch | Yes |
| `password_change_rejected` | backend | `services/auth.py` -> `change_password()`, at its refusals. | `POST /auth/change-password` answered 400 or 422. | batch | Yes |
| `access_denied` | backend | `services/auth.py` -> `require_self_or_admin()`, `require_admin()` and the disabled-account branch of `get_current_user()`; `services/routers/users.py` -> `update_user_credentials()` (role change by a non-admin). | A route answered 403. | batch | Yes |
| `user_role_changed` | backend | `services/routers/users.py` -> `update_user_credentials()`, after `update_user()`. | `PUT /users/{user_id}` changed the `role` field. | batch | Yes |
| `incident_created` | backend | `services/routers/incidents.py` -> `create_incident()`, after `commit_session()`. | `POST /api/incidents` succeeded. | batch | Yes |
| `incident_status_changed` | backend | `services/routers/incidents.py` -> `update_status()`, after `commit_session()`. | `PATCH /api/incidents/{incident_id}/status` succeeded. | batch | Yes |
| `incident_file_analyzed` | backend | `services/routers/incident_analysis.py` -> `analyze_incident_file()`, after `analyze_csv()` returns. | `POST /api/incidents/analyze` answered 200. | batch | Yes |
| `incident_file_rejected` | backend | `services/routers/incident_analysis.py` -> `analyze_incident_file()`, at each early return. | `POST /api/incidents/analyze` answered 400, 413, 415 or 422. | batch | Yes |
| `page_viewed` | frontend | One handler in the root layout of `uis/backoffice` (and of `uis/website` if its pages are tracked). | A route finished rendering (initial load or in-app navigation). | batch | Yes |
| `flow_started` | frontend | The form components under `uis/backoffice/app/**` (inbound, outbound, incident report, registration, password reset and change). | The user first interacted with a flow's form (focused or edited a field). | batch | Yes |
| `flow_abandoned` | frontend | The same form components, on route change and on `visibilitychange`/`pagehide` (sent with `navigator.sendBeacon`). | The user left the page (navigation, tab close, session end) after `flow_started` and before the flow's success event. | batch | Yes |
| `filter_applied` | frontend | `uis/backoffice/app/backoffice/incidents/page.tsx`, after the list reloads. | The user changed a filter on the incident list and the new results loaded. | batch | Yes |
| `form_validation_failed` | frontend | The form components under `uis/backoffice/app/**`, where `fieldErrors` is set. | The user submitted a form and the browser-side or server-side validation produced errors. | batch | Yes |
| `api_latency_recorded` | backend | A request middleware in `services/main.py`, after the response is built. It also assigns the `requestId`. | Any API request finished. | stream | Yes |
| `page_load_recorded` | frontend | One observer in the root layout of `uis/backoffice` using the browser's performance APIs. | A page finished loading, or an in-app navigation finished. | batch | Yes |
| `query_slow_detected` | backend | A SQLAlchemy engine event hook in `services/database.py`, tied to the current request. | A statement ran longer than the slow-query threshold (start at 200 ms), or one request ran an unusual number of statements. | batch | Yes |
| `frontend_error_caught` | frontend | Global handlers in the root layout of `uis/backoffice` and the boundary in `uis/backoffice/app/error.tsx`. | A `window` error, an unhandled promise rejection or a React error boundary fired. | stream | Yes |
| `api_error_returned` | backend | The exception handlers and the catch-all middleware in `services/main.py`, and `IncidentRoute` in `services/routers/incidents.py`. | A response with status 500 to 599 was sent. | stream | Yes |
| `network_request_failed` | frontend | `uis/backoffice/lib/api-client.ts` -> `apiRequest()`, in the `catch` around `fetch`. | `fetch` failed with a network error or a timeout. | batch | Yes |
| `email_delivery_failed` | backend | `services/mailer.py` -> `send_email()`, in each failure branch. | The mailer returned `False`. | stream | Yes |
| `service_started` | backend | `services/main.py` -> the `lifespan` handler, after the startup checks; the UI containers from `uis/start.sh`. | The service finished starting up. | stream | Yes |

## 4. Phase 2: the event contract

### 4.1 Naming: `entity_action`

Every `event_type` is the entity first, then a verb from a controlled list: lowercase, snake case, past tense, for example `inbound_order_created`. A new verb requires updating this table.

| Verb | Use it when |
|---|---|
| `created` | a business record was persisted (after the commit, never before) |
| `rejected` | the system refused a request because of validation or a business rule |
| `denied` | the system refused a request because the caller is not authorised (HTTP 403) |
| `triggered` | a configured rule fired (for example a stock minimum was crossed) |
| `detected` | the system observed a condition on its own (an audit, a scan, an integrity check) |
| `failed` | something that should have worked did not (an attempt or a delivery) |
| `caught` | an unexpected error was intercepted |
| `returned` | an operation answered with an error status |
| `recorded` | a measurement was taken (latency, load time) |
| `succeeded` | an attempt worked (sign-in) |
| `requested` | someone asked for something that completes later (password reset link) |
| `completed` | a multi-step process reached its end |
| `registered` | a new account was created |
| `changed` | an existing record moved from one value to another |
| `expired` | a time-limited thing ran out (a session) |
| `viewed` | a screen was shown |
| `started` | a guided task or a process began |
| `abandoned` | a guided task was left unfinished |
| `applied` | the user used a filter |
| `analyzed` | a file went through the analysis |

Two rules keep the names honest. A refusal because of a rule is `rejected`; a refusal because of permissions is `denied`. And `created` means the record is stored, so it is emitted after the commit, never before.

### 4.2 The Event Envelope

Every event, from the browser or from the API, has the same outer shape. Only `properties` changes.

| Field | Type | Req. | Meaning |
|---|---|---|---|
| `eventId` | string (UUID v4) | yes | Unique id of this event. Lets the pipeline drop duplicates when a batch is retried. |
| `timestamp` | string (ISO 8601, UTC) | yes | When the thing happened, taken on the producer, for example `2026-11-21T14:03:22.418Z`. Always UTC with a `Z`. |
| `sessionId` | string (UUID) or null | yes | One browser session (created at sign-in, regenerated at sign-out). `null` only for events with no user session (service start, scheduled scans). |
| `userId` | string (UUID) or null | yes | Internal account id (`User.user_id`). It is pseudonymous: no name, no email. `null` for anonymous callers (failed logins) and system events. |
| `event_type` | string | yes | Event name in `entity_action` format (lowercase snake case, entity first, then the verb from the controlled list). |
| `schemaVersion` | string (semver) | yes | Version of the schema this event follows, for example `1.0.0`. |
| `requestId` | string (UUID) | yes | Correlation id. The frontend creates one per user action and sends it as `X-Request-ID`; the API reuses it, or creates one when absent, and writes it on every event of that request. For events with no request, a fresh UUID. |
| `properties` | object | yes | Event-specific payload. Only the keys allowlisted for that `event_type` are accepted. |

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "outbound_order_created",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": { "...": "see the event" }
}
```

### 4.3 Versioning

`schemaVersion` follows semantic versioning. Adding an optional property is a minor version. Renaming, removing or changing the type or meaning of a property is a major version, and the old version keeps being accepted for a transition period. The pipeline routes each event by `event_type` and `schemaVersion` to the schema in `event-schemas.json`.

### 4.4 Property allowlists

Every event has an allowlist: the only keys that may appear in `properties`. In `event-schemas.json` this is `additionalProperties: false` on each event's `properties` object, so an unknown key makes the event invalid and the producer drops it. Free text is never allowed: where a closed list exists, the schema uses an `enum` or a `pattern`. An allowlist is also how a developer cannot accidentally leak a field by logging a whole object.

| Event | Allowed properties (`*` = optional) |
|---|---|
| `inbound_order_created` | `warehouse`, `country`, `client_id`, `product_id`, `product_category`, `order_id`, `quantity`, `stock_after`, `has_client_reference` |
| `outbound_order_created` | `warehouse`, `country`, `client_id`, `product_id`, `product_category`, `order_id`, `exit_type`, `quantity`, `stock_after` |
| `stock_threshold_triggered` | `warehouse`, `country`, `client_id`, `product_id`, `product_category`, `quantity`, `threshold`, `severity`, `order_id` |
| `direct_stock_edit_rejected` | `warehouse`, `country`, `client_id`, `product_id`, `product_category`, `quantity`, `attempted_operation`, `attempt_channel`, `actor_role`, `http_method` |
| `inventory_discrepancy_detected` | `warehouse`, `country`, `client_id`, `product_id`, `product_category`, `quantity`, `system_quantity`, `counted_quantity`, `direction`, `count_id`, `detection_source` |
| `inbound_order_rejected` | `warehouse`, `country`, `client_id`, `product_id`, `product_category`, `rejection_reason`, `http_status`, `quantity*`, `requested_sku_id*`, `actor_role` |
| `outbound_order_rejected` | `warehouse`, `country`, `client_id`, `product_id`, `product_category`, `rejection_reason`, `http_status`, `exit_type*`, `quantity*`, `available_quantity*`, `actor_role` |
| `product_created` | `warehouse`, `country`, `client_id`, `product_id`, `product_category`, `actor_role` |
| `stock_integrity_violation_detected` | `warehouse`, `country`, `client_id`, `product_id`, `product_category`, `violation_type`, `detection_point`, `quantity`, `expected_warehouse*`, `observed_warehouse*`, `order_id*` |
| `login_succeeded` | `user_role`, `method`, `failures_before_success`, `is_first_login` |
| `login_failed` | `failure_reason`, `email_hash`, `source_network_hash*`, `attempts_in_window` |
| `login_attempt_failed` | `failure_reason`, `attempt_number` |
| `session_expired` | `reason`, `session_age_seconds*`, `route_template` |
| `user_registered` | `initial_role`, `has_name`, `has_phone`, `has_address`, `registration_channel` |
| `password_reset_requested` | `email_hash`, `account_found`, `throttled` |
| `password_reset_completed` | `token_age_seconds`, `sessions_revoked` |
| `password_reset_rejected` | `rejection_reason`, `token_age_seconds*` |
| `password_changed` | `user_role`, `sessions_revoked` |
| `password_change_rejected` | `rejection_reason`, `attempts_in_window` |
| `access_denied` | `denial_reason`, `resource`, `http_method`, `route_template`, `actor_role`, `target_user_id*` |
| `user_role_changed` | `target_user_id`, `previous_role`, `new_role` |
| `incident_created` | `category`, `origin`, `branch`, `initial_status` |
| `incident_status_changed` | `incident_id`, `from_status`, `to_status`, `incident_age_seconds`, `category`, `branch` |
| `incident_file_analyzed` | `total_records`, `valid_records`, `invalid_records`, `invalid_by_rule`, `average_satisfaction`, `duration_ms`, `file_size_bytes` |
| `incident_file_rejected` | `rejection_reason`, `http_status`, `file_size_bytes*` |
| `page_viewed` | `section`, `route_template`, `referrer_section`, `device_class`, `is_authenticated` |
| `flow_started` | `flow_name`, `entry_section` |
| `flow_abandoned` | `flow_name`, `last_step`, `elapsed_seconds`, `fields_touched` |
| `filter_applied` | `surface`, `filters_active`, `result_count` |
| `form_validation_failed` | `form_name`, `field_names`, `error_count`, `attempt_number` |
| `api_latency_recorded` | `method`, `route_template`, `status_code`, `duration_ms`, `sample_rate`, `is_slow` |
| `page_load_recorded` | `route_template`, `section`, `navigation_type`, `ttfb_ms*`, `fcp_ms*`, `lcp_ms*`, `cls*`, `inp_ms*`, `device_class`, `connection_type` |
| `query_slow_detected` | `operation`, `table`, `duration_ms`, `statement_fingerprint`, `route_template`, `query_count_in_request` |
| `frontend_error_caught` | `error_source`, `error_name`, `error_fingerprint`, `route_template`, `occurrences`, `handled_by_boundary` |
| `api_error_returned` | `method`, `route_template`, `status_code`, `error_code`, `exception_type*`, `duration_ms` |
| `network_request_failed` | `route_template`, `failure_kind`, `duration_ms`, `occurrences`, `online` |
| `email_delivery_failed` | `message_type`, `provider`, `failure_kind`, `http_status*` |
| `service_started` | `service`, `startup_duration_ms`, `database_kind*`, `email_provider*`, `reload_enabled` |

### 4.5 Schemas, event by event

Each entry has the hypothesis and decision, when it fires, where to instrument it, the delivery mode, the personal-data treatment, every property with its type, whether it is required, and a full example. Mandatory metrics come first in each category.

### Inventory (business)

#### `inbound_order_created`

A warehouse registered the receipt of goods from a client.

> We capture `inbound_order_created` because we need to know `how much volume comes in, by client and by warehouse`, which allows us to make the decision `plan warehouse capacity and staffing based on incoming volume (Ana)`.

- **Class:** Mandatory
- **Produced by:** backend
- **Fires when:** A goods receipt (StockEntry) was saved successfully.
- **Instrument at:** `services/routers/inventory.py` -> `create_stock_entry()`, right after `commit_session()` succeeds. Never before the commit: a rolled-back receipt must not be counted.
- **Used by:** Ana Whitfield (Warehouse Operations); Thomas Harry (volume by country)
- **Delivery:** batch (hourly). Capacity and staffing are planned per shift or per day. An hourly view is fresh enough, and a streaming path would cost more for a decision that is taken hours ahead.
- **Personal data:** none. No personal data. `userId` is an internal identifier.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `warehouse` | string (enum); one of `los_angeles`, `zaragoza` | yes | none | Warehouse where the stock sits or where the movement happened (los_angeles or zaragoza). **Source:** Internal code mapped: LA -> los_angeles, ZGZ -> zaragoza (SKU.warehouse or the movement's warehouse). |
| `country` | string (enum); one of `US`, `ES` | yes | none | Country of the warehouse, so dashboards can compare the United States with Spain without a join. **Source:** Derived from `warehouse`: los_angeles -> US, zaragoza -> ES. |
| `client_id` | string; pattern `^[a-z0-9]+(-[a-z0-9]+)*$`; max length 64 | yes | none | The brand (B2B client) that owns the SKU. Each SKU belongs to exactly one client. **Source:** Stable slug of SKU.client_name (lowercase, spaces to hyphens), for example `fashion-co`. Replace by the client key when a clients table exists. |
| `product_id` | string; pattern `^[A-Za-z0-9._-]{1,64}$` | yes | none | The SKU code of the product. **Source:** SKU.sku (the SKU code, not the numeric row id). |
| `product_category` | string (enum); one of `fashion`, `electronics`, `cosmetics` | yes | none | Category of the product (fashion, electronics or cosmetics). **Source:** SKU.category. |
| `order_id` | string; pattern `^[0-9]{1,18}$` | yes | none | Identifier of the inbound or outbound order, as text. **Source:** StockEntry.id or StockExit.id. |
| `quantity` | integer; >= 1 | yes | none | Units received in this order. **Source:** StockEntry.quantity. |
| `stock_after` | integer; >= 0 | yes | none | Stock of the SKU in that warehouse after the receipt. **Source:** `current_stock()` after the commit. |
| `has_client_reference` | boolean | yes | none | Whether the operator entered the client's reference (for example a purchase order number). The reference text is not captured. **Source:** `bool(StockEntry.reference)`. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "inbound_order_created",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "order_id": "1042",
    "warehouse": "los_angeles",
    "country": "US",
    "client_id": "fashion-co",
    "product_id": "FSH-TEE-M",
    "product_category": "fashion",
    "quantity": 120,
    "stock_after": 164,
    "has_client_reference": true
  }
}
```

#### `outbound_order_created`

A warehouse completed the picking and dispatch (or a confirmed loss) of an outbound order.

> We capture `outbound_order_created` because we need to know `how many orders are processed, by client and warehouse, and at what rate`, which allows us to make the decision `detect operational bottlenecks before they affect the delivery SLA (Ana)`.

- **Class:** Mandatory
- **Produced by:** backend
- **Fires when:** A stock exit (StockExit) of type `dispatch` or `loss` was saved successfully.
- **Instrument at:** `services/routers/inventory.py` -> `create_stock_exit()`, right after `commit_session()` succeeds. The stock-threshold check runs right after (see `stock_threshold_triggered`).
- **Used by:** Ana Whitfield (Warehouse Operations); Thomas Harry (SLA by country)
- **Delivery:** stream (within 60 seconds). A falling dispatch rate is only actionable while the shift is still running. A next-day report cannot prevent an SLA breach, so this feeds the live operations view.
- **Personal data:** none. No personal data. The tracking number and the recipient are never included: they belong to the last-mile domain.
- **Notes:** Dashboards must count `exit_type = dispatch` for the dispatch rate. `loss` rows are confirmed losses and feed shrinkage per client and warehouse.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `warehouse` | string (enum); one of `los_angeles`, `zaragoza` | yes | none | Warehouse where the stock sits or where the movement happened (los_angeles or zaragoza). **Source:** Internal code mapped: LA -> los_angeles, ZGZ -> zaragoza (SKU.warehouse or the movement's warehouse). |
| `country` | string (enum); one of `US`, `ES` | yes | none | Country of the warehouse, so dashboards can compare the United States with Spain without a join. **Source:** Derived from `warehouse`: los_angeles -> US, zaragoza -> ES. |
| `client_id` | string; pattern `^[a-z0-9]+(-[a-z0-9]+)*$`; max length 64 | yes | none | The brand (B2B client) that owns the SKU. Each SKU belongs to exactly one client. **Source:** Stable slug of SKU.client_name (lowercase, spaces to hyphens), for example `fashion-co`. Replace by the client key when a clients table exists. |
| `product_id` | string; pattern `^[A-Za-z0-9._-]{1,64}$` | yes | none | The SKU code of the product. **Source:** SKU.sku (the SKU code, not the numeric row id). |
| `product_category` | string (enum); one of `fashion`, `electronics`, `cosmetics` | yes | none | Category of the product (fashion, electronics or cosmetics). **Source:** SKU.category. |
| `order_id` | string; pattern `^[0-9]{1,18}$` | yes | none | Identifier of the inbound or outbound order, as text. **Source:** StockEntry.id or StockExit.id. |
| `exit_type` | string (enum); one of `dispatch`, `loss` | yes | none | `dispatch` (goods leave for delivery) or `loss` (confirmed loss). **Source:** StockExit.exit_type. |
| `quantity` | integer; >= 1 | yes | none | Units that left the warehouse in this order. **Source:** StockExit.quantity. |
| `stock_after` | integer; >= 0 | yes | none | Stock of the SKU in that warehouse after the exit. **Source:** `current_stock()` after the commit. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "outbound_order_created",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "order_id": "2210",
    "exit_type": "dispatch",
    "warehouse": "zaragoza",
    "country": "ES",
    "client_id": "electro-brand",
    "product_id": "ELC-BT-HS",
    "product_category": "electronics",
    "quantity": 3,
    "stock_after": 47
  }
}
```

#### `stock_threshold_triggered`

A SKU's stock fell below the configured minimum for its client (or reached zero).

> We capture `stock_threshold_triggered` because we need to know `how often a client runs out of available stock for a SKU`, which allows us to make the decision `alert the client and the commercial team before a stockout (Miguel)`.

- **Class:** Mandatory
- **Produced by:** backend
- **Fires when:** After an outbound order, the SKU's stock crossed from at or above its minimum to below it (edge-triggered), or reached 0.
- **Instrument at:** `services/routers/inventory.py` -> `create_stock_exit()`, after `outbound_order_created`. Compare stock before and after the movement against the SKU's minimum.
- **Used by:** Miguel Torres (Commercial and Client Relations); Ana Whitfield
- **Delivery:** stream (within 60 seconds). A stockout alert only has value before the stock runs out. Waiting for a batch defeats the purpose, because the client has to be warned while there is still time to replenish.
- **Volume control:** Edge-triggered: one event per crossing, not one per outbound order while the stock stays low. The rule re-arms when stock returns to the minimum or above.
- **Personal data:** none. No personal data.
- **Notes:** Prerequisite: the configurable minimum per client and SKU does not exist yet (the backoffice marks 5 units or fewer as low stock with a fixed number). It must be added before this event can fire.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `warehouse` | string (enum); one of `los_angeles`, `zaragoza` | yes | none | Warehouse where the stock sits or where the movement happened (los_angeles or zaragoza). **Source:** Internal code mapped: LA -> los_angeles, ZGZ -> zaragoza (SKU.warehouse or the movement's warehouse). |
| `country` | string (enum); one of `US`, `ES` | yes | none | Country of the warehouse, so dashboards can compare the United States with Spain without a join. **Source:** Derived from `warehouse`: los_angeles -> US, zaragoza -> ES. |
| `client_id` | string; pattern `^[a-z0-9]+(-[a-z0-9]+)*$`; max length 64 | yes | none | The brand (B2B client) that owns the SKU. Each SKU belongs to exactly one client. **Source:** Stable slug of SKU.client_name (lowercase, spaces to hyphens), for example `fashion-co`. Replace by the client key when a clients table exists. |
| `product_id` | string; pattern `^[A-Za-z0-9._-]{1,64}$` | yes | none | The SKU code of the product. **Source:** SKU.sku (the SKU code, not the numeric row id). |
| `product_category` | string (enum); one of `fashion`, `electronics`, `cosmetics` | yes | none | Category of the product (fashion, electronics or cosmetics). **Source:** SKU.category. |
| `quantity` | integer; >= 0 | yes | none | Stock level of the SKU in that warehouse after the movement that crossed the minimum. **Source:** `current_stock()` after the commit. |
| `threshold` | integer; >= 0 | yes | none | The configured minimum for that client and SKU, in units. **Source:** The new minimum-stock setting. |
| `severity` | string (enum); one of `below_minimum`, `out_of_stock` | yes | none | `below_minimum` when stock is under the minimum, `out_of_stock` when it reached zero. **Source:** `out_of_stock` if `quantity` is 0. |
| `order_id` | string; pattern `^[0-9]{1,18}$` | yes | none | Identifier of the inbound or outbound order, as text. **Source:** StockEntry.id or StockExit.id. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "stock_threshold_triggered",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "warehouse": "los_angeles",
    "country": "US",
    "client_id": "fashion-co",
    "product_id": "FSH-TEE-M",
    "product_category": "fashion",
    "quantity": 4,
    "threshold": 10,
    "severity": "below_minimum",
    "order_id": "2211"
  }
}
```

#### `direct_stock_edit_rejected`

A user tried to change stock directly, outside an inbound or outbound order, and the system refused.

> We capture `direct_stock_edit_rejected` because we need to know `if warehouse staff are attempting to bypass the traceability controls`, which allows us to make the decision `reinforce training or permissions at the warehouse where this happens most`.

- **Class:** Mandatory
- **Produced by:** backend
- **Fires when:** A request that would change stock without an order is refused. Two detection points: (a) `PUT`, `PATCH` or `DELETE` on `/inventory/products/{sku_id}`; (b) a stock value (`current_stock`, `stock`, `quantity_on_hand`) inside any inventory payload.
- **Instrument at:** `services/routers/inventory.py`: a handler for the `405` of the product routes (a), and a payload guard in `create_product()` (b).
- **Used by:** Ana Whitfield (Warehouse Operations); warehouse managers
- **Delivery:** batch (hourly). Training and permission changes take days to act on. Hourly batches reveal the pattern early enough, and the system has already blocked the attempt, so there is nothing to stop in real time.
- **Personal data:** none. No personal data. The user is identified only by the pseudonymous `userId`.
- **Notes:** Today (a) is refused with HTTP 405, but (b) is accepted silently: `POST /inventory/products` with `current_stock: 500` answers 201 and drops the value, so the attempt leaves no trace. The plan requires (b) to be rejected with HTTP 400 and to emit this event. The user's own warehouse is not stored anywhere yet, so `warehouse` is the warehouse of the product, the closest available proxy for 'where it happens'. `quantity` is 0 when the attempt carried no number.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `warehouse` | string (enum) or null; one of `los_angeles`, `zaragoza` | yes | none | Warehouse where the stock sits or where the movement happened (los_angeles or zaragoza). **Source:** Internal code mapped: LA -> los_angeles, ZGZ -> zaragoza (SKU.warehouse or the movement's warehouse). |
| `country` | string (enum) or null; one of `US`, `ES` | yes | none | Country of the warehouse, so dashboards can compare the United States with Spain without a join. **Source:** Derived from `warehouse`: los_angeles -> US, zaragoza -> ES. |
| `client_id` | string or null; pattern `^[a-z0-9]+(-[a-z0-9]+)*$`; max length 64 | yes | none | The brand (B2B client) that owns the SKU. Each SKU belongs to exactly one client. **Source:** Stable slug of SKU.client_name (lowercase, spaces to hyphens), for example `fashion-co`. Replace by the client key when a clients table exists. |
| `product_id` | string or null; pattern `^[A-Za-z0-9._-]{1,64}$` | yes | none | The SKU code of the product. **Source:** SKU.sku (the SKU code, not the numeric row id). |
| `product_category` | string (enum) or null; one of `fashion`, `electronics`, `cosmetics` | yes | none | Category of the product (fashion, electronics or cosmetics). **Source:** SKU.category. |
| `quantity` | integer; >= 0 | yes | none | The stock value or change the user tried to apply (absolute value). 0 when the attempt carried no number. **Source:** The number found in the rejected payload. |
| `attempted_operation` | string (enum); one of `set_stock`, `adjust_stock`, `delete_product`, `other` | yes | none | What the user tried to do to the stock. **Source:** Inferred from the HTTP method and the payload. |
| `attempt_channel` | string (enum); one of `product_update_route`, `stock_field_in_payload` | yes | none | Where the attempt was caught. **Source:** Detection point (a) or (b). |
| `actor_role` | string (enum); one of `admin`, `manager`, `user` | yes | none | Role of the signed-in user who did the action (admin, manager or user). **Source:** Authenticated user's role (userId is in the envelope). |
| `http_method` | string (enum); one of `GET`, `POST`, `PUT`, `PATCH`, `DELETE` | yes | none | HTTP method of the rejected request. **Source:** Request method. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "direct_stock_edit_rejected",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "warehouse": "zaragoza",
    "country": "ES",
    "client_id": "electro-brand",
    "product_id": "ELC-BT-HS",
    "product_category": "electronics",
    "quantity": 500,
    "attempted_operation": "set_stock",
    "attempt_channel": "stock_field_in_payload",
    "actor_role": "user",
    "http_method": "POST"
  }
}
```

#### `inventory_discrepancy_detected`

A physical count or audit found a difference between the system's stock and the real stock.

> We capture `inventory_discrepancy_detected` because we need to know `which SKUs and warehouses see the most discrepancies`, which allows us to make the decision `prioritise inventory audits on the SKUs with the highest discrepancy rate (Ana)`.

- **Class:** Mandatory
- **Produced by:** backend
- **Fires when:** A stock count is saved and the counted units differ from the system stock.
- **Instrument at:** A new stock-count flow (to be built). The reconciliation must still go through an adjustment inbound or outbound order that references `count_id`, so stock is never edited directly.
- **Used by:** Ana Whitfield (Warehouse Operations)
- **Delivery:** batch (daily). Audits are planned weekly and discrepancy rates only make sense over days. A daily batch loses nothing.
- **Personal data:** none. No personal data.
- **Notes:** Prerequisite: the application has no physical count or audit capability yet. Without it this event cannot fire.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `warehouse` | string (enum); one of `los_angeles`, `zaragoza` | yes | none | Warehouse where the stock sits or where the movement happened (los_angeles or zaragoza). **Source:** Internal code mapped: LA -> los_angeles, ZGZ -> zaragoza (SKU.warehouse or the movement's warehouse). |
| `country` | string (enum); one of `US`, `ES` | yes | none | Country of the warehouse, so dashboards can compare the United States with Spain without a join. **Source:** Derived from `warehouse`: los_angeles -> US, zaragoza -> ES. |
| `client_id` | string; pattern `^[a-z0-9]+(-[a-z0-9]+)*$`; max length 64 | yes | none | The brand (B2B client) that owns the SKU. Each SKU belongs to exactly one client. **Source:** Stable slug of SKU.client_name (lowercase, spaces to hyphens), for example `fashion-co`. Replace by the client key when a clients table exists. |
| `product_id` | string; pattern `^[A-Za-z0-9._-]{1,64}$` | yes | none | The SKU code of the product. **Source:** SKU.sku (the SKU code, not the numeric row id). |
| `product_category` | string (enum); one of `fashion`, `electronics`, `cosmetics` | yes | none | Category of the product (fashion, electronics or cosmetics). **Source:** SKU.category. |
| `quantity` | integer; >= 1 | yes | none | Absolute difference in units between the counted and the system stock. **Source:** `abs(counted_quantity - system_quantity)`. |
| `system_quantity` | integer; >= 0 | yes | none | Stock the system showed at the moment of the count. **Source:** `current_stock()` when the count is saved. |
| `counted_quantity` | integer; >= 0 | yes | none | Units physically counted. **Source:** Entered by the person who counted. |
| `direction` | string (enum); one of `shortage`, `surplus` | yes | none | `shortage` when fewer units were found than the system shows, `surplus` when more. **Source:** Sign of the difference. |
| `count_id` | string; pattern `^[A-Za-z0-9_-]{1,40}$` | yes | none | Identifier of the count or audit session. **Source:** Identifier of the new count record. |
| `detection_source` | string (enum); one of `cycle_count`, `full_audit`, `spot_check` | yes | none | Kind of count that found it. **Source:** Chosen when the count is created. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "inventory_discrepancy_detected",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "warehouse": "los_angeles",
    "country": "US",
    "client_id": "beauty-lab",
    "product_id": "COS-SER-30",
    "product_category": "cosmetics",
    "quantity": 6,
    "system_quantity": 80,
    "counted_quantity": 74,
    "direction": "shortage",
    "count_id": "CNT-2026-0007",
    "detection_source": "cycle_count"
  }
}
```

#### `inbound_order_rejected`

A goods receipt was refused by validation (unknown SKU, invalid quantity, invalid warehouse).

> We capture `inbound_order_rejected` because we need to know `which SKUs and warehouses accumulate failed receipts, and why`, which allows us to make the decision `fix the SKU master data or retrain the receiving staff on the SKUs that fail most`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `POST /inventory/orders/inbound` answers 400, 404 or 422.
- **Instrument at:** `services/routers/inventory.py` (the 404 of `_sku_or_404()`) and a validation-error handler for the 422 of the inbound route.
- **Used by:** Ana Whitfield (Warehouse Operations)
- **Delivery:** batch (hourly). Data fixes and retraining are decided per day or per week. Hourly batches are enough and the operator already sees the error on screen.
- **Personal data:** none. No personal data. Only SKU identifiers and counts.
- **Notes:** Answers the tech lead's question 'which products accumulate the most validation errors?'. Fields are nullable because a refused request may not identify a known product.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `warehouse` | string (enum) or null; one of `los_angeles`, `zaragoza` | yes | none | Warehouse where the stock sits or where the movement happened (los_angeles or zaragoza). **Source:** Internal code mapped: LA -> los_angeles, ZGZ -> zaragoza (SKU.warehouse or the movement's warehouse). |
| `country` | string (enum) or null; one of `US`, `ES` | yes | none | Country of the warehouse, so dashboards can compare the United States with Spain without a join. **Source:** Derived from `warehouse`: los_angeles -> US, zaragoza -> ES. |
| `client_id` | string or null; pattern `^[a-z0-9]+(-[a-z0-9]+)*$`; max length 64 | yes | none | The brand (B2B client) that owns the SKU. Each SKU belongs to exactly one client. **Source:** Stable slug of SKU.client_name (lowercase, spaces to hyphens), for example `fashion-co`. Replace by the client key when a clients table exists. |
| `product_id` | string or null; pattern `^[A-Za-z0-9._-]{1,64}$` | yes | none | The SKU code of the product. **Source:** SKU.sku (the SKU code, not the numeric row id). |
| `product_category` | string (enum) or null; one of `fashion`, `electronics`, `cosmetics` | yes | none | Category of the product (fashion, electronics or cosmetics). **Source:** SKU.category. |
| `rejection_reason` | string (enum); one of `unknown_product`, `invalid_quantity`, `invalid_warehouse`, `malformed_request` | yes | none | Why the receipt was refused. **Source:** Mapped from the validation error. |
| `http_status` | integer; >= 400; <= 499 | yes | none | HTTP status returned to the user. **Source:** Response status. |
| `quantity` | integer or null; >= 0 | no | none | Quantity the user tried to receive, when it was a valid number. **Source:** Request payload. |
| `requested_sku_id` | integer or null; >= 0 | no | none | Numeric product id sent in the request, useful when the SKU does not exist. **Source:** Request payload. |
| `actor_role` | string (enum); one of `admin`, `manager`, `user` | yes | none | Role of the signed-in user who did the action (admin, manager or user). **Source:** Authenticated user's role (userId is in the envelope). |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "inbound_order_rejected",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "warehouse": "zaragoza",
    "country": "ES",
    "client_id": null,
    "product_id": null,
    "product_category": null,
    "rejection_reason": "unknown_product",
    "http_status": 404,
    "quantity": 40,
    "requested_sku_id": 999,
    "actor_role": "user"
  }
}
```

#### `outbound_order_rejected`

An outbound order was refused: insufficient stock, unknown SKU or invalid data.

> We capture `outbound_order_rejected` because we need to know `which products block dispatch because the stock the system shows cannot cover the order, or because of data entry mistakes`, which allows us to make the decision `order a recount for the products that repeatedly block dispatch, and fix the data entry problems behind the rest`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `POST /inventory/orders/outbound` answers 400, 404 or 422.
- **Instrument at:** `services/routers/inventory.py` -> `create_stock_exit()` (the 400 'Insufficient stock' and the 404) and a validation-error handler for the 422.
- **Used by:** Ana Whitfield (Warehouse Operations)
- **Delivery:** batch (hourly). A recount is scheduled for the next shift or day. Hourly batches are enough, and the operator already sees the error immediately.
- **Personal data:** none. No personal data. The tracking number is never included.
- **Notes:** Repeated `insufficient_stock` for a SKU that the warehouse says is on the shelf is the cheapest signal of an inaccurate system stock.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `warehouse` | string (enum) or null; one of `los_angeles`, `zaragoza` | yes | none | Warehouse where the stock sits or where the movement happened (los_angeles or zaragoza). **Source:** Internal code mapped: LA -> los_angeles, ZGZ -> zaragoza (SKU.warehouse or the movement's warehouse). |
| `country` | string (enum) or null; one of `US`, `ES` | yes | none | Country of the warehouse, so dashboards can compare the United States with Spain without a join. **Source:** Derived from `warehouse`: los_angeles -> US, zaragoza -> ES. |
| `client_id` | string or null; pattern `^[a-z0-9]+(-[a-z0-9]+)*$`; max length 64 | yes | none | The brand (B2B client) that owns the SKU. Each SKU belongs to exactly one client. **Source:** Stable slug of SKU.client_name (lowercase, spaces to hyphens), for example `fashion-co`. Replace by the client key when a clients table exists. |
| `product_id` | string or null; pattern `^[A-Za-z0-9._-]{1,64}$` | yes | none | The SKU code of the product. **Source:** SKU.sku (the SKU code, not the numeric row id). |
| `product_category` | string (enum) or null; one of `fashion`, `electronics`, `cosmetics` | yes | none | Category of the product (fashion, electronics or cosmetics). **Source:** SKU.category. |
| `rejection_reason` | string (enum); one of `insufficient_stock`, `unknown_product`, `invalid_quantity`, `missing_tracking_number`, `tracking_number_not_allowed`, `invalid_exit_type`, `invalid_warehouse`, `malformed_request` | yes | none | Why the order was refused. **Source:** Mapped from the business rule or the validation error. |
| `http_status` | integer; >= 400; <= 499 | yes | none | HTTP status returned to the user. **Source:** Response status. |
| `exit_type` | string (enum) or null; one of `dispatch`, `loss` | no | none | Type of exit the user tried to register. **Source:** Request payload. |
| `quantity` | integer or null; >= 0 | no | none | Quantity the user tried to dispatch, when it was a valid number. **Source:** Request payload. |
| `available_quantity` | integer or null; >= 0 | no | none | Stock available at the time, only for `insufficient_stock`. **Source:** `current_stock()`. |
| `actor_role` | string (enum); one of `admin`, `manager`, `user` | yes | none | Role of the signed-in user who did the action (admin, manager or user). **Source:** Authenticated user's role (userId is in the envelope). |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "outbound_order_rejected",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "warehouse": "los_angeles",
    "country": "US",
    "client_id": "fashion-co",
    "product_id": "FSH-TEE-M",
    "product_category": "fashion",
    "rejection_reason": "insufficient_stock",
    "http_status": 400,
    "exit_type": "dispatch",
    "quantity": 30,
    "available_quantity": 12,
    "actor_role": "user"
  }
}
```

#### `product_created`

A new SKU was registered for a client in a warehouse.

> We capture `product_created` because we need to know `how fast each client's catalogue grows, and in which warehouse and category`, which allows us to make the decision `plan shelf space per client and follow up with clients whose catalogue has stopped growing (Miguel)`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `POST /inventory/products` succeeded.
- **Instrument at:** `services/routers/inventory.py` -> `create_product()`, after `commit_session()`.
- **Used by:** Miguel Torres (Commercial and Client Relations); Ana Whitfield
- **Delivery:** batch (daily). Catalogue growth is a weekly commercial and space-planning conversation. A daily batch is plenty.
- **Personal data:** none. No personal data.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `warehouse` | string (enum); one of `los_angeles`, `zaragoza` | yes | none | Warehouse where the stock sits or where the movement happened (los_angeles or zaragoza). **Source:** Internal code mapped: LA -> los_angeles, ZGZ -> zaragoza (SKU.warehouse or the movement's warehouse). |
| `country` | string (enum); one of `US`, `ES` | yes | none | Country of the warehouse, so dashboards can compare the United States with Spain without a join. **Source:** Derived from `warehouse`: los_angeles -> US, zaragoza -> ES. |
| `client_id` | string; pattern `^[a-z0-9]+(-[a-z0-9]+)*$`; max length 64 | yes | none | The brand (B2B client) that owns the SKU. Each SKU belongs to exactly one client. **Source:** Stable slug of SKU.client_name (lowercase, spaces to hyphens), for example `fashion-co`. Replace by the client key when a clients table exists. |
| `product_id` | string; pattern `^[A-Za-z0-9._-]{1,64}$` | yes | none | The SKU code of the product. **Source:** SKU.sku (the SKU code, not the numeric row id). |
| `product_category` | string (enum); one of `fashion`, `electronics`, `cosmetics` | yes | none | Category of the product (fashion, electronics or cosmetics). **Source:** SKU.category. |
| `actor_role` | string (enum); one of `admin`, `manager`, `user` | yes | none | Role of the signed-in user who did the action (admin, manager or user). **Source:** Authenticated user's role (userId is in the envelope). |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "product_created",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "warehouse": "los_angeles",
    "country": "US",
    "client_id": "beauty-lab",
    "product_id": "COS-SER-30",
    "product_category": "cosmetics",
    "actor_role": "manager"
  }
}
```

#### `stock_integrity_violation_detected`

One of the system's own inventory rules was found broken: negative stock, a movement in a different warehouse than its SKU, or a movement without a product.

> We capture `stock_integrity_violation_detected` because we need to know `whether the rules the business relies on (stock never negative, movement in the SKU's warehouse) are being broken by a bug or by bad data`, which allows us to make the decision `stop the line: block the affected flow and repair the data before more movements pile on top of it`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** A check finds computed stock below zero, a movement whose warehouse differs from its SKU's warehouse, or a movement whose SKU no longer exists.
- **Instrument at:** `services/routers/inventory.py` -> `create_stock_entry()` and `create_stock_exit()` (request-time check) plus a periodic scan job (to be built).
- **Used by:** TrackFlow Tech (engineering); Ana Whitfield
- **Delivery:** stream (within 60 seconds). A broken invariant corrupts every later read and every dispatch decision. The longer it goes unnoticed, the larger the repair, so engineering needs to know within the minute.
- **Personal data:** none. No personal data.
- **Notes:** Grounded in a real gap: today `POST /inventory/orders/inbound` with `warehouse: ZGZ` for a SKU registered in Los Angeles answers 201, because the payload warehouse is never compared with the SKU's warehouse.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `warehouse` | string (enum); one of `los_angeles`, `zaragoza` | yes | none | Warehouse where the stock sits or where the movement happened (los_angeles or zaragoza). **Source:** Internal code mapped: LA -> los_angeles, ZGZ -> zaragoza (SKU.warehouse or the movement's warehouse). |
| `country` | string (enum); one of `US`, `ES` | yes | none | Country of the warehouse, so dashboards can compare the United States with Spain without a join. **Source:** Derived from `warehouse`: los_angeles -> US, zaragoza -> ES. |
| `client_id` | string; pattern `^[a-z0-9]+(-[a-z0-9]+)*$`; max length 64 | yes | none | The brand (B2B client) that owns the SKU. Each SKU belongs to exactly one client. **Source:** Stable slug of SKU.client_name (lowercase, spaces to hyphens), for example `fashion-co`. Replace by the client key when a clients table exists. |
| `product_id` | string; pattern `^[A-Za-z0-9._-]{1,64}$` | yes | none | The SKU code of the product. **Source:** SKU.sku (the SKU code, not the numeric row id). |
| `product_category` | string (enum); one of `fashion`, `electronics`, `cosmetics` | yes | none | Category of the product (fashion, electronics or cosmetics). **Source:** SKU.category. |
| `violation_type` | string (enum); one of `negative_stock`, `warehouse_mismatch`, `orphan_movement` | yes | none | Which rule was broken. **Source:** Result of the check. |
| `detection_point` | string (enum); one of `request_check`, `periodic_scan` | yes | none | Where the problem was found. **Source:** Which check ran. |
| `quantity` | integer; >= 0 | yes | none | Units involved: the movement quantity, or the size of the negative stock. **Source:** From the offending movement or computed stock. |
| `expected_warehouse` | string (enum) or null; one of `los_angeles`, `zaragoza` | no | none | Warehouse the SKU belongs to. **Source:** SKU.warehouse. |
| `observed_warehouse` | string (enum) or null; one of `los_angeles`, `zaragoza` | no | none | Warehouse recorded on the movement. **Source:** Movement warehouse. |
| `order_id` | string or null; pattern `^[0-9]{1,18}$` | no | none | The movement involved, when there is one. **Source:** StockEntry.id or StockExit.id. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "stock_integrity_violation_detected",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "warehouse": "zaragoza",
    "country": "ES",
    "client_id": "fashion-co",
    "product_id": "FSH-TEE-M",
    "product_category": "fashion",
    "violation_type": "warehouse_mismatch",
    "detection_point": "request_check",
    "quantity": 5,
    "expected_warehouse": "los_angeles",
    "observed_warehouse": "zaragoza",
    "order_id": "1043"
  }
}
```

### Authentication and access

#### `login_succeeded`

A user signed in with the right credentials.

> We capture `login_succeeded` because we need to know `how many operators sign in per hour and per day, and how often they need several attempts`, which allows us to make the decision `size support coverage and shift schedules, and tune the lockout and rate limits with real data`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `POST /auth/login` (or `/auth/token`) issued a token.
- **Instrument at:** `services/auth.py` -> `login()` after `authenticate()` returns.
- **Used by:** Ana Whitfield; TrackFlow Tech
- **Delivery:** batch (hourly). Staffing and support cover are planned over days. The security question (attacks) is answered by `login_failed`, which is streamed.
- **Personal data:** none. No personal data. The user is the pseudonymous `userId`.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `user_role` | string (enum); one of `admin`, `manager`, `user` | yes | none | Role of the user who signed in. **Source:** User.role. |
| `method` | string (enum); one of `password` | yes | none | How the user authenticated. Only `password` exists today; the field keeps the schema stable if single sign-on is added. **Source:** Constant today. |
| `failures_before_success` | integer; >= 0 | yes | none | Failed attempts for this account in the previous 15 minutes. **Source:** Counted server-side from `login_failed` events for the same account. |
| `is_first_login` | boolean | yes | none | Whether this is the first sign-in of the account. **Source:** No earlier `login_succeeded` for this user. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "login_succeeded",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "user_role": "user",
    "method": "password",
    "failures_before_success": 1,
    "is_first_login": false
  }
}
```

#### `login_failed`

A sign-in attempt was refused.

> We capture `login_failed` because we need to know `whether failed sign-ins are forgotten passwords or an attack (many attempts, or many different accounts)`, which allows us to make the decision `enable rate limiting or account lockout, block abusive sources, or force password resets`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `POST /auth/login` (or `/auth/token`) answered 401 or 403.
- **Instrument at:** `services/auth.py` -> `authenticate()`, at each of its three refusals (unknown account, wrong password, disabled account).
- **Used by:** TrackFlow Tech (security)
- **Delivery:** stream (within 60 seconds). Credential stuffing is a burst that lasts minutes. To block it the security owner has to see it while it is happening, not the next morning.
- **Volume control:** Never dropped or sampled. Detection depends on counting every attempt. The cost is bounded by the login route's own rate.
- **Personal data:** pseudonymised. The email is never stored: only a keyed hash. The source network is a keyed hash of a truncated address. See the privacy section.
- **Notes:** The API answers the same message whether the account exists or not. The reason below is for internal telemetry only and must never be sent back to the client.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `failure_reason` | string (enum); one of `unknown_account`, `wrong_password`, `account_disabled` | yes | none | Internal reason for the refusal. **Source:** Branch of `authenticate()` that refused. |
| `email_hash` | string; pattern `^[0-9a-f]{16}$` | yes | pseudonymised | Keyed hash of the email that was tried, to count attempts per account without storing the address. **Source:** First 16 hex characters of HMAC-SHA256(lowercased email, TELEMETRY_PEPPER). The pepper is a secret kept outside the repository. |
| `source_network_hash` | string or null; pattern `^[0-9a-f]{16}$` | no | pseudonymised | Keyed hash of the caller's network (IPv4 truncated to /24, IPv6 to /48), to spot one source trying many accounts. **Source:** First 16 hex characters of HMAC-SHA256(truncated address, TELEMETRY_PEPPER). The raw address is never stored. Retention 30 days. |
| `attempts_in_window` | integer; >= 1 | yes | none | Failed attempts for this email hash in the last 15 minutes, including this one. **Source:** Counted server-side. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "login_failed",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "failure_reason": "wrong_password",
    "email_hash": "9f2c4a7b1d3e5f60",
    "source_network_hash": "1a2b3c4d5e6f7081",
    "attempts_in_window": 3
  }
}
```

#### `login_attempt_failed`

A sign-in attempt failed, seen from the browser: the user could not get in.

> We capture `login_attempt_failed` because we need to know `whether people fail to sign in because the credentials are wrong or because the service cannot be reached, as the user experiences it`, which allows us to make the decision `put effort into the password-help path when wrong credentials dominate, and escalate to engineering when network or service failures dominate`.

- **Class:** Identified opportunity
- **Produced by:** frontend
- **Fires when:** The sign-in form submitted and `signIn()` threw.
- **Instrument at:** `uis/backoffice/lib/auth.ts` -> `signIn()`, in the failure branch. It is an authentication hook, so no page tracks it.
- **Used by:** TrackFlow Tech; Ana Whitfield
- **Delivery:** batch (hourly). Deciding where to invest (password help or reliability) is a weekly conversation. The security signal that needs a fast answer is the streamed `login_failed` from the API.
- **Volume control:** One event per submit.
- **Personal data:** none. The email and the password the user typed are never captured, not even hashed: the browser cannot hold the server pepper. Only the failure class and a counter.
- **Notes:** This event is the user's view of a failure. It complements, and does not replace, the backend `login_failed`, which knows the internal reason and carries the keyed email hash.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `failure_reason` | string (enum); one of `invalid_credentials`, `network_error`, `rate_limited`, `service_unavailable`, `storage_unavailable` | yes | none | Why the attempt failed, as the browser can tell. **Source:** HTTP status of the answer: 400, 401 or 403 is `invalid_credentials`, no answer is `network_error`, 429 is `rate_limited`, 5xx is `service_unavailable`, and a browser that refuses to store the session is `storage_unavailable`. |
| `attempt_number` | integer; >= 1; <= 50 | yes | none | Consecutive failed attempts in this visit to the page, starting at 1. **Source:** Counter kept in memory, reset on success. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "login_attempt_failed",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "failure_reason": "invalid_credentials",
    "attempt_number": 2
  }
}
```

#### `session_expired`

A signed-in user was sent back to the login page because the session stopped being valid.

> We capture `session_expired` because we need to know `whether the 8-hour session matches real shift length, and how often operators are cut off mid-task`, which allows us to make the decision `change the session lifetime, add silent renewal, or warn the user before it ends`.

- **Class:** Identified opportunity
- **Produced by:** frontend
- **Fires when:** A protected call answered 401 and the session was cleared.
- **Instrument at:** `uis/backoffice/lib/api-client.ts` -> `apiRequest()`, in the 401 branch before `redirectToLogin()`.
- **Used by:** TrackFlow Tech; Ana Whitfield
- **Delivery:** batch (hourly). Changing the session lifetime is a configuration decision taken after looking at days of data, not minutes.
- **Personal data:** none. No personal data.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `reason` | string (enum); one of `token_expired`, `token_invalid`, `password_changed`, `account_removed` | yes | none | Why the session was no longer valid. **Source:** Taken from the API's 401 message class. |
| `session_age_seconds` | integer or null; >= 0 | no | none | How long ago the session started, if known. **Source:** Now minus the sign-in time kept in the browser. |
| `route_template` | string; pattern `^/[A-Za-z0-9_{}./-]{0,119}$` | yes | none | Route where the expiry was noticed. **Source:** FastAPI route path template, or the Next.js route pattern. Never the real URL: query strings can carry tokens. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "session_expired",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "reason": "token_expired",
    "session_age_seconds": 28804,
    "route_template": "/backoffice/inventory/orders/outbound"
  }
}
```

#### `user_registered`

A new account was created.

> We capture `user_registered` because we need to know `how many accounts are created, how much optional profile data people give, and whether the legacy register route is still used`, which allows us to make the decision `simplify or extend the sign-up form, and retire `/auth/register` once nobody uses it`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `POST /users` or the deprecated `POST /auth/register` created an account.
- **Instrument at:** `services/routers/users.py` -> `register_user()` and `services/auth.py` -> `register_alias()`, after the account is stored.
- **Used by:** TrackFlow Tech; Ana Whitfield
- **Delivery:** batch (daily). Sign-up volume is a daily or weekly figure and no one acts on a single registration.
- **Personal data:** none. Only whether optional fields were filled in. Their content is never captured.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `initial_role` | string (enum); one of `user` | yes | none | Role given at creation. Registration always yields `user`. **Source:** Constant. |
| `has_name` | boolean | yes | none | Whether a name was provided. **Source:** Payload field present. |
| `has_phone` | boolean | yes | none | Whether a phone number was provided. **Source:** Payload field present. |
| `has_address` | boolean | yes | none | Whether an address was provided. **Source:** Payload field present. |
| `registration_channel` | string (enum); one of `users_endpoint`, `legacy_register_alias` | yes | none | Which endpoint was used. **Source:** Route that handled the request. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "user_registered",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "initial_role": "user",
    "has_name": true,
    "has_phone": false,
    "has_address": false,
    "registration_channel": "users_endpoint"
  }
}
```

#### `password_reset_requested`

Someone asked for a password reset link.

> We capture `password_reset_requested` because we need to know `how many people forget their password, and whether unknown addresses are being probed`, which allows us to make the decision `review the password policy or add single sign-on, and spot account-enumeration attempts`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `POST /auth/forgot-password` was received (it always answers 200).
- **Instrument at:** `services/auth.py` -> `forgot_password()`, after the account lookup.
- **Used by:** TrackFlow Tech (security)
- **Delivery:** batch (hourly). The decision is a policy review. The urgent attack signal is already covered by the streamed `login_failed`.
- **Personal data:** pseudonymised. Keyed email hash only. `account_found` is security-sensitive: it must stay in the restricted security dataset and never reach a dashboard or a client.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `email_hash` | string; pattern `^[0-9a-f]{16}$` | yes | pseudonymised | Keyed hash of the email the link was requested for. **Source:** First 16 hex characters of HMAC-SHA256(lowercased email, TELEMETRY_PEPPER), the same method as in `login_failed`. |
| `account_found` | boolean | yes | sensitive (restricted) | Whether an active account exists for that email. Sensitive: restricted dataset only. **Source:** Result of the account lookup. The API response never reveals it. |
| `throttled` | boolean | yes | none | Whether the per-account cooldown suppressed the email. **Source:** Result of `register_reset_request()`. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "password_reset_requested",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "email_hash": "0a1b2c3d4e5f6071",
    "account_found": true,
    "throttled": false
  }
}
```

#### `password_reset_completed`

A password was changed through a reset link.

> We capture `password_reset_completed` because we need to know `how many requested resets are completed and how long people take`, which allows us to make the decision `adjust the 30-minute link lifetime and the email wording, and judge email deliverability`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `POST /auth/reset-password` accepted the link and changed the password.
- **Instrument at:** `services/auth.py` -> `reset_password()`, after `complete_password_reset()` succeeds.
- **Used by:** TrackFlow Tech
- **Delivery:** batch (daily). Link lifetime and wording are tuned from days of data.
- **Personal data:** none. No personal data.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `token_age_seconds` | integer; >= 0 | yes | none | Seconds between the link being issued and being used. **Source:** Now minus the token's issue time. |
| `sessions_revoked` | boolean | yes | none | Whether sessions started before the reset were signed out (they always are). **Source:** Constant `true` today. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "password_reset_completed",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "token_age_seconds": 420,
    "sessions_revoked": true
  }
}
```

#### `password_reset_rejected`

A reset link was refused (expired, already used, invalid or the account is unavailable).

> We capture `password_reset_rejected` because we need to know `why reset links fail, and whether the link lifetime is too short`, which allows us to make the decision `lengthen or shorten the link lifetime, or make the 'request a new link' path easier to find`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `POST /auth/reset-password` answered 400.
- **Instrument at:** `services/auth.py` -> `reset_password()`, at its two refusals.
- **Used by:** TrackFlow Tech
- **Delivery:** batch (daily). Tuning the lifetime is a configuration change decided from days of data.
- **Personal data:** none. No personal data. The token itself is never captured.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `rejection_reason` | string (enum); one of `expired`, `already_used_or_superseded`, `invalid_token`, `account_unavailable` | yes | none | Internal reason (never returned to the client, which always gets the same message). **Source:** Branch that refused the link. |
| `token_age_seconds` | integer or null; >= 0 | no | none | Seconds since the link was issued, when it could be read. **Source:** Token claims, if the signature was valid. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "password_reset_rejected",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "rejection_reason": "expired",
    "token_age_seconds": 2100
  }
}
```

#### `password_changed`

A signed-in user changed their own password.

> We capture `password_changed` because we need to know `how often people rotate their password on their own`, which allows us to make the decision `decide whether to promote or enforce periodic password changes`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `POST /auth/change-password` succeeded.
- **Instrument at:** `services/auth.py` -> `change_password()`, after `update_user()`.
- **Used by:** TrackFlow Tech (security)
- **Delivery:** batch (daily). A password rotation policy is decided over weeks of data, so a daily batch loses nothing.
- **Personal data:** none. No personal data.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `user_role` | string (enum); one of `admin`, `manager`, `user` | yes | none | Role of the user. **Source:** User.role. |
| `sessions_revoked` | boolean | yes | none | Whether older sessions were signed out (they always are). **Source:** Constant `true` today. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "password_changed",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "user_role": "manager",
    "sessions_revoked": true
  }
}
```

#### `password_change_rejected`

A password change was refused: wrong current password, same password, or a new password that breaks the rules.

> We capture `password_change_rejected` because we need to know `whether users struggle with the password rules, or someone with a stolen session is guessing the current password`, which allows us to make the decision `improve the password hints in the form, and investigate accounts with many wrong current passwords`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `POST /auth/change-password` answered 400 or 422.
- **Instrument at:** `services/auth.py` -> `change_password()`, at its refusals.
- **Used by:** TrackFlow Tech (security)
- **Delivery:** batch (daily). Form hints are improved from days of data. A guessing burst is caught by the streamed `login_failed` and `api_error_returned`.
- **Personal data:** none. No personal data. Passwords are never captured.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `rejection_reason` | string (enum); one of `wrong_current_password`, `same_as_current`, `invalid_new_password` | yes | none | Why the change was refused. **Source:** Branch that refused the request. |
| `attempts_in_window` | integer; >= 1 | yes | none | Refusals for this user in the last 15 minutes, including this one. **Source:** Counted server-side. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "password_change_rejected",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "rejection_reason": "wrong_current_password",
    "attempts_in_window": 2
  }
}
```

#### `access_denied`

An authenticated user was refused access to something that is not theirs or that needs a higher role.

> We capture `access_denied` because we need to know `whether users probe accounts or administrator functions they should not reach`, which allows us to make the decision `review roles and permissions, and investigate users who keep trying`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** A route answered 403.
- **Instrument at:** `services/auth.py` -> `require_self_or_admin()`, `require_admin()` and the disabled-account branch of `get_current_user()`; `services/routers/users.py` -> `update_user_credentials()` (role change by a non-admin).
- **Used by:** TrackFlow Tech (security)
- **Delivery:** batch (hourly). Reviewing permissions is a periodic task. Hourly batches surface a probing user the same day.
- **Personal data:** none. No personal data. `target_user_id` is a pseudonymous identifier.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `denial_reason` | string (enum); one of `not_owner`, `admin_required`, `account_disabled` | yes | none | Why access was refused. **Source:** Check that refused. |
| `resource` | string (enum); one of `users`, `profiles`, `inventory`, `incidents`, `incident_analysis`, `other` | yes | none | Kind of resource the user tried to reach. **Source:** Router that handled the request. |
| `http_method` | string (enum); one of `GET`, `POST`, `PUT`, `PATCH`, `DELETE` | yes | none | HTTP method of the request. **Source:** Request method. |
| `route_template` | string; pattern `^/[A-Za-z0-9_{}./-]{0,119}$` | yes | none | Route with parameters replaced by their name, without query string, for example `/inventory/products/{sku_id}`. **Source:** FastAPI route path template, or the Next.js route pattern. Never the real URL: query strings can carry tokens. |
| `actor_role` | string (enum); one of `admin`, `manager`, `user` | yes | none | Role of the signed-in user who did the action (admin, manager or user). **Source:** Authenticated user's role (userId is in the envelope). |
| `target_user_id` | string or null; pattern `^[0-9a-f-]{36}$` | no | pseudonymised | The account the user tried to reach, when the route has one. **Source:** `user_id` path parameter. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "access_denied",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "denial_reason": "not_owner",
    "resource": "users",
    "http_method": "GET",
    "route_template": "/users/{user_id}",
    "actor_role": "user",
    "target_user_id": "b9048e2a-ad79-4f10-b831-e5e2f9762629"
  }
}
```

#### `user_role_changed`

An administrator changed the role of an account.

> We capture `user_role_changed` because we need to know `who gains or loses privileges, and when`, which allows us to make the decision `audit privileged access every quarter and spot unexpected promotions`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `PUT /users/{user_id}` changed the `role` field.
- **Instrument at:** `services/routers/users.py` -> `update_user_credentials()`, after `update_user()`.
- **Used by:** TrackFlow Tech (security); Thomas Harry
- **Delivery:** batch (daily). Privileged-access reviews are periodic. The count is tiny and nobody has to react within the hour.
- **Personal data:** pseudonymised. Only pseudonymous user identifiers. The administrator is `userId` in the envelope.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `target_user_id` | string; pattern `^[0-9a-f-]{36}$` | yes | pseudonymised | The account whose role changed. **Source:** `user_id` path parameter. |
| `previous_role` | string (enum); one of `admin`, `manager`, `user` | yes | none | Role before the change. **Source:** User.role before the update. |
| `new_role` | string (enum); one of `admin`, `manager`, `user` | yes | none | Role after the change. **Source:** Value requested. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "user_role_changed",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "target_user_id": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
    "previous_role": "user",
    "new_role": "manager"
  }
}
```

### Incident management (business)

#### `incident_created`

An incident was registered in the incident manager.

> We capture `incident_created` because we need to know `how many incidents are reported, by category, origin and branch`, which allows us to make the decision `staff customer-experience coverage by branch and prioritise root-cause work on the most frequent categories (Valentina)`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `POST /api/incidents` succeeded.
- **Instrument at:** `services/routers/incidents.py` -> `create_incident()`, after `commit_session()`.
- **Used by:** Valentina Cruz (Customer Experience)
- **Delivery:** batch (hourly). Staffing and root-cause work are decided per day or per week. Hourly is fresh enough.
- **Personal data:** none. The description and title are free text that can contain names or emails, so they are never captured. Only the closed categories are.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `category` | string (enum); one of `lost_parcel`, `delivery_failure`, `inventory_discrepancy`, `carrier_issue`, `returns_issue`, `warehouse_incident`, `system_failure`, `client_complaint`, `other` | yes | none | Incident category. **Source:** Incident.category. |
| `origin` | string (enum); one of `customer`, `branch`, `internal` | yes | none | Where the report came from. **Source:** Incident.origin. |
| `branch` | string (enum); one of `central`, `la_warehouse`, `la_office`, `zaragoza_warehouse`, `zaragoza_office` | yes | none | Branch the incident belongs to. **Source:** Incident.branch. |
| `initial_status` | string (enum); one of `open`, `in_progress`, `resolved`, `discarded` | yes | none | Status at creation. **Source:** Incident.status. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "incident_created",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "category": "lost_parcel",
    "origin": "customer",
    "branch": "la_warehouse",
    "initial_status": "open"
  }
}
```

#### `incident_status_changed`

An incident moved to another status.

> We capture `incident_status_changed` because we need to know `how long incidents take to move through their lifecycle and where they stall`, which allows us to make the decision `escalate or rebalance staffing when the time to resolve grows (Valentina)`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `PATCH /api/incidents/{incident_id}/status` succeeded.
- **Instrument at:** `services/routers/incidents.py` -> `update_status()`, after `commit_session()`.
- **Used by:** Valentina Cruz (Customer Experience)
- **Delivery:** batch (hourly). Time to resolve is reviewed daily. Hourly batches are enough to see a pile-up in the same shift.
- **Personal data:** none. No personal data.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `incident_id` | integer; >= 1 | yes | none | Numeric id of the incident. **Source:** Path parameter. |
| `from_status` | string (enum); one of `open`, `in_progress`, `resolved`, `discarded` | yes | none | Status before. **Source:** Incident.status before the update. |
| `to_status` | string (enum); one of `open`, `in_progress`, `resolved`, `discarded` | yes | none | Status after. **Source:** Value requested. |
| `incident_age_seconds` | integer; >= 0 | yes | none | Seconds since the incident was created. **Source:** Now minus Incident.created_at. |
| `category` | string (enum); one of `lost_parcel`, `delivery_failure`, `inventory_discrepancy`, `carrier_issue`, `returns_issue`, `warehouse_incident`, `system_failure`, `client_complaint`, `other` | yes | none | Incident category. **Source:** Incident.category. |
| `branch` | string (enum); one of `central`, `la_warehouse`, `la_office`, `zaragoza_warehouse`, `zaragoza_office` | yes | none | Branch of the incident. **Source:** Incident.branch. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "incident_status_changed",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "incident_id": 17,
    "from_status": "open",
    "to_status": "in_progress",
    "incident_age_seconds": 5400,
    "category": "delivery_failure",
    "branch": "zaragoza_office"
  }
}
```

#### `incident_file_analyzed`

The helpdesk incident export went through the analysis.

> We capture `incident_file_analyzed` because we need to know `how often the helpdesk export is analysed, how clean it is, and how satisfaction evolves`, which allows us to make the decision `agree data-quality fixes with the team that owns the helpdesk export, and track the satisfaction trend (Valentina)`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `POST /api/incidents/analyze` answered 200.
- **Instrument at:** `services/routers/incident_analysis.py` -> `analyze_incident_file()`, after `analyze_csv()` returns.
- **Used by:** Valentina Cruz (Customer Experience)
- **Delivery:** batch (daily). The export is analysed a few times a week and the data-quality conversation is weekly.
- **Personal data:** none. Aggregates only. The file name, the rows and the customer emails never leave the analysis.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `total_records` | integer; >= 1 | yes | none | Records in the file. **Source:** Analysis result. |
| `valid_records` | integer; >= 0 | yes | none | Records that passed every rule. **Source:** Analysis result. |
| `invalid_records` | integer; >= 0 | yes | none | Records that broke at least one rule. **Source:** Analysis result. |
| `invalid_by_rule` | object (allowlisted keys, integer values); keys: `invalid_tracking_number`, `invalid_carrier`, `invalid_category`, `invalid_email`, `closed_without_score`, `invalid_country`, `invalid_description`, `score_out_of_range`, `invalid_status`, `missing_required_field` | yes | none | Count of records per broken rule. A record can appear under several rules. **Source:** `AnalysisResult.rule_counts`. |
| `average_satisfaction` | number or null; >= 1; <= 5 | yes | none | Average satisfaction score of valid closed incidents, or null when there are none. **Source:** `AnalysisResult.average_score`. |
| `duration_ms` | integer; >= 0 | yes | none | Time the analysis took. **Source:** Measured around `analyze_csv()`. |
| `file_size_bytes` | integer; >= 1 | yes | none | Size of the uploaded file. **Source:** `UploadFile.size`. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "incident_file_analyzed",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "total_records": 100,
    "valid_records": 95,
    "invalid_records": 5,
    "invalid_by_rule": {
      "invalid_tracking_number": 1,
      "invalid_carrier": 1,
      "invalid_category": 1,
      "invalid_email": 1,
      "closed_without_score": 1
    },
    "average_satisfaction": 3.06,
    "duration_ms": 84,
    "file_size_bytes": 11392
  }
}
```

#### `incident_file_rejected`

An uploaded incident file could not be analysed.

> We capture `incident_file_rejected` because we need to know `why uploads fail, and whether the size limit or the export instructions are the problem`, which allows us to make the decision `improve the export instructions, or raise the upload limit if large files are the cause`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** `POST /api/incidents/analyze` answered 400, 413, 415 or 422.
- **Instrument at:** `services/routers/incident_analysis.py` -> `analyze_incident_file()`, at each early return.
- **Used by:** Valentina Cruz (Customer Experience); TrackFlow Tech
- **Delivery:** batch (daily). Instructions and limits are adjusted from weekly patterns, and the user already sees the reason on screen.
- **Personal data:** none. No file name and no content.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `rejection_reason` | string (enum); one of `empty_file`, `no_file`, `not_csv`, `too_large`, `not_multipart`, `invalid_encoding`, `missing_columns`, `no_data_rows`, `malformed_csv` | yes | none | Why the file was refused. **Source:** Mapped from the API error code and message class. |
| `http_status` | integer; >= 400; <= 499 | yes | none | HTTP status returned. **Source:** Response status. |
| `file_size_bytes` | integer or null; >= 0 | no | none | Size of the file, when it was received. **Source:** `UploadFile.size`. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "incident_file_rejected",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "rejection_reason": "missing_columns",
    "http_status": 422,
    "file_size_bytes": 2048
  }
}
```

### Navigation and engagement

#### `page_viewed`

A screen of the backoffice was shown.

> We capture `page_viewed` because we need to know `which sections operators actually use, and in what order`, which allows us to make the decision `reorder the menu, retire sections nobody opens, and put engineering time into the most visited screens`.

- **Class:** Identified opportunity
- **Produced by:** frontend
- **Fires when:** A route finished rendering (initial load or in-app navigation).
- **Instrument at:** One handler in the root layout of `uis/backoffice` (and of `uis/website` if its pages are tracked).
- **Used by:** TrackFlow Tech; Ana Whitfield
- **Delivery:** batch (hourly). Menu and roadmap decisions are made over weeks.
- **Volume control:** One event per route change. Ignore a repeat of the same route within 2 seconds (double render, redirect).
- **Personal data:** none. Only the route template, never the real URL: query strings can carry a password-reset token.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `section` | string (enum); one of `overview`, `inventory_products`, `inventory_inbound_order`, `inventory_outbound_order`, `inventory_order_history`, `inventory_stock_count`, `incidents_list`, `incidents_new`, `incidents_file_analysis`, `account_profile`, `account_change_password`, `auth_login`, `auth_register`, `auth_forgot_password`, `auth_reset_password`, `other` | yes | none | Section of the backoffice. **Source:** Map from the route. |
| `route_template` | string; pattern `^/[A-Za-z0-9_{}./-]{0,119}$` | yes | none | Next.js route pattern, without query string. **Source:** FastAPI route path template, or the Next.js route pattern. Never the real URL: query strings can carry tokens. |
| `referrer_section` | string (enum) or null; one of `overview`, `inventory_products`, `inventory_inbound_order`, `inventory_outbound_order`, `inventory_order_history`, `inventory_stock_count`, `incidents_list`, `incidents_new`, `incidents_file_analysis`, `account_profile`, `account_change_password`, `auth_login`, `auth_register`, `auth_forgot_password`, `auth_reset_password`, `other`, `external` | yes | none | Section the user came from, `external` for another site, or null for a direct visit. **Source:** Previous route in the app. |
| `device_class` | string (enum); one of `desktop`, `tablet`, `mobile` | yes | none | Kind of device. **Source:** Coarse class from the viewport width and pointer type. The user agent string is not stored. |
| `is_authenticated` | boolean | yes | none | Whether the user was signed in. **Source:** Token present in the browser. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "page_viewed",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "section": "inventory_outbound_order",
    "route_template": "/backoffice/inventory/orders/outbound",
    "referrer_section": "inventory_products",
    "device_class": "tablet",
    "is_authenticated": true
  }
}
```

#### `flow_started`

A user began a guided task (an order, an incident report, a registration).

> We capture `flow_started` because we need to know `how many times each guided task is started; this is the denominator of the abandonment rate`, which allows us to make the decision `choose which flow to simplify first`.

- **Class:** Identified opportunity
- **Produced by:** frontend
- **Fires when:** The user first interacted with a flow's form (focused or edited a field).
- **Instrument at:** The form components under `uis/backoffice/app/**` (inbound, outbound, incident report, registration, password reset and change).
- **Used by:** TrackFlow Tech; Ana Whitfield
- **Delivery:** batch (hourly). Flow redesign is a roadmap decision taken from days of data.
- **Volume control:** At most once per form visit.
- **Personal data:** none. No personal data and no field values.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `flow_name` | string (enum); one of `inbound_order`, `outbound_order`, `incident_report`, `incident_file_analysis`, `registration`, `password_reset`, `password_change`, `stock_count` | yes | none | Which guided task. **Source:** Fixed per form. |
| `entry_section` | string (enum); one of `overview`, `inventory_products`, `inventory_inbound_order`, `inventory_outbound_order`, `inventory_order_history`, `inventory_stock_count`, `incidents_list`, `incidents_new`, `incidents_file_analysis`, `account_profile`, `account_change_password`, `auth_login`, `auth_register`, `auth_forgot_password`, `auth_reset_password`, `other` | yes | none | Section where the flow was opened. **Source:** Current section. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "flow_started",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "flow_name": "outbound_order",
    "entry_section": "inventory_products"
  }
}
```

#### `flow_abandoned`

A user started a guided task and left without finishing it.

> We capture `flow_abandoned` because we need to know `which flows are left unfinished and at which step`, which allows us to make the decision `redesign the step where people drop out`.

- **Class:** Identified opportunity
- **Produced by:** frontend
- **Fires when:** The user left the page (navigation, tab close, session end) after `flow_started` and before the flow's success event.
- **Instrument at:** The same form components, on route change and on `visibilitychange`/`pagehide` (sent with `navigator.sendBeacon`).
- **Used by:** TrackFlow Tech; Ana Whitfield
- **Delivery:** batch (hourly). Redesigning a flow is decided from days of data.
- **Volume control:** At most once per flow visit.
- **Personal data:** none. Only how many fields were touched, never their names beyond the closed list, and never their values.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `flow_name` | string (enum); one of `inbound_order`, `outbound_order`, `incident_report`, `incident_file_analysis`, `registration`, `password_reset`, `password_change`, `stock_count` | yes | none | Which guided task. **Source:** Fixed per form. |
| `last_step` | string (enum); one of `form_opened`, `field_edited`, `validation_failed`, `submit_failed` | yes | none | Last thing the user did before leaving. **Source:** Last state of the form. |
| `elapsed_seconds` | integer; >= 0; <= 86400 | yes | none | Seconds between starting and leaving. **Source:** Now minus the `flow_started` time. |
| `fields_touched` | integer; >= 0; <= 50 | yes | none | How many fields the user edited. **Source:** Count of changed inputs. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "flow_abandoned",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "flow_name": "outbound_order",
    "last_step": "validation_failed",
    "elapsed_seconds": 95,
    "fields_touched": 3
  }
}
```

#### `filter_applied`

A user filtered the incident list.

> We capture `filter_applied` because we need to know `which filters operators use and how narrow the results get`, which allows us to make the decision `set sensible default filters and add saved views for the most common ones`.

- **Class:** Identified opportunity
- **Produced by:** frontend
- **Fires when:** The user changed a filter on the incident list and the new results loaded.
- **Instrument at:** `uis/backoffice/app/backoffice/incidents/page.tsx`, after the list reloads.
- **Used by:** Valentina Cruz (Customer Experience); TrackFlow Tech
- **Delivery:** batch (daily). Default filters are a design decision made from weekly usage.
- **Volume control:** Debounce 800 ms and emit only the final filter state, so changing three selects in a row gives one event.
- **Personal data:** none. Filter names only, never the selected values beyond the closed lists.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `surface` | string (enum); one of `incident_list` | yes | none | Where the filter was used. **Source:** Fixed today. |
| `filters_active` | array of enum strings; items: `status`, `origin`, `branch`, `category` | yes | none | Which filters are set after the change. **Source:** Current filter state. |
| `result_count` | integer; >= 0 | yes | none | How many incidents match. **Source:** Length of the returned list. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "filter_applied",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "surface": "incident_list",
    "filters_active": [
      "status",
      "branch"
    ],
    "result_count": 12
  }
}
```

#### `form_validation_failed`

A form showed validation errors after a submit.

> We capture `form_validation_failed` because we need to know `which fields cause the most mistakes`, which allows us to make the decision `reword labels, add hints, or relax constraints on those fields`.

- **Class:** Identified opportunity
- **Produced by:** frontend
- **Fires when:** The user submitted a form and the browser-side or server-side validation produced errors.
- **Instrument at:** The form components under `uis/backoffice/app/**`, where `fieldErrors` is set.
- **Used by:** TrackFlow Tech; Ana Whitfield
- **Delivery:** batch (daily). Form copy and constraints are refined from weekly patterns.
- **Volume control:** Once per submit, with the list of fields in error.
- **Personal data:** none. Field names from a closed list only. Never the values the user typed.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `form_name` | string (enum); one of `inbound_order`, `outbound_order`, `incident_report`, `registration`, `login`, `password_reset_request`, `password_reset`, `password_change`, `profile_edit`, `stock_count` | yes | none | Which form. **Source:** Fixed per form. |
| `field_names` | array of enum strings; items: `email`, `password`, `name`, `phone`, `address`, `product`, `quantity`, `reference`, `exit_type`, `tracking_number`, `title`, `description`, `category`, `origin`, `branch`, `status`, `current_password`, `new_password`, `confirm_password` | yes | none | Fields that showed an error. **Source:** Keys of the form's error state. |
| `error_count` | integer; >= 1; <= 50 | yes | none | Number of errors shown. **Source:** Count of errors. |
| `attempt_number` | integer; >= 1; <= 50 | yes | none | How many times the user has submitted this form visit, starting at 1. **Source:** Counter per form visit. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "form_validation_failed",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "form_name": "outbound_order",
    "field_names": [
      "tracking_number",
      "quantity"
    ],
    "error_count": 2,
    "attempt_number": 1
  }
}
```

### Performance

#### `api_latency_recorded`

The time one API request took, with its route and result.

> We capture `api_latency_recorded` because we need to know `whether the API slows down during shifts and which routes are slow`, which allows us to make the decision `optimise or scale the slow routes, and escalate when the 95th percentile breaches the target`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** Any API request finished.
- **Instrument at:** A request middleware in `services/main.py`, after the response is built. It also assigns the `requestId`.
- **Used by:** TrackFlow Tech
- **Delivery:** stream (within 60 seconds). A slow API during a shift stops people working. The alert only helps if it arrives while the shift is still on.
- **Volume control:** High frequency. Always emit slow requests (above the slow threshold) and every 5xx; sample 1 in 10 of the fast successful ones and record the `sample_rate`, so counts can be re-weighted. Skip `/health`.
- **Personal data:** none. No personal data. Neither the query string nor the body is captured.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `method` | string (enum); one of `GET`, `POST`, `PUT`, `PATCH`, `DELETE` | yes | none | HTTP method. **Source:** Request method. |
| `route_template` | string; pattern `^/[A-Za-z0-9_{}./-]{0,119}$` | yes | none | Route with parameters replaced by their name, without query string, for example `/inventory/products/{sku_id}`. **Source:** FastAPI route path template, or the Next.js route pattern. Never the real URL: query strings can carry tokens. |
| `status_code` | integer; >= 100; <= 599 | yes | none | HTTP status of the response. **Source:** Response status. |
| `duration_ms` | integer; >= 0 | yes | none | Time from receiving the request to sending the response. **Source:** Monotonic clock around the handler. |
| `sample_rate` | number; <= 1; > 0 | yes | none | Share of events of this kind that are emitted: 1 when always emitted, 0.1 when sampled one in ten. **Source:** Sampling decision. |
| `is_slow` | boolean | yes | none | Whether the request was above the slow threshold. **Source:** `duration_ms` against the threshold (start at 1000 ms). |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "api_latency_recorded",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "method": "POST",
    "route_template": "/inventory/orders/outbound",
    "status_code": 201,
    "duration_ms": 212,
    "sample_rate": 0.1,
    "is_slow": false
  }
}
```

#### `page_load_recorded`

How fast a screen loaded for the user, with the standard web performance measures.

> We capture `page_load_recorded` because we need to know `how fast pages load on the devices and connections used in each warehouse`, which allows us to make the decision `prioritise frontend optimisation, and decide whether warehouse tablets on slow connections need a lighter interface`.

- **Class:** Identified opportunity
- **Produced by:** frontend
- **Fires when:** A page finished loading, or an in-app navigation finished.
- **Instrument at:** One observer in the root layout of `uis/backoffice` using the browser's performance APIs.
- **Used by:** TrackFlow Tech
- **Delivery:** batch (hourly). Frontend optimisation is planned over weeks, so hourly aggregates are enough.
- **Volume control:** One event per navigation.
- **Personal data:** none. No personal data. Coarse device class only.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `route_template` | string; pattern `^/[A-Za-z0-9_{}./-]{0,119}$` | yes | none | Next.js route pattern. **Source:** FastAPI route path template, or the Next.js route pattern. Never the real URL: query strings can carry tokens. |
| `section` | string (enum); one of `overview`, `inventory_products`, `inventory_inbound_order`, `inventory_outbound_order`, `inventory_order_history`, `inventory_stock_count`, `incidents_list`, `incidents_new`, `incidents_file_analysis`, `account_profile`, `account_change_password`, `auth_login`, `auth_register`, `auth_forgot_password`, `auth_reset_password`, `other` | yes | none | Section of the backoffice. **Source:** Map from the route. |
| `navigation_type` | string (enum); one of `initial`, `spa` | yes | none | `initial` for a full page load, `spa` for an in-app navigation. **Source:** Navigation timing. |
| `ttfb_ms` | integer or null; >= 0 | no | none | Time to first byte. **Source:** Navigation timing. |
| `fcp_ms` | integer or null; >= 0 | no | none | First contentful paint. **Source:** Paint timing. |
| `lcp_ms` | integer or null; >= 0 | no | none | Largest contentful paint. **Source:** Largest-contentful-paint observer. |
| `cls` | number or null; >= 0 | no | none | Cumulative layout shift. **Source:** Layout-shift observer. |
| `inp_ms` | integer or null; >= 0 | no | none | Interaction to next paint, when the user interacted. **Source:** Event timing. |
| `device_class` | string (enum); one of `desktop`, `tablet`, `mobile` | yes | none | Kind of device. **Source:** Coarse class from the viewport and pointer type. |
| `connection_type` | string (enum); one of `4g`, `3g`, `2g`, `slow-2g`, `unknown` | yes | none | Effective connection type reported by the browser. **Source:** `navigator.connection.effectiveType`, or `unknown`. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "page_load_recorded",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "route_template": "/backoffice/inventory/products",
    "section": "inventory_products",
    "navigation_type": "initial",
    "ttfb_ms": 180,
    "fcp_ms": 640,
    "lcp_ms": 1210,
    "cls": 0.02,
    "inp_ms": 96,
    "device_class": "tablet",
    "connection_type": "4g"
  }
}
```

#### `query_slow_detected`

A database statement took longer than the threshold.

> We capture `query_slow_detected` because we need to know `which statements dominate response time (the list endpoints run several queries per row today)`, which allows us to make the decision `add indexes, pagination or joins to the worst offenders`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** A statement ran longer than the slow-query threshold (start at 200 ms), or one request ran an unusual number of statements.
- **Instrument at:** A SQLAlchemy engine event hook in `services/database.py`, tied to the current request.
- **Used by:** TrackFlow Tech
- **Delivery:** batch (daily). Query tuning is engineering work planned from a day of data.
- **Volume control:** At most one event per statement fingerprint per minute.
- **Personal data:** none. The SQL text and its parameters are never captured, only a hash of the statement without values.
- **Notes:** Grounded in a real finding: `list_orders()` and `list_products()` run several queries for every row they return (the stock is computed per row).

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `operation` | string (enum); one of `select`, `insert`, `update`, `delete`, `other` | yes | none | Kind of statement. **Source:** First keyword of the statement. |
| `table` | string; pattern `^[a-z_]{1,40}$` | yes | none | Main table involved. **Source:** Parsed from the statement. |
| `duration_ms` | integer; >= 0 | yes | none | Time the statement took. **Source:** Before and after the cursor execute event. |
| `statement_fingerprint` | string; pattern `^[0-9a-f]{16}$` | yes | none | Hash of the statement with values removed, to group repeats. **Source:** First 16 hex characters of SHA-256 of the normalised statement. |
| `route_template` | string; pattern `^/[A-Za-z0-9_{}./-]{0,119}$` | yes | none | Route being served when the statement ran. **Source:** FastAPI route path template, or the Next.js route pattern. Never the real URL: query strings can carry tokens. |
| `query_count_in_request` | integer; >= 1 | yes | none | How many statements the request had run when this one finished. **Source:** Per-request counter. A high value signals an N+1 pattern. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "query_slow_detected",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "operation": "select",
    "table": "stockentry",
    "duration_ms": 340,
    "statement_fingerprint": "3d5f7a9b1c2e4f60",
    "route_template": "/inventory/orders",
    "query_count_in_request": 214
  }
}
```

### Errors and platform health

#### `frontend_error_caught`

An unexpected error was intercepted in the browser.

> We capture `frontend_error_caught` because we need to know `whether a release introduced errors, and which screens crash`, which allows us to make the decision `roll back or hotfix, and prioritise the screens that crash most`.

- **Class:** Identified opportunity
- **Produced by:** frontend
- **Fires when:** A `window` error, an unhandled promise rejection or a React error boundary fired.
- **Instrument at:** Global handlers in the root layout of `uis/backoffice` and the boundary in `uis/backoffice/app/error.tsx`.
- **Used by:** TrackFlow Tech
- **Delivery:** stream (within 60 seconds). A regression after a release affects every operator at once. Spotting the spike within the minute shortens the rollback.
- **Volume control:** Group by `error_fingerprint`: at most one event per fingerprint per session per minute, with `occurrences` holding the count that was folded in.
- **Personal data:** none. The error message and the stack are never captured: they can contain user data. Only the error name and a hash of the stack.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `error_source` | string (enum); one of `window_error`, `unhandled_rejection`, `react_error_boundary` | yes | none | Where the error was intercepted. **Source:** Which handler fired. |
| `error_name` | string; pattern `^[A-Za-z0-9_$.]{1,80}$` | yes | none | Class of the error, for example `TypeError`. **Source:** `error.name`. |
| `error_fingerprint` | string; pattern `^[0-9a-f]{16}$` | yes | none | Hash of the normalised stack, to group the same error. **Source:** 16 hex characters of a 64-bit FNV-1a hash of the normalised stack (file paths without query strings, line numbers kept, values removed). It only groups repeats, so it does not need to be cryptographic and can be computed synchronously at capture time. |
| `route_template` | string; pattern `^/[A-Za-z0-9_{}./-]{0,119}$` | yes | none | Route where it happened. **Source:** FastAPI route path template, or the Next.js route pattern. Never the real URL: query strings can carry tokens. |
| `occurrences` | integer; >= 1 | yes | none | How many identical errors this event stands for. **Source:** Counter of folded duplicates. |
| `handled_by_boundary` | boolean | yes | none | Whether a React error boundary showed the fallback screen. **Source:** True when the boundary fired. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "frontend_error_caught",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "error_source": "react_error_boundary",
    "error_name": "TypeError",
    "error_fingerprint": "a1b2c3d4e5f60718",
    "route_template": "/backoffice/incidents",
    "occurrences": 3,
    "handled_by_boundary": true
  }
}
```

#### `api_error_returned`

The API answered with a server error.

> We capture `api_error_returned` because we need to know `how often and where the API fails with server errors`, which allows us to make the decision `page the engineer on duty and open an incident for the failing route`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** A response with status 500 to 599 was sent.
- **Instrument at:** The exception handlers and the catch-all middleware in `services/main.py`, and `IncidentRoute` in `services/routers/incidents.py`.
- **Used by:** TrackFlow Tech
- **Delivery:** stream (within 60 seconds). A failing route blocks the operators now. The page must go out within the minute.
- **Volume control:** Group by route and `error_code`: at most one event per group per 10 seconds during a storm; the count stays in `api_latency_recorded`.
- **Personal data:** none. The exception message is never captured, only its class name.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `method` | string (enum); one of `GET`, `POST`, `PUT`, `PATCH`, `DELETE` | yes | none | HTTP method. **Source:** Request method. |
| `route_template` | string; pattern `^/[A-Za-z0-9_{}./-]{0,119}$` | yes | none | Route with parameters replaced by their name, without query string, for example `/inventory/products/{sku_id}`. **Source:** FastAPI route path template, or the Next.js route pattern. Never the real URL: query strings can carry tokens. |
| `status_code` | integer; >= 500; <= 599 | yes | none | HTTP status returned. **Source:** Response status. |
| `error_code` | string (enum); one of `internal_error`, `service_unavailable`, `database_unavailable`, `account_store_unavailable` | yes | none | Internal error class. **Source:** Which handler produced the response. |
| `exception_type` | string or null; pattern `^[A-Za-z0-9_.]{1,100}$` | no | none | Class name of the exception, if there was one. **Source:** `type(exc).__name__`. |
| `duration_ms` | integer; >= 0 | yes | none | Time until the error response. **Source:** Same clock as `api_latency_recorded`. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "api_error_returned",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "method": "POST",
    "route_template": "/inventory/orders/outbound",
    "status_code": 503,
    "error_code": "database_unavailable",
    "exception_type": "OperationalError",
    "duration_ms": 5003
  }
}
```

#### `network_request_failed`

A call from the browser to the API did not get an answer.

> We capture `network_request_failed` because we need to know `whether operators lose connectivity, and at which times`, which allows us to make the decision `invest in the network at the affected warehouse, and add an offline queue for orders if it is frequent`.

- **Class:** Identified opportunity
- **Produced by:** frontend
- **Fires when:** `fetch` failed with a network error or a timeout.
- **Instrument at:** `uis/backoffice/lib/api-client.ts` -> `apiRequest()`, in the `catch` around `fetch`.
- **Used by:** Ana Whitfield; TrackFlow Tech
- **Delivery:** batch (hourly). Events from a client that has lost its connection are buffered and sent when it returns, so they cannot be real time. A network investment is a decision taken over days.
- **Volume control:** Fold repeats of the same route and failure kind within 30 seconds into one event with `occurrences`.
- **Personal data:** none. No personal data.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `route_template` | string; pattern `^/[A-Za-z0-9_{}./-]{0,119}$` | yes | none | Route with parameters replaced by their name, without query string, for example `/inventory/products/{sku_id}`. **Source:** FastAPI route path template, or the Next.js route pattern. Never the real URL: query strings can carry tokens. |
| `failure_kind` | string (enum); one of `unreachable`, `timeout` | yes | none | What went wrong. **Source:** Which `catch` branch. |
| `duration_ms` | integer; >= 0 | yes | none | Time spent before giving up. **Source:** Clock around the call. |
| `occurrences` | integer; >= 1 | yes | none | How many identical failures this event stands for. **Source:** Counter of folded duplicates. |
| `online` | boolean | yes | none | Whether the browser reported being online. **Source:** `navigator.onLine`. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "network_request_failed",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "route_template": "/inventory/orders/inbound",
    "failure_kind": "timeout",
    "duration_ms": 15000,
    "occurrences": 2,
    "online": true
  }
}
```

#### `email_delivery_failed`

An email (a password-reset link) could not be sent.

> We capture `email_delivery_failed` because we need to know `whether password-reset emails are failing to leave the system`, which allows us to make the decision `fix the provider key or its configuration, or switch provider, before people are locked out`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** The mailer returned `False`.
- **Instrument at:** `services/mailer.py` -> `send_email()`, in each failure branch.
- **Used by:** TrackFlow Tech
- **Delivery:** stream (within 60 seconds). While delivery is broken, a user who forgot their password cannot get back in. The fix is urgent.
- **Personal data:** none. No recipient address, no link and no key. Only the failure class and the provider's status code.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `message_type` | string (enum); one of `password_reset` | yes | none | Kind of email. **Source:** Fixed today. |
| `provider` | string (enum); one of `resend`, `console`, `disabled` | yes | none | Email provider in use. **Source:** `EMAIL_PROVIDER`. |
| `failure_kind` | string (enum); one of `provider_http_error`, `network_error`, `not_configured` | yes | none | What went wrong. **Source:** Branch of `send_email()`. |
| `http_status` | integer or null; >= 100; <= 599 | no | none | Status returned by the provider, when there was one. **Source:** Provider response. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "email_delivery_failed",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "message_type": "password_reset",
    "provider": "resend",
    "failure_kind": "provider_http_error",
    "http_status": 403
  }
}
```

#### `service_started`

A service of the platform started.

> We capture `service_started` because we need to know `how often services restart and with which configuration`, which allows us to make the decision `detect crash loops and configuration drift between environments`.

- **Class:** Identified opportunity
- **Produced by:** backend
- **Fires when:** The service finished starting up.
- **Instrument at:** `services/main.py` -> the `lifespan` handler, after the startup checks; the UI containers from `uis/start.sh`.
- **Used by:** TrackFlow Tech
- **Delivery:** stream (within 60 seconds). A crash loop shows up as a stream of restarts. Engineering has to see it within minutes, not the next day.
- **Personal data:** none. No personal data and no configuration values, only which kind of backend is in use.

| Property | Type | Req. | PII | Description and how it is obtained |
|---|---|---|---|---|
| `service` | string (enum); one of `api`, `ui_website`, `ui_backoffice` | yes | none | Which service started. **Source:** Fixed per service. |
| `startup_duration_ms` | integer; >= 0 | yes | none | Time from process start to ready. **Source:** Clock around the startup. |
| `database_kind` | string (enum) or null; one of `postgresql`, `sqlite` | no | none | Kind of database the API connected to. **Source:** Scheme of `DATABASE_URL`. API only. |
| `email_provider` | string (enum) or null; one of `resend`, `console`, `disabled` | no | none | Email provider configured. **Source:** `EMAIL_PROVIDER`. API only. |
| `reload_enabled` | boolean | yes | none | Whether the process runs with hot reload (development). **Source:** Startup flag. |

Example:

```json
{
  "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21",
  "timestamp": "2026-11-21T14:03:22.418Z",
  "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
  "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629",
  "event_type": "service_started",
  "schemaVersion": "1.1.0",
  "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84",
  "properties": {
    "service": "api",
    "startup_duration_ms": 1840,
    "database_kind": "postgresql",
    "email_provider": "resend",
    "reload_enabled": false
  }
}
```

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

| Event | Mode | Window | Why this mode (decision urgency) |
|---|---|---|---|
| `outbound_order_created` | stream | within 60 seconds | A falling dispatch rate is only actionable while the shift is still running. A next-day report cannot prevent an SLA breach, so this feeds the live operations view. |
| `stock_threshold_triggered` | stream | within 60 seconds | A stockout alert only has value before the stock runs out. Waiting for a batch defeats the purpose, because the client has to be warned while there is still time to replenish. |
| `stock_integrity_violation_detected` | stream | within 60 seconds | A broken invariant corrupts every later read and every dispatch decision. The longer it goes unnoticed, the larger the repair, so engineering needs to know within the minute. |
| `login_failed` | stream | within 60 seconds | Credential stuffing is a burst that lasts minutes. To block it the security owner has to see it while it is happening, not the next morning. |
| `api_latency_recorded` | stream | within 60 seconds | A slow API during a shift stops people working. The alert only helps if it arrives while the shift is still on. |
| `frontend_error_caught` | stream | within 60 seconds | A regression after a release affects every operator at once. Spotting the spike within the minute shortens the rollback. |
| `api_error_returned` | stream | within 60 seconds | A failing route blocks the operators now. The page must go out within the minute. |
| `email_delivery_failed` | stream | within 60 seconds | While delivery is broken, a user who forgot their password cannot get back in. The fix is urgent. |
| `service_started` | stream | within 60 seconds | A crash loop shows up as a stream of restarts. Engineering has to see it within minutes, not the next day. |
| `inbound_order_created` | batch | hourly | Capacity and staffing are planned per shift or per day. An hourly view is fresh enough, and a streaming path would cost more for a decision that is taken hours ahead. |
| `direct_stock_edit_rejected` | batch | hourly | Training and permission changes take days to act on. Hourly batches reveal the pattern early enough, and the system has already blocked the attempt, so there is nothing to stop in real time. |
| `inventory_discrepancy_detected` | batch | daily | Audits are planned weekly and discrepancy rates only make sense over days. A daily batch loses nothing. |
| `inbound_order_rejected` | batch | hourly | Data fixes and retraining are decided per day or per week. Hourly batches are enough and the operator already sees the error on screen. |
| `outbound_order_rejected` | batch | hourly | A recount is scheduled for the next shift or day. Hourly batches are enough, and the operator already sees the error immediately. |
| `product_created` | batch | daily | Catalogue growth is a weekly commercial and space-planning conversation. A daily batch is plenty. |
| `login_succeeded` | batch | hourly | Staffing and support cover are planned over days. The security question (attacks) is answered by `login_failed`, which is streamed. |
| `login_attempt_failed` | batch | hourly | Deciding where to invest (password help or reliability) is a weekly conversation. The security signal that needs a fast answer is the streamed `login_failed` from the API. |
| `session_expired` | batch | hourly | Changing the session lifetime is a configuration decision taken after looking at days of data, not minutes. |
| `user_registered` | batch | daily | Sign-up volume is a daily or weekly figure and no one acts on a single registration. |
| `password_reset_requested` | batch | hourly | The decision is a policy review. The urgent attack signal is already covered by the streamed `login_failed`. |
| `password_reset_completed` | batch | daily | Link lifetime and wording are tuned from days of data. |
| `password_reset_rejected` | batch | daily | Tuning the lifetime is a configuration change decided from days of data. |
| `password_changed` | batch | daily | A password rotation policy is decided over weeks of data, so a daily batch loses nothing. |
| `password_change_rejected` | batch | daily | Form hints are improved from days of data. A guessing burst is caught by the streamed `login_failed` and `api_error_returned`. |
| `access_denied` | batch | hourly | Reviewing permissions is a periodic task. Hourly batches surface a probing user the same day. |
| `user_role_changed` | batch | daily | Privileged-access reviews are periodic. The count is tiny and nobody has to react within the hour. |
| `incident_created` | batch | hourly | Staffing and root-cause work are decided per day or per week. Hourly is fresh enough. |
| `incident_status_changed` | batch | hourly | Time to resolve is reviewed daily. Hourly batches are enough to see a pile-up in the same shift. |
| `incident_file_analyzed` | batch | daily | The export is analysed a few times a week and the data-quality conversation is weekly. |
| `incident_file_rejected` | batch | daily | Instructions and limits are adjusted from weekly patterns, and the user already sees the reason on screen. |
| `page_viewed` | batch | hourly | Menu and roadmap decisions are made over weeks. |
| `flow_started` | batch | hourly | Flow redesign is a roadmap decision taken from days of data. |
| `flow_abandoned` | batch | hourly | Redesigning a flow is decided from days of data. |
| `filter_applied` | batch | daily | Default filters are a design decision made from weekly usage. |
| `form_validation_failed` | batch | daily | Form copy and constraints are refined from weekly patterns. |
| `page_load_recorded` | batch | hourly | Frontend optimisation is planned over weeks, so hourly aggregates are enough. |
| `query_slow_detected` | batch | daily | Query tuning is engineering work planned from a day of data. |
| `network_request_failed` | batch | hourly | Events from a client that has lost its connection are buffered and sent when it returns, so they cannot be real time. A network investment is a decision taken over days. |

### 6.2 High-frequency events: throttle, debounce, sample

Some events fire constantly. Without a rule, they would outnumber everything else and hide it.

| Event | Rule |
|---|---|
| `stock_threshold_triggered` | Edge-triggered: one event per crossing, not one per outbound order while the stock stays low. The rule re-arms when stock returns to the minimum or above. |
| `login_failed` | Never dropped or sampled. Detection depends on counting every attempt. The cost is bounded by the login route's own rate. |
| `login_attempt_failed` | One event per submit. |
| `page_viewed` | One event per route change. Ignore a repeat of the same route within 2 seconds (double render, redirect). |
| `flow_started` | At most once per form visit. |
| `flow_abandoned` | At most once per flow visit. |
| `filter_applied` | Debounce 800 ms and emit only the final filter state, so changing three selects in a row gives one event. |
| `form_validation_failed` | Once per submit, with the list of fields in error. |
| `api_latency_recorded` | High frequency. Always emit slow requests (above the slow threshold) and every 5xx; sample 1 in 10 of the fast successful ones and record the `sample_rate`, so counts can be re-weighted. Skip `/health`. |
| `page_load_recorded` | One event per navigation. |
| `query_slow_detected` | At most one event per statement fingerprint per minute. |
| `frontend_error_caught` | Group by `error_fingerprint`: at most one event per fingerprint per session per minute, with `occurrences` holding the count that was folded in. |
| `api_error_returned` | Group by route and `error_code`: at most one event per group per 10 seconds during a storm; the count stays in `api_latency_recorded`. |
| `network_request_failed` | Fold repeats of the same route and failure kind within 30 seconds into one event with `occurrences`. |

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

| Event | Property | Treatment | How it is sanitised |
|---|---|---|---|
| `login_failed` | `email_hash` | pseudonymised | First 16 hex characters of HMAC-SHA256(lowercased email, TELEMETRY_PEPPER). The pepper is a secret kept outside the repository. |
| `login_failed` | `source_network_hash` | pseudonymised | First 16 hex characters of HMAC-SHA256(truncated address, TELEMETRY_PEPPER). The raw address is never stored. Retention 30 days. |
| `password_reset_requested` | `email_hash` | pseudonymised | First 16 hex characters of HMAC-SHA256(lowercased email, TELEMETRY_PEPPER), the same method as in `login_failed`. |
| `password_reset_requested` | `account_found` | sensitive (restricted) | Result of the account lookup. The API response never reveals it. |
| `access_denied` | `target_user_id` | pseudonymised | `user_id` path parameter. |
| `user_role_changed` | `target_user_id` | pseudonymised | `user_id` path parameter. |

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

| Event | Class | Phase 2 | Where |
|---|---|---|---|
| `inbound_order_created` | Mandatory | Backoffice (`uis/backoffice`) | `lib/inventory.ts` -> `inventoryApi.createInboundOrder` |
| `outbound_order_created` | Mandatory | Backoffice (`uis/backoffice`) | `lib/inventory.ts` -> `inventoryApi.createOutboundOrder` |
| `stock_threshold_triggered` | Mandatory | Backoffice (`uis/backoffice`) | `lib/inventory.ts` -> `inventoryApi.createOutboundOrder` (after the exit) |
| `direct_stock_edit_rejected` | Mandatory | Backoffice (`uis/backoffice`) | `lib/inventory.ts` -> `inventoryApi.createProduct` (stock-field guard) |
| `inventory_discrepancy_detected` | Mandatory | Backoffice (`uis/backoffice`) | `lib/inventory.ts` -> `inventoryApi.recordStockCount` (new stock-count page) |
| `inbound_order_rejected` | Identified opportunity | Backoffice (`uis/backoffice`) | `lib/inventory.ts` -> `inventoryApi.createInboundOrder` (failure branch) |
| `outbound_order_rejected` | Identified opportunity | Backoffice (`uis/backoffice`) | `lib/inventory.ts` -> `inventoryApi.createOutboundOrder` (failure branch) |
| `product_created` | Identified opportunity | Backoffice (`uis/backoffice`) | `lib/inventory.ts` -> `inventoryApi.createProduct` |
| `stock_integrity_violation_detected` | Identified opportunity | Backoffice (`uis/backoffice`) | `lib/inventory.ts` -> `checkOrderIntegrity` (response check) |
| `login_succeeded` | Identified opportunity | Backend, Phase 3 | Needs a server-side producer. |
| `login_failed` | Identified opportunity | Backend, Phase 3 | Needs a server-side producer. |
| `login_attempt_failed` | Identified opportunity | Backoffice (`uis/backoffice`) | `lib/auth.ts` -> `signIn` (failure branch) |
| `session_expired` | Identified opportunity | Backoffice (`uis/backoffice`) | `lib/api-client.ts` -> `apiRequest` (401 branch) |
| `user_registered` | Identified opportunity | Backoffice (`uis/backoffice`) | `lib/auth.ts` -> `registerUser` |
| `password_reset_requested` | Identified opportunity | Backend, Phase 3 | Needs a server-side producer. |
| `password_reset_completed` | Identified opportunity | Backend, Phase 3 | Needs a server-side producer. |
| `password_reset_rejected` | Identified opportunity | Backend, Phase 3 | Needs a server-side producer. |
| `password_changed` | Identified opportunity | Backoffice (`uis/backoffice`) | `lib/auth.ts` -> `changePassword` |
| `password_change_rejected` | Identified opportunity | Backend, Phase 3 | Needs a server-side producer. |
| `access_denied` | Identified opportunity | Backend, Phase 3 | Needs a server-side producer. |
| `user_role_changed` | Identified opportunity | Backend, Phase 3 | Needs a server-side producer. |
| `incident_created` | Identified opportunity | Backoffice (`uis/backoffice`) | `lib/incidents.ts` -> `incidentsApi.create` |
| `incident_status_changed` | Identified opportunity | Backoffice (`uis/backoffice`) | `lib/incidents.ts` -> `incidentsApi.updateStatus` |
| `incident_file_analyzed` | Identified opportunity | Backoffice (`uis/backoffice`) | `lib/incident-analysis.ts` -> `analysisApi.analyze` |
| `incident_file_rejected` | Identified opportunity | Backoffice (`uis/backoffice`) | `lib/incident-analysis.ts` -> `analysisApi.analyze` (failure branch) |
| `page_viewed` | Identified opportunity | Backoffice (`uis/backoffice`) | `app/components/TelemetryProvider.tsx` (route changes) |
| `flow_started` | Identified opportunity | Backoffice (`uis/backoffice`) | `app/components/TelemetryProvider.tsx` (first interaction with a form) |
| `flow_abandoned` | Identified opportunity | Backoffice (`uis/backoffice`) | `app/components/TelemetryProvider.tsx` (route change or page hide before completion) |
| `filter_applied` | Identified opportunity | Backoffice (`uis/backoffice`) | `lib/incidents.ts` -> `incidentsApi.list` (debounced) |
| `form_validation_failed` | Identified opportunity | Backoffice (`uis/backoffice`) | `app/components/TelemetryProvider.tsx` (submit followed by invalid fields) |
| `api_latency_recorded` | Identified opportunity | Backend, Phase 3 | Needs a server-side producer. |
| `page_load_recorded` | Identified opportunity | Backoffice (`uis/backoffice`) | `app/components/TelemetryProvider.tsx` (Web Vitals) |
| `query_slow_detected` | Identified opportunity | Backend, Phase 3 | Needs a server-side producer. |
| `frontend_error_caught` | Identified opportunity | Backoffice (`uis/backoffice`) | `app/components/TelemetryProvider.tsx` and `app/error.tsx` |
| `api_error_returned` | Identified opportunity | Backend, Phase 3 | Needs a server-side producer. |
| `network_request_failed` | Identified opportunity | Backoffice (`uis/backoffice`) | `lib/api-client.ts` -> `apiRequest` (network failure branch) |
| `email_delivery_failed` | Identified opportunity | Backend, Phase 3 | Needs a server-side producer. |
| `service_started` | Identified opportunity | Backend, Phase 3 | Needs a server-side producer. |

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

| Event | `level` | `value` comes from | `message` |
|---|---|---|---|
| `inbound_order_created` | info | `quantity` | A warehouse registered the receipt of goods from a client. |
| `outbound_order_created` | info | `quantity` | A warehouse completed the picking and dispatch (or a confirmed loss) of an outbound order. |
| `stock_threshold_triggered` | warn | `quantity` | A SKU's stock fell below the configured minimum for its client (or reached zero). |
| `direct_stock_edit_rejected` | warn | `quantity` | A user tried to change stock directly, outside an inbound or outbound order, and the system refused. |
| `inventory_discrepancy_detected` | warn | `quantity` | A physical count or audit found a difference between the system's stock and the real stock. |
| `inbound_order_rejected` | warn | `quantity` | A goods receipt was refused by validation (unknown SKU, invalid quantity, invalid warehouse). |
| `outbound_order_rejected` | warn | `quantity` | An outbound order was refused: insufficient stock, unknown SKU or invalid data. |
| `product_created` | info | null | A new SKU was registered for a client in a warehouse. |
| `stock_integrity_violation_detected` | error | `quantity` | One of the system's own inventory rules was found broken: negative stock, a movement in a different warehouse than its SKU, or a movement without a product. |
| `login_succeeded` | info | null | A user signed in with the right credentials. |
| `login_failed` | warn | `attempts_in_window` | A sign-in attempt was refused. |
| `login_attempt_failed` | warn | `attempt_number` | A sign-in attempt failed, seen from the browser: the user could not get in. |
| `session_expired` | warn | `session_age_seconds` | A signed-in user was sent back to the login page because the session stopped being valid. |
| `user_registered` | info | null | A new account was created. |
| `password_reset_requested` | info | null | Someone asked for a password reset link. |
| `password_reset_completed` | info | null | A password was changed through a reset link. |
| `password_reset_rejected` | warn | null | A reset link was refused (expired, already used, invalid or the account is unavailable). |
| `password_changed` | info | null | A signed-in user changed their own password. |
| `password_change_rejected` | warn | `attempts_in_window` | A password change was refused: wrong current password, same password, or a new password that breaks the rules. |
| `access_denied` | warn | null | An authenticated user was refused access to something that is not theirs or that needs a higher role. |
| `user_role_changed` | info | null | An administrator changed the role of an account. |
| `incident_created` | info | null | An incident was registered in the incident manager. |
| `incident_status_changed` | info | `incident_age_seconds` | An incident moved to another status. |
| `incident_file_analyzed` | info | `duration_ms` | The helpdesk incident export went through the analysis. |
| `incident_file_rejected` | warn | null | An uploaded incident file could not be analysed. |
| `page_viewed` | info | null | A screen of the backoffice was shown. |
| `flow_started` | info | null | A user began a guided task (an order, an incident report, a registration). |
| `flow_abandoned` | info | `elapsed_seconds` | A user started a guided task and left without finishing it. |
| `filter_applied` | info | `result_count` | A user filtered the incident list. |
| `form_validation_failed` | info | `error_count` | A form showed validation errors after a submit. |
| `api_latency_recorded` | info | `duration_ms` | The time one API request took, with its route and result. |
| `page_load_recorded` | info | `lcp_ms` | How fast a screen loaded for the user, with the standard web performance measures. |
| `query_slow_detected` | warn | `duration_ms` | A database statement took longer than the threshold. |
| `frontend_error_caught` | error | `occurrences` | An unexpected error was intercepted in the browser. |
| `api_error_returned` | error | `duration_ms` | The API answered with a server error. |
| `network_request_failed` | warn | `duration_ms` | A call from the browser to the API did not get an answer. |
| `email_delivery_failed` | error | null | An email (a password-reset link) could not be sent. |
| `service_started` | info | `startup_duration_ms` | A service of the platform started. |

### 13.4 What `tags` holds

`tags` is the event's `properties` object, key for key, so the dimensions the plan requires (`warehouse`, `country`, `client_id`, `product_id`, `product_category`, `quantity` and the rest of each allowlist) are searchable without a join. The envelope fields that analytics needs are kept under one reserved key, `_envelope`: `eventId`, `sessionId`, `userId`, `schemaVersion` and `requestId`. No allowlisted property starts with an underscore, so the two never collide.

```json
{
  "warehouse": "los_angeles", "country": "US", "client_id": "fashion-co", "product_id": "FSH-TEE-M",
  "product_category": "fashion", "order_id": "2210", "exit_type": "dispatch", "quantity": 3, "stock_after": 47,
  "_envelope": {
    "eventId": "5b0f2a52-6c1e-4f7e-9d0b-3a1c8e7f4d21", "sessionId": "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13",
    "userId": "b9048e2a-ad79-4f10-b831-e5e2f9762629", "schemaVersion": "1.1.0",
    "requestId": "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84"
  }
}
```

### 13.5 Known limits

- **No de-duplication yet.** The browser can send an event twice (a batch handed to `sendBeacon` while it was mid-retry). The `eventId` is stored in `tags._envelope`, but enforcing uniqueness would need a fourth index beyond the three the tech lead specified, so analytics should count distinct `tags->'_envelope'->>'eventId'` until it is added.
- **No authentication or rate limit** on the endpoint (section 9.2).
- **`service` is a constant** while the browser is the only producer.
- **`timestamp` is the browser's clock.** A wrong clock misplaces the event; the arrival time is not stored.
