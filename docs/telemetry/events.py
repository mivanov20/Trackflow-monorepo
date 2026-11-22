"""Catalogue of TrackFlow telemetry events: the single source of truth of the Telemetry Plan.

`build.py` renders `telemetry-plan.md` and `event-schemas.json` from this file, so the plan and the
schemas cannot disagree. Edit events here, then run `python docs/telemetry/build.py`.

This module only describes events. It contains no instrumentation code.

Every event answers the golden rule: "We capture <event_type> because we need to know
<hypothesis>, which allows us to make the decision <decision>."
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "shared"))

from trackflow_shared.incident_analysis import RULE_KEYS  # noqa: E402
from trackflow_shared.incidents import BRANCHES, CATEGORIES as INCIDENT_CATEGORIES  # noqa: E402
from trackflow_shared.incidents import ORIGINS as INCIDENT_ORIGINS, STATUSES as INCIDENT_STATUSES  # noqa: E402

SCHEMA_VERSION = "1.1.0"  # 1.1.0: adds login_attempt_failed; frontend error fingerprint is a 64-bit hash

# --- shared vocabularies -----------------------------------------------------------------

WAREHOUSES = ["los_angeles", "zaragoza"]
COUNTRIES = ["US", "ES"]
PRODUCT_CATEGORIES = ["fashion", "electronics", "cosmetics"]
USER_ROLES = ["admin", "manager", "user"]
DEVICE_CLASSES = ["desktop", "tablet", "mobile"]
CONNECTION_TYPES = ["4g", "3g", "2g", "slow-2g", "unknown"]
SECTIONS = [
    "overview", "inventory_products", "inventory_inbound_order", "inventory_outbound_order", "inventory_order_history", "inventory_stock_count",
    "incidents_list", "incidents_new", "incidents_file_analysis", "account_profile", "account_change_password",
    "auth_login", "auth_register", "auth_forgot_password", "auth_reset_password", "other",
]
FLOWS = [
    "inbound_order", "outbound_order", "incident_report", "incident_file_analysis",
    "registration", "password_reset", "password_change", "stock_count",
]
FORMS = [
    "inbound_order", "outbound_order", "incident_report", "registration", "login",
    "password_reset_request", "password_reset", "password_change", "profile_edit", "stock_count",
]
FORM_FIELDS = [
    "email", "password", "name", "phone", "address", "product", "quantity", "reference", "exit_type", "tracking_number",
    "title", "description", "category", "origin", "branch", "status", "current_password", "new_password", "confirm_password",
]
HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]

# Allowed verbs of the entity_action taxonomy, with the rule for when to use each one.
VERBS = {
    "created": "a business record was persisted (after the commit, never before)",
    "rejected": "the system refused a request because of validation or a business rule",
    "denied": "the system refused a request because the caller is not authorised (HTTP 403)",
    "triggered": "a configured rule fired (for example a stock minimum was crossed)",
    "detected": "the system observed a condition on its own (an audit, a scan, an integrity check)",
    "failed": "something that should have worked did not (an attempt or a delivery)",
    "caught": "an unexpected error was intercepted",
    "returned": "an operation answered with an error status",
    "recorded": "a measurement was taken (latency, load time)",
    "succeeded": "an attempt worked (sign-in)",
    "requested": "someone asked for something that completes later (password reset link)",
    "completed": "a multi-step process reached its end",
    "registered": "a new account was created",
    "changed": "an existing record moved from one value to another",
    "expired": "a time-limited thing ran out (a session)",
    "viewed": "a screen was shown",
    "started": "a guided task or a process began",
    "abandoned": "a guided task was left unfinished",
    "applied": "the user used a filter",
    "analyzed": "a file went through the analysis",
}

CATEGORIES = {
    "inventory": "Inventory (business)",
    "authentication": "Authentication and access",
    "incidents": "Incident management (business)",
    "navigation": "Navigation and engagement",
    "performance": "Performance",
    "errors": "Errors and platform health",
}


# --- property helpers ---------------------------------------------------------------------


def _prop(name, description, schema, *, required=True, pii="none", how=None, label=None):
    return {"name": name, "description": description, "schema": schema, "required": required, "pii": pii, "how": how, "label": label}


def S(name, description, *, enum=None, pattern=None, max_len=None, null=False, required=True, pii="none", how=None):
    schema = {"type": ["string", "null"] if null else "string"}
    if enum is not None:
        schema["enum"] = list(enum) + ([None] if null else [])
    if pattern:
        schema["pattern"] = pattern
    if max_len:
        schema["maxLength"] = max_len
    label = "string" + (" (enum)" if enum is not None else "") + (" or null" if null else "")
    return _prop(name, description, schema, required=required, pii=pii, how=how, label=label)


def I(name, description, *, minimum=None, maximum=None, null=False, required=True, pii="none", how=None):  # noqa: E743
    schema = {"type": ["integer", "null"] if null else "integer"}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return _prop(name, description, schema, required=required, pii=pii, how=how, label="integer" + (" or null" if null else ""))


def N(name, description, *, minimum=None, maximum=None, exclusive_minimum=None, null=False, required=True, pii="none", how=None):
    schema = {"type": ["number", "null"] if null else "number"}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    if exclusive_minimum is not None:
        schema["exclusiveMinimum"] = exclusive_minimum
    return _prop(name, description, schema, required=required, pii=pii, how=how, label="number" + (" or null" if null else ""))


def B(name, description, *, required=True, pii="none", how=None):
    return _prop(name, description, {"type": "boolean"}, required=required, pii=pii, how=how, label="boolean")


def A(name, description, item_enum, *, min_items=1, required=True, pii="none", how=None):
    schema = {"type": "array", "items": {"type": "string", "enum": list(item_enum)}, "minItems": min_items, "uniqueItems": True}
    return _prop(name, description, schema, required=required, pii=pii, how=how, label="array of enum strings")


def O(name, description, key_enum, *, required=True, pii="none", how=None):  # noqa: E743
    schema = {
        "type": "object",
        "properties": {key: {"type": "integer", "minimum": 0} for key in key_enum},
        "additionalProperties": False,
    }
    return _prop(name, description, schema, required=required, pii=pii, how=how, label="object (allowlisted keys, integer values)")


HASH16 = "^[0-9a-f]{16}$"
ROUTE = "^/[A-Za-z0-9_{}./-]{0,119}$"


def warehouse(null=False):
    return S("warehouse", "Warehouse where the stock sits or where the movement happened (los_angeles or zaragoza).", enum=WAREHOUSES, null=null,
             how="Internal code mapped: LA -> los_angeles, ZGZ -> zaragoza (SKU.warehouse or the movement's warehouse).")


def country(null=False):
    return S("country", "Country of the warehouse, so dashboards can compare the United States with Spain without a join.", enum=COUNTRIES, null=null,
             how="Derived from `warehouse`: los_angeles -> US, zaragoza -> ES.")


def client_id(null=False):
    return S("client_id", "The brand (B2B client) that owns the SKU. Each SKU belongs to exactly one client.", pattern="^[a-z0-9]+(-[a-z0-9]+)*$", max_len=64, null=null,
             how="Stable slug of SKU.client_name (lowercase, spaces to hyphens), for example `fashion-co`. Replace by the client key when a clients table exists.")


def product_id(null=False):
    return S("product_id", "The SKU code of the product.", pattern="^[A-Za-z0-9._-]{1,64}$", null=null, how="SKU.sku (the SKU code, not the numeric row id).")


def product_category(null=False):
    return S("product_category", "Category of the product (fashion, electronics or cosmetics).", enum=PRODUCT_CATEGORIES, null=null, how="SKU.category.")


def core(null=False):
    """The dimensions every inventory event can be aggregated by (warehouse, country, client, product, category)."""
    return [warehouse(null), country(null), client_id(null), product_id(null), product_category(null)]


def actor_role():
    return S("actor_role", "Role of the signed-in user who did the action (admin, manager or user).", enum=USER_ROLES, how="Authenticated user's role (userId is in the envelope).")


def order_id():
    return S("order_id", "Identifier of the inbound or outbound order, as text.", pattern="^[0-9]{1,18}$", how="StockEntry.id or StockExit.id.")


def route_template(description="Route with parameters replaced by their name, without query string, for example `/inventory/products/{sku_id}`."):
    return S("route_template", description, pattern=ROUTE, how="FastAPI route path template, or the Next.js route pattern. Never the real URL: query strings can carry tokens.")


# --- the catalogue -------------------------------------------------------------------------

STREAM = "stream"
BATCH = "batch"
STREAM_WINDOW = "within 60 seconds"

EVENTS = [
    # =========================================================== INVENTORY (business) ===
    dict(
        type="inbound_order_created", klass="mandatory", category="inventory", source=["backend"],
        description="A warehouse registered the receipt of goods from a client.",
        hypothesis="how much volume comes in, by client and by warehouse",
        decision="plan warehouse capacity and staffing based on incoming volume (Ana)",
        consumers="Ana Whitfield (Warehouse Operations); Thomas Harry (volume by country)",
        trigger="A goods receipt (StockEntry) was saved successfully.",
        emit_at="`services/routers/inventory.py` -> `create_stock_entry()`, right after `commit_session()` succeeds. Never before the commit: a rolled-back receipt must not be counted.",
        delivery=BATCH, window="hourly",
        delivery_reason="Capacity and staffing are planned per shift or per day. An hourly view is fresh enough, and a streaming path would cost more for a decision that is taken hours ahead.",
        pii="none", pii_note="No personal data. `userId` is an internal identifier.",
        properties=core() + [
            order_id(),
            I("quantity", "Units received in this order.", minimum=1, how="StockEntry.quantity."),
            I("stock_after", "Stock of the SKU in that warehouse after the receipt.", minimum=0, how="`current_stock()` after the commit."),
            B("has_client_reference", "Whether the operator entered the client's reference (for example a purchase order number). The reference text is not captured.", how="`bool(StockEntry.reference)`."),
        ],
        example=dict(order_id="1042", warehouse="los_angeles", country="US", client_id="fashion-co", product_id="FSH-TEE-M", product_category="fashion", quantity=120, stock_after=164, has_client_reference=True),
    ),
    dict(
        type="outbound_order_created", klass="mandatory", category="inventory", source=["backend"],
        description="A warehouse completed the picking and dispatch (or a confirmed loss) of an outbound order.",
        hypothesis="how many orders are processed, by client and warehouse, and at what rate",
        decision="detect operational bottlenecks before they affect the delivery SLA (Ana)",
        consumers="Ana Whitfield (Warehouse Operations); Thomas Harry (SLA by country)",
        trigger="A stock exit (StockExit) of type `dispatch` or `loss` was saved successfully.",
        emit_at="`services/routers/inventory.py` -> `create_stock_exit()`, right after `commit_session()` succeeds. The stock-threshold check runs right after (see `stock_threshold_triggered`).",
        delivery=STREAM, window=STREAM_WINDOW,
        delivery_reason="A falling dispatch rate is only actionable while the shift is still running. A next-day report cannot prevent an SLA breach, so this feeds the live operations view.",
        pii="none", pii_note="No personal data. The tracking number and the recipient are never included: they belong to the last-mile domain.",
        notes="Dashboards must count `exit_type = dispatch` for the dispatch rate. `loss` rows are confirmed losses and feed shrinkage per client and warehouse.",
        properties=core() + [
            order_id(),
            S("exit_type", "`dispatch` (goods leave for delivery) or `loss` (confirmed loss).", enum=["dispatch", "loss"], how="StockExit.exit_type."),
            I("quantity", "Units that left the warehouse in this order.", minimum=1, how="StockExit.quantity."),
            I("stock_after", "Stock of the SKU in that warehouse after the exit.", minimum=0, how="`current_stock()` after the commit."),
        ],
        example=dict(order_id="2210", exit_type="dispatch", warehouse="zaragoza", country="ES", client_id="electro-brand", product_id="ELC-BT-HS", product_category="electronics", quantity=3, stock_after=47),
    ),
    dict(
        type="stock_threshold_triggered", klass="mandatory", category="inventory", source=["backend"],
        description="A SKU's stock fell below the configured minimum for its client (or reached zero).",
        hypothesis="how often a client runs out of available stock for a SKU",
        decision="alert the client and the commercial team before a stockout (Miguel)",
        consumers="Miguel Torres (Commercial and Client Relations); Ana Whitfield",
        trigger="After an outbound order, the SKU's stock crossed from at or above its minimum to below it (edge-triggered), or reached 0.",
        emit_at="`services/routers/inventory.py` -> `create_stock_exit()`, after `outbound_order_created`. Compare stock before and after the movement against the SKU's minimum.",
        delivery=STREAM, window=STREAM_WINDOW,
        delivery_reason="A stockout alert only has value before the stock runs out. Waiting for a batch defeats the purpose, because the client has to be warned while there is still time to replenish.",
        throttle="Edge-triggered: one event per crossing, not one per outbound order while the stock stays low. The rule re-arms when stock returns to the minimum or above.",
        pii="none", pii_note="No personal data.",
        notes="Prerequisite: the configurable minimum per client and SKU does not exist yet (the backoffice marks 5 units or fewer as low stock with a fixed number). It must be added before this event can fire.",
        properties=core() + [
            I("quantity", "Stock level of the SKU in that warehouse after the movement that crossed the minimum.", minimum=0, how="`current_stock()` after the commit."),
            I("threshold", "The configured minimum for that client and SKU, in units.", minimum=0, how="The new minimum-stock setting."),
            S("severity", "`below_minimum` when stock is under the minimum, `out_of_stock` when it reached zero.", enum=["below_minimum", "out_of_stock"], how="`out_of_stock` if `quantity` is 0."),
            order_id(),
        ],
        example=dict(warehouse="los_angeles", country="US", client_id="fashion-co", product_id="FSH-TEE-M", product_category="fashion", quantity=4, threshold=10, severity="below_minimum", order_id="2211"),
    ),
    dict(
        type="direct_stock_edit_rejected", klass="mandatory", category="inventory", source=["backend"],
        description="A user tried to change stock directly, outside an inbound or outbound order, and the system refused.",
        hypothesis="if warehouse staff are attempting to bypass the traceability controls",
        decision="reinforce training or permissions at the warehouse where this happens most",
        consumers="Ana Whitfield (Warehouse Operations); warehouse managers",
        trigger="A request that would change stock without an order is refused. Two detection points: (a) `PUT`, `PATCH` or `DELETE` on `/inventory/products/{sku_id}`; (b) a stock value (`current_stock`, `stock`, `quantity_on_hand`) inside any inventory payload.",
        emit_at="`services/routers/inventory.py`: a handler for the `405` of the product routes (a), and a payload guard in `create_product()` (b).",
        delivery=BATCH, window="hourly",
        delivery_reason="Training and permission changes take days to act on. Hourly batches reveal the pattern early enough, and the system has already blocked the attempt, so there is nothing to stop in real time.",
        pii="none", pii_note="No personal data. The user is identified only by the pseudonymous `userId`.",
        notes="Today (a) is refused with HTTP 405, but (b) is accepted silently: `POST /inventory/products` with `current_stock: 500` answers 201 and drops the value, so the attempt leaves no trace. The plan requires (b) to be rejected with HTTP 400 and to emit this event. The user's own warehouse is not stored anywhere yet, so `warehouse` is the warehouse of the product, the closest available proxy for 'where it happens'. `quantity` is 0 when the attempt carried no number.",
        properties=core(null=True) + [
            I("quantity", "The stock value or change the user tried to apply (absolute value). 0 when the attempt carried no number.", minimum=0, how="The number found in the rejected payload."),
            S("attempted_operation", "What the user tried to do to the stock.", enum=["set_stock", "adjust_stock", "delete_product", "other"], how="Inferred from the HTTP method and the payload."),
            S("attempt_channel", "Where the attempt was caught.", enum=["product_update_route", "stock_field_in_payload"], how="Detection point (a) or (b)."),
            actor_role(),
            S("http_method", "HTTP method of the rejected request.", enum=HTTP_METHODS, how="Request method."),
        ],
        example=dict(warehouse="zaragoza", country="ES", client_id="electro-brand", product_id="ELC-BT-HS", product_category="electronics", quantity=500, attempted_operation="set_stock", attempt_channel="stock_field_in_payload", actor_role="user", http_method="POST"),
    ),
    dict(
        type="inventory_discrepancy_detected", klass="mandatory", category="inventory", source=["backend"],
        description="A physical count or audit found a difference between the system's stock and the real stock.",
        hypothesis="which SKUs and warehouses see the most discrepancies",
        decision="prioritise inventory audits on the SKUs with the highest discrepancy rate (Ana)",
        consumers="Ana Whitfield (Warehouse Operations)",
        trigger="A stock count is saved and the counted units differ from the system stock.",
        emit_at="A new stock-count flow (to be built). The reconciliation must still go through an adjustment inbound or outbound order that references `count_id`, so stock is never edited directly.",
        delivery=BATCH, window="daily",
        delivery_reason="Audits are planned weekly and discrepancy rates only make sense over days. A daily batch loses nothing.",
        pii="none", pii_note="No personal data.",
        notes="Prerequisite: the application has no physical count or audit capability yet. Without it this event cannot fire.",
        properties=core() + [
            I("quantity", "Absolute difference in units between the counted and the system stock.", minimum=1, how="`abs(counted_quantity - system_quantity)`."),
            I("system_quantity", "Stock the system showed at the moment of the count.", minimum=0, how="`current_stock()` when the count is saved."),
            I("counted_quantity", "Units physically counted.", minimum=0, how="Entered by the person who counted."),
            S("direction", "`shortage` when fewer units were found than the system shows, `surplus` when more.", enum=["shortage", "surplus"], how="Sign of the difference."),
            S("count_id", "Identifier of the count or audit session.", pattern="^[A-Za-z0-9_-]{1,40}$", how="Identifier of the new count record."),
            S("detection_source", "Kind of count that found it.", enum=["cycle_count", "full_audit", "spot_check"], how="Chosen when the count is created."),
        ],
        example=dict(warehouse="los_angeles", country="US", client_id="beauty-lab", product_id="COS-SER-30", product_category="cosmetics", quantity=6, system_quantity=80, counted_quantity=74, direction="shortage", count_id="CNT-2026-0007", detection_source="cycle_count"),
    ),
    dict(
        type="inbound_order_rejected", klass="opportunity", category="inventory", source=["backend"],
        description="A goods receipt was refused by validation (unknown SKU, invalid quantity, invalid warehouse).",
        hypothesis="which SKUs and warehouses accumulate failed receipts, and why",
        decision="fix the SKU master data or retrain the receiving staff on the SKUs that fail most",
        consumers="Ana Whitfield (Warehouse Operations)",
        trigger="`POST /inventory/orders/inbound` answers 400, 404 or 422.",
        emit_at="`services/routers/inventory.py` (the 404 of `_sku_or_404()`) and a validation-error handler for the 422 of the inbound route.",
        delivery=BATCH, window="hourly",
        delivery_reason="Data fixes and retraining are decided per day or per week. Hourly batches are enough and the operator already sees the error on screen.",
        pii="none", pii_note="No personal data. Only SKU identifiers and counts.",
        notes="Answers the tech lead's question 'which products accumulate the most validation errors?'. Fields are nullable because a refused request may not identify a known product.",
        properties=core(null=True) + [
            S("rejection_reason", "Why the receipt was refused.", enum=["unknown_product", "invalid_quantity", "invalid_warehouse", "malformed_request"], how="Mapped from the validation error."),
            I("http_status", "HTTP status returned to the user.", minimum=400, maximum=499, how="Response status."),
            I("quantity", "Quantity the user tried to receive, when it was a valid number.", minimum=0, null=True, required=False, how="Request payload."),
            I("requested_sku_id", "Numeric product id sent in the request, useful when the SKU does not exist.", minimum=0, null=True, required=False, how="Request payload."),
            actor_role(),
        ],
        example=dict(warehouse="zaragoza", country="ES", client_id=None, product_id=None, product_category=None, rejection_reason="unknown_product", http_status=404, quantity=40, requested_sku_id=999, actor_role="user"),
    ),
    dict(
        type="outbound_order_rejected", klass="opportunity", category="inventory", source=["backend"],
        description="An outbound order was refused: insufficient stock, unknown SKU or invalid data.",
        hypothesis="which products block dispatch because the stock the system shows cannot cover the order, or because of data entry mistakes",
        decision="order a recount for the products that repeatedly block dispatch, and fix the data entry problems behind the rest",
        consumers="Ana Whitfield (Warehouse Operations)",
        trigger="`POST /inventory/orders/outbound` answers 400, 404 or 422.",
        emit_at="`services/routers/inventory.py` -> `create_stock_exit()` (the 400 'Insufficient stock' and the 404) and a validation-error handler for the 422.",
        delivery=BATCH, window="hourly",
        delivery_reason="A recount is scheduled for the next shift or day. Hourly batches are enough, and the operator already sees the error immediately.",
        pii="none", pii_note="No personal data. The tracking number is never included.",
        notes="Repeated `insufficient_stock` for a SKU that the warehouse says is on the shelf is the cheapest signal of an inaccurate system stock.",
        properties=core(null=True) + [
            S("rejection_reason", "Why the order was refused.", enum=["insufficient_stock", "unknown_product", "invalid_quantity", "missing_tracking_number", "tracking_number_not_allowed", "invalid_exit_type", "invalid_warehouse", "malformed_request"], how="Mapped from the business rule or the validation error."),
            I("http_status", "HTTP status returned to the user.", minimum=400, maximum=499, how="Response status."),
            S("exit_type", "Type of exit the user tried to register.", enum=["dispatch", "loss"], null=True, required=False, how="Request payload."),
            I("quantity", "Quantity the user tried to dispatch, when it was a valid number.", minimum=0, null=True, required=False, how="Request payload."),
            I("available_quantity", "Stock available at the time, only for `insufficient_stock`.", minimum=0, null=True, required=False, how="`current_stock()`."),
            actor_role(),
        ],
        example=dict(warehouse="los_angeles", country="US", client_id="fashion-co", product_id="FSH-TEE-M", product_category="fashion", rejection_reason="insufficient_stock", http_status=400, exit_type="dispatch", quantity=30, available_quantity=12, actor_role="user"),
    ),
    dict(
        type="product_created", klass="opportunity", category="inventory", source=["backend"],
        description="A new SKU was registered for a client in a warehouse.",
        hypothesis="how fast each client's catalogue grows, and in which warehouse and category",
        decision="plan shelf space per client and follow up with clients whose catalogue has stopped growing (Miguel)",
        consumers="Miguel Torres (Commercial and Client Relations); Ana Whitfield",
        trigger="`POST /inventory/products` succeeded.",
        emit_at="`services/routers/inventory.py` -> `create_product()`, after `commit_session()`.",
        delivery=BATCH, window="daily",
        delivery_reason="Catalogue growth is a weekly commercial and space-planning conversation. A daily batch is plenty.",
        pii="none", pii_note="No personal data.",
        properties=core() + [actor_role()],
        example=dict(warehouse="los_angeles", country="US", client_id="beauty-lab", product_id="COS-SER-30", product_category="cosmetics", actor_role="manager"),
    ),
    dict(
        type="stock_integrity_violation_detected", klass="opportunity", category="inventory", source=["backend"],
        description="One of the system's own inventory rules was found broken: negative stock, a movement in a different warehouse than its SKU, or a movement without a product.",
        hypothesis="whether the rules the business relies on (stock never negative, movement in the SKU's warehouse) are being broken by a bug or by bad data",
        decision="stop the line: block the affected flow and repair the data before more movements pile on top of it",
        consumers="TrackFlow Tech (engineering); Ana Whitfield",
        trigger="A check finds computed stock below zero, a movement whose warehouse differs from its SKU's warehouse, or a movement whose SKU no longer exists.",
        emit_at="`services/routers/inventory.py` -> `create_stock_entry()` and `create_stock_exit()` (request-time check) plus a periodic scan job (to be built).",
        delivery=STREAM, window=STREAM_WINDOW,
        delivery_reason="A broken invariant corrupts every later read and every dispatch decision. The longer it goes unnoticed, the larger the repair, so engineering needs to know within the minute.",
        pii="none", pii_note="No personal data.",
        notes="Grounded in a real gap: today `POST /inventory/orders/inbound` with `warehouse: ZGZ` for a SKU registered in Los Angeles answers 201, because the payload warehouse is never compared with the SKU's warehouse.",
        properties=core() + [
            S("violation_type", "Which rule was broken.", enum=["negative_stock", "warehouse_mismatch", "orphan_movement"], how="Result of the check."),
            S("detection_point", "Where the problem was found.", enum=["request_check", "periodic_scan"], how="Which check ran."),
            I("quantity", "Units involved: the movement quantity, or the size of the negative stock.", minimum=0, how="From the offending movement or computed stock."),
            S("expected_warehouse", "Warehouse the SKU belongs to.", enum=WAREHOUSES, null=True, required=False, how="SKU.warehouse."),
            S("observed_warehouse", "Warehouse recorded on the movement.", enum=WAREHOUSES, null=True, required=False, how="Movement warehouse."),
            S("order_id", "The movement involved, when there is one.", pattern="^[0-9]{1,18}$", null=True, required=False, how="StockEntry.id or StockExit.id."),
        ],
        example=dict(warehouse="zaragoza", country="ES", client_id="fashion-co", product_id="FSH-TEE-M", product_category="fashion", violation_type="warehouse_mismatch", detection_point="request_check", quantity=5, expected_warehouse="los_angeles", observed_warehouse="zaragoza", order_id="1043"),
    ),
    # ===================================================== AUTHENTICATION AND ACCESS ===
    dict(
        type="login_succeeded", klass="opportunity", category="authentication", source=["backend"],
        description="A user signed in with the right credentials.",
        hypothesis="how many operators sign in per hour and per day, and how often they need several attempts",
        decision="size support coverage and shift schedules, and tune the lockout and rate limits with real data",
        consumers="Ana Whitfield; TrackFlow Tech",
        trigger="`POST /auth/login` (or `/auth/token`) issued a token.",
        emit_at="`services/auth.py` -> `login()` after `authenticate()` returns.",
        delivery=BATCH, window="hourly",
        delivery_reason="Staffing and support cover are planned over days. The security question (attacks) is answered by `login_failed`, which is streamed.",
        pii="none", pii_note="No personal data. The user is the pseudonymous `userId`.",
        properties=[
            S("user_role", "Role of the user who signed in.", enum=USER_ROLES, how="User.role."),
            S("method", "How the user authenticated. Only `password` exists today; the field keeps the schema stable if single sign-on is added.", enum=["password"], how="Constant today."),
            I("failures_before_success", "Failed attempts for this account in the previous 15 minutes.", minimum=0, how="Counted server-side from `login_failed` events for the same account."),
            B("is_first_login", "Whether this is the first sign-in of the account.", how="No earlier `login_succeeded` for this user."),
        ],
        example=dict(user_role="user", method="password", failures_before_success=1, is_first_login=False),
    ),
    dict(
        type="login_failed", klass="opportunity", category="authentication", source=["backend"],
        description="A sign-in attempt was refused.",
        hypothesis="whether failed sign-ins are forgotten passwords or an attack (many attempts, or many different accounts)",
        decision="enable rate limiting or account lockout, block abusive sources, or force password resets",
        consumers="TrackFlow Tech (security)",
        trigger="`POST /auth/login` (or `/auth/token`) answered 401 or 403.",
        emit_at="`services/auth.py` -> `authenticate()`, at each of its three refusals (unknown account, wrong password, disabled account).",
        delivery=STREAM, window=STREAM_WINDOW,
        delivery_reason="Credential stuffing is a burst that lasts minutes. To block it the security owner has to see it while it is happening, not the next morning.",
        throttle="Never dropped or sampled. Detection depends on counting every attempt. The cost is bounded by the login route's own rate.",
        pii="pseudonymized", pii_note="The email is never stored: only a keyed hash. The source network is a keyed hash of a truncated address. See the privacy section.",
        notes="The API answers the same message whether the account exists or not. The reason below is for internal telemetry only and must never be sent back to the client.",
        properties=[
            S("failure_reason", "Internal reason for the refusal.", enum=["unknown_account", "wrong_password", "account_disabled"], how="Branch of `authenticate()` that refused."),
            S("email_hash", "Keyed hash of the email that was tried, to count attempts per account without storing the address.", pattern=HASH16, pii="pseudonymized",
              how="First 16 hex characters of HMAC-SHA256(lowercased email, TELEMETRY_PEPPER). The pepper is a secret kept outside the repository."),
            S("source_network_hash", "Keyed hash of the caller's network (IPv4 truncated to /24, IPv6 to /48), to spot one source trying many accounts.", pattern=HASH16, null=True, required=False, pii="pseudonymized",
              how="First 16 hex characters of HMAC-SHA256(truncated address, TELEMETRY_PEPPER). The raw address is never stored. Retention 30 days."),
            I("attempts_in_window", "Failed attempts for this email hash in the last 15 minutes, including this one.", minimum=1, how="Counted server-side."),
        ],
        example=dict(failure_reason="wrong_password", email_hash="9f2c4a7b1d3e5f60", source_network_hash="1a2b3c4d5e6f7081", attempts_in_window=3),
    ),
    dict(
        type="login_attempt_failed", klass="opportunity", category="authentication", source=["frontend"],
        description="A sign-in attempt failed, seen from the browser: the user could not get in.",
        hypothesis="whether people fail to sign in because the credentials are wrong or because the service cannot be reached, as the user experiences it",
        decision="put effort into the password-help path when wrong credentials dominate, and escalate to engineering when network or service failures dominate",
        consumers="TrackFlow Tech; Ana Whitfield",
        trigger="The sign-in form submitted and `signIn()` threw.",
        emit_at="`uis/backoffice/lib/auth.ts` -> `signIn()`, in the failure branch. It is an authentication hook, so no page tracks it.",
        delivery=BATCH, window="hourly",
        delivery_reason="Deciding where to invest (password help or reliability) is a weekly conversation. The security signal that needs a fast answer is the streamed `login_failed` from the API.",
        throttle="One event per submit.",
        pii="none", pii_note="The email and the password the user typed are never captured, not even hashed: the browser cannot hold the server pepper. Only the failure class and a counter.",
        notes="This event is the user's view of a failure. It complements, and does not replace, the backend `login_failed`, which knows the internal reason and carries the keyed email hash.",
        properties=[
            S("failure_reason", "Why the attempt failed, as the browser can tell.", enum=["invalid_credentials", "network_error", "rate_limited", "service_unavailable", "storage_unavailable"],
              how="HTTP status of the answer: 400, 401 or 403 is `invalid_credentials`, no answer is `network_error`, 429 is `rate_limited`, 5xx is `service_unavailable`, and a browser that refuses to store the session is `storage_unavailable`."),
            I("attempt_number", "Consecutive failed attempts in this visit to the page, starting at 1.", minimum=1, maximum=50, how="Counter kept in memory, reset on success."),
        ],
        example=dict(failure_reason="invalid_credentials", attempt_number=2),
    ),
    dict(
        type="session_expired", klass="opportunity", category="authentication", source=["frontend"],
        description="A signed-in user was sent back to the login page because the session stopped being valid.",
        hypothesis="whether the 8-hour session matches real shift length, and how often operators are cut off mid-task",
        decision="change the session lifetime, add silent renewal, or warn the user before it ends",
        consumers="TrackFlow Tech; Ana Whitfield",
        trigger="A protected call answered 401 and the session was cleared.",
        emit_at="`uis/backoffice/lib/api-client.ts` -> `apiRequest()`, in the 401 branch before `redirectToLogin()`.",
        delivery=BATCH, window="hourly",
        delivery_reason="Changing the session lifetime is a configuration decision taken after looking at days of data, not minutes.",
        pii="none", pii_note="No personal data.",
        properties=[
            S("reason", "Why the session was no longer valid.", enum=["token_expired", "token_invalid", "password_changed", "account_removed"], how="Taken from the API's 401 message class."),
            I("session_age_seconds", "How long ago the session started, if known.", minimum=0, null=True, required=False, how="Now minus the sign-in time kept in the browser."),
            route_template("Route where the expiry was noticed."),
        ],
        example=dict(reason="token_expired", session_age_seconds=28804, route_template="/backoffice/inventory/orders/outbound"),
    ),
    dict(
        type="user_registered", klass="opportunity", category="authentication", source=["backend"],
        description="A new account was created.",
        hypothesis="how many accounts are created, how much optional profile data people give, and whether the legacy register route is still used",
        decision="simplify or extend the sign-up form, and retire `/auth/register` once nobody uses it",
        consumers="TrackFlow Tech; Ana Whitfield",
        trigger="`POST /users` or the deprecated `POST /auth/register` created an account.",
        emit_at="`services/routers/users.py` -> `register_user()` and `services/auth.py` -> `register_alias()`, after the account is stored.",
        delivery=BATCH, window="daily",
        delivery_reason="Sign-up volume is a daily or weekly figure and no one acts on a single registration.",
        pii="none", pii_note="Only whether optional fields were filled in. Their content is never captured.",
        properties=[
            S("initial_role", "Role given at creation. Registration always yields `user`.", enum=["user"], how="Constant."),
            B("has_name", "Whether a name was provided.", how="Payload field present."),
            B("has_phone", "Whether a phone number was provided.", how="Payload field present."),
            B("has_address", "Whether an address was provided.", how="Payload field present."),
            S("registration_channel", "Which endpoint was used.", enum=["users_endpoint", "legacy_register_alias"], how="Route that handled the request."),
        ],
        example=dict(initial_role="user", has_name=True, has_phone=False, has_address=False, registration_channel="users_endpoint"),
    ),
    dict(
        type="password_reset_requested", klass="opportunity", category="authentication", source=["backend"],
        description="Someone asked for a password reset link.",
        hypothesis="how many people forget their password, and whether unknown addresses are being probed",
        decision="review the password policy or add single sign-on, and spot account-enumeration attempts",
        consumers="TrackFlow Tech (security)",
        trigger="`POST /auth/forgot-password` was received (it always answers 200).",
        emit_at="`services/auth.py` -> `forgot_password()`, after the account lookup.",
        delivery=BATCH, window="hourly",
        delivery_reason="The decision is a policy review. The urgent attack signal is already covered by the streamed `login_failed`.",
        pii="pseudonymized", pii_note="Keyed email hash only. `account_found` is security-sensitive: it must stay in the restricted security dataset and never reach a dashboard or a client.",
        properties=[
            S("email_hash", "Keyed hash of the email the link was requested for.", pattern=HASH16, pii="pseudonymized",
              how="First 16 hex characters of HMAC-SHA256(lowercased email, TELEMETRY_PEPPER), the same method as in `login_failed`."),
            B("account_found", "Whether an active account exists for that email. Sensitive: restricted dataset only.", pii="sensitive", how="Result of the account lookup. The API response never reveals it."),
            B("throttled", "Whether the per-account cooldown suppressed the email.", how="Result of `register_reset_request()`."),
        ],
        example=dict(email_hash="0a1b2c3d4e5f6071", account_found=True, throttled=False),
    ),
    dict(
        type="password_reset_completed", klass="opportunity", category="authentication", source=["backend"],
        description="A password was changed through a reset link.",
        hypothesis="how many requested resets are completed and how long people take",
        decision="adjust the 30-minute link lifetime and the email wording, and judge email deliverability",
        consumers="TrackFlow Tech",
        trigger="`POST /auth/reset-password` accepted the link and changed the password.",
        emit_at="`services/auth.py` -> `reset_password()`, after `complete_password_reset()` succeeds.",
        delivery=BATCH, window="daily",
        delivery_reason="Link lifetime and wording are tuned from days of data.",
        pii="none", pii_note="No personal data.",
        properties=[
            I("token_age_seconds", "Seconds between the link being issued and being used.", minimum=0, how="Now minus the token's issue time."),
            B("sessions_revoked", "Whether sessions started before the reset were signed out (they always are).", how="Constant `true` today."),
        ],
        example=dict(token_age_seconds=420, sessions_revoked=True),
    ),
    dict(
        type="password_reset_rejected", klass="opportunity", category="authentication", source=["backend"],
        description="A reset link was refused (expired, already used, invalid or the account is unavailable).",
        hypothesis="why reset links fail, and whether the link lifetime is too short",
        decision="lengthen or shorten the link lifetime, or make the 'request a new link' path easier to find",
        consumers="TrackFlow Tech",
        trigger="`POST /auth/reset-password` answered 400.",
        emit_at="`services/auth.py` -> `reset_password()`, at its two refusals.",
        delivery=BATCH, window="daily",
        delivery_reason="Tuning the lifetime is a configuration change decided from days of data.",
        pii="none", pii_note="No personal data. The token itself is never captured.",
        properties=[
            S("rejection_reason", "Internal reason (never returned to the client, which always gets the same message).", enum=["expired", "already_used_or_superseded", "invalid_token", "account_unavailable"], how="Branch that refused the link."),
            I("token_age_seconds", "Seconds since the link was issued, when it could be read.", minimum=0, null=True, required=False, how="Token claims, if the signature was valid."),
        ],
        example=dict(rejection_reason="expired", token_age_seconds=2100),
    ),
    dict(
        type="password_changed", klass="opportunity", category="authentication", source=["backend"],
        description="A signed-in user changed their own password.",
        hypothesis="how often people rotate their password on their own",
        decision="decide whether to promote or enforce periodic password changes",
        consumers="TrackFlow Tech (security)",
        trigger="`POST /auth/change-password` succeeded.",
        emit_at="`services/auth.py` -> `change_password()`, after `update_user()`.",
        delivery=BATCH, window="daily",
        delivery_reason="A password rotation policy is decided over weeks of data, so a daily batch loses nothing.",
        pii="none", pii_note="No personal data.",
        properties=[
            S("user_role", "Role of the user.", enum=USER_ROLES, how="User.role."),
            B("sessions_revoked", "Whether older sessions were signed out (they always are).", how="Constant `true` today."),
        ],
        example=dict(user_role="manager", sessions_revoked=True),
    ),
    dict(
        type="password_change_rejected", klass="opportunity", category="authentication", source=["backend"],
        description="A password change was refused: wrong current password, same password, or a new password that breaks the rules.",
        hypothesis="whether users struggle with the password rules, or someone with a stolen session is guessing the current password",
        decision="improve the password hints in the form, and investigate accounts with many wrong current passwords",
        consumers="TrackFlow Tech (security)",
        trigger="`POST /auth/change-password` answered 400 or 422.",
        emit_at="`services/auth.py` -> `change_password()`, at its refusals.",
        delivery=BATCH, window="daily",
        delivery_reason="Form hints are improved from days of data. A guessing burst is caught by the streamed `login_failed` and `api_error_returned`.",
        pii="none", pii_note="No personal data. Passwords are never captured.",
        properties=[
            S("rejection_reason", "Why the change was refused.", enum=["wrong_current_password", "same_as_current", "invalid_new_password"], how="Branch that refused the request."),
            I("attempts_in_window", "Refusals for this user in the last 15 minutes, including this one.", minimum=1, how="Counted server-side."),
        ],
        example=dict(rejection_reason="wrong_current_password", attempts_in_window=2),
    ),
    dict(
        type="access_denied", klass="opportunity", category="authentication", source=["backend"],
        description="An authenticated user was refused access to something that is not theirs or that needs a higher role.",
        hypothesis="whether users probe accounts or administrator functions they should not reach",
        decision="review roles and permissions, and investigate users who keep trying",
        consumers="TrackFlow Tech (security)",
        trigger="A route answered 403.",
        emit_at="`services/auth.py` -> `require_self_or_admin()`, `require_admin()` and the disabled-account branch of `get_current_user()`; `services/routers/users.py` -> `update_user_credentials()` (role change by a non-admin).",
        delivery=BATCH, window="hourly",
        delivery_reason="Reviewing permissions is a periodic task. Hourly batches surface a probing user the same day.",
        pii="none", pii_note="No personal data. `target_user_id` is a pseudonymous identifier.",
        properties=[
            S("denial_reason", "Why access was refused.", enum=["not_owner", "admin_required", "account_disabled"], how="Check that refused."),
            S("resource", "Kind of resource the user tried to reach.", enum=["users", "profiles", "inventory", "incidents", "incident_analysis", "other"], how="Router that handled the request."),
            S("http_method", "HTTP method of the request.", enum=HTTP_METHODS, how="Request method."),
            route_template(),
            actor_role(),
            S("target_user_id", "The account the user tried to reach, when the route has one.", pattern="^[0-9a-f-]{36}$", null=True, required=False, pii="pseudonymized", how="`user_id` path parameter."),
        ],
        example=dict(denial_reason="not_owner", resource="users", http_method="GET", route_template="/users/{user_id}", actor_role="user", target_user_id="b9048e2a-ad79-4f10-b831-e5e2f9762629"),
    ),
    dict(
        type="user_role_changed", klass="opportunity", category="authentication", source=["backend"],
        description="An administrator changed the role of an account.",
        hypothesis="who gains or loses privileges, and when",
        decision="audit privileged access every quarter and spot unexpected promotions",
        consumers="TrackFlow Tech (security); Thomas Harry",
        trigger="`PUT /users/{user_id}` changed the `role` field.",
        emit_at="`services/routers/users.py` -> `update_user_credentials()`, after `update_user()`.",
        delivery=BATCH, window="daily",
        delivery_reason="Privileged-access reviews are periodic. The count is tiny and nobody has to react within the hour.",
        pii="pseudonymized", pii_note="Only pseudonymous user identifiers. The administrator is `userId` in the envelope.",
        properties=[
            S("target_user_id", "The account whose role changed.", pattern="^[0-9a-f-]{36}$", pii="pseudonymized", how="`user_id` path parameter."),
            S("previous_role", "Role before the change.", enum=USER_ROLES, how="User.role before the update."),
            S("new_role", "Role after the change.", enum=USER_ROLES, how="Value requested."),
        ],
        example=dict(target_user_id="b9048e2a-ad79-4f10-b831-e5e2f9762629", previous_role="user", new_role="manager"),
    ),
    # ================================================================= INCIDENTS ===
    dict(
        type="incident_created", klass="opportunity", category="incidents", source=["backend"],
        description="An incident was registered in the incident manager.",
        hypothesis="how many incidents are reported, by category, origin and branch",
        decision="staff customer-experience coverage by branch and prioritise root-cause work on the most frequent categories (Valentina)",
        consumers="Valentina Cruz (Customer Experience)",
        trigger="`POST /api/incidents` succeeded.",
        emit_at="`services/routers/incidents.py` -> `create_incident()`, after `commit_session()`.",
        delivery=BATCH, window="hourly",
        delivery_reason="Staffing and root-cause work are decided per day or per week. Hourly is fresh enough.",
        pii="none", pii_note="The description and title are free text that can contain names or emails, so they are never captured. Only the closed categories are.",
        properties=[
            S("category", "Incident category.", enum=INCIDENT_CATEGORIES, how="Incident.category."),
            S("origin", "Where the report came from.", enum=INCIDENT_ORIGINS, how="Incident.origin."),
            S("branch", "Branch the incident belongs to.", enum=list(BRANCHES), how="Incident.branch."),
            S("initial_status", "Status at creation.", enum=INCIDENT_STATUSES, how="Incident.status."),
        ],
        example=dict(category="lost_parcel", origin="customer", branch="la_warehouse", initial_status="open"),
    ),
    dict(
        type="incident_status_changed", klass="opportunity", category="incidents", source=["backend"],
        description="An incident moved to another status.",
        hypothesis="how long incidents take to move through their lifecycle and where they stall",
        decision="escalate or rebalance staffing when the time to resolve grows (Valentina)",
        consumers="Valentina Cruz (Customer Experience)",
        trigger="`PATCH /api/incidents/{incident_id}/status` succeeded.",
        emit_at="`services/routers/incidents.py` -> `update_status()`, after `commit_session()`.",
        delivery=BATCH, window="hourly",
        delivery_reason="Time to resolve is reviewed daily. Hourly batches are enough to see a pile-up in the same shift.",
        pii="none", pii_note="No personal data.",
        properties=[
            I("incident_id", "Numeric id of the incident.", minimum=1, how="Path parameter."),
            S("from_status", "Status before.", enum=INCIDENT_STATUSES, how="Incident.status before the update."),
            S("to_status", "Status after.", enum=INCIDENT_STATUSES, how="Value requested."),
            I("incident_age_seconds", "Seconds since the incident was created.", minimum=0, how="Now minus Incident.created_at."),
            S("category", "Incident category.", enum=INCIDENT_CATEGORIES, how="Incident.category."),
            S("branch", "Branch of the incident.", enum=list(BRANCHES), how="Incident.branch."),
        ],
        example=dict(incident_id=17, from_status="open", to_status="in_progress", incident_age_seconds=5400, category="delivery_failure", branch="zaragoza_office"),
    ),
    dict(
        type="incident_file_analyzed", klass="opportunity", category="incidents", source=["backend"],
        description="The helpdesk incident export went through the analysis.",
        hypothesis="how often the helpdesk export is analysed, how clean it is, and how satisfaction evolves",
        decision="agree data-quality fixes with the team that owns the helpdesk export, and track the satisfaction trend (Valentina)",
        consumers="Valentina Cruz (Customer Experience)",
        trigger="`POST /api/incidents/analyze` answered 200.",
        emit_at="`services/routers/incident_analysis.py` -> `analyze_incident_file()`, after `analyze_csv()` returns.",
        delivery=BATCH, window="daily",
        delivery_reason="The export is analysed a few times a week and the data-quality conversation is weekly.",
        pii="none", pii_note="Aggregates only. The file name, the rows and the customer emails never leave the analysis.",
        properties=[
            I("total_records", "Records in the file.", minimum=1, how="Analysis result."),
            I("valid_records", "Records that passed every rule.", minimum=0, how="Analysis result."),
            I("invalid_records", "Records that broke at least one rule.", minimum=0, how="Analysis result."),
            O("invalid_by_rule", "Count of records per broken rule. A record can appear under several rules.", RULE_KEYS, how="`AnalysisResult.rule_counts`."),
            N("average_satisfaction", "Average satisfaction score of valid closed incidents, or null when there are none.", minimum=1, maximum=5, null=True, how="`AnalysisResult.average_score`."),
            I("duration_ms", "Time the analysis took.", minimum=0, how="Measured around `analyze_csv()`."),
            I("file_size_bytes", "Size of the uploaded file.", minimum=1, how="`UploadFile.size`."),
        ],
        example=dict(total_records=100, valid_records=95, invalid_records=5, invalid_by_rule=dict(invalid_tracking_number=1, invalid_carrier=1, invalid_category=1, invalid_email=1, closed_without_score=1), average_satisfaction=3.06, duration_ms=84, file_size_bytes=11392),
    ),
    dict(
        type="incident_file_rejected", klass="opportunity", category="incidents", source=["backend"],
        description="An uploaded incident file could not be analysed.",
        hypothesis="why uploads fail, and whether the size limit or the export instructions are the problem",
        decision="improve the export instructions, or raise the upload limit if large files are the cause",
        consumers="Valentina Cruz (Customer Experience); TrackFlow Tech",
        trigger="`POST /api/incidents/analyze` answered 400, 413, 415 or 422.",
        emit_at="`services/routers/incident_analysis.py` -> `analyze_incident_file()`, at each early return.",
        delivery=BATCH, window="daily",
        delivery_reason="Instructions and limits are adjusted from weekly patterns, and the user already sees the reason on screen.",
        pii="none", pii_note="No file name and no content.",
        properties=[
            S("rejection_reason", "Why the file was refused.", enum=["empty_file", "no_file", "not_csv", "too_large", "not_multipart", "invalid_encoding", "missing_columns", "no_data_rows", "malformed_csv"], how="Mapped from the API error code and message class."),
            I("http_status", "HTTP status returned.", minimum=400, maximum=499, how="Response status."),
            I("file_size_bytes", "Size of the file, when it was received.", minimum=0, null=True, required=False, how="`UploadFile.size`."),
        ],
        example=dict(rejection_reason="missing_columns", http_status=422, file_size_bytes=2048),
    ),
    # ============================================================ NAVIGATION ===
    dict(
        type="page_viewed", klass="opportunity", category="navigation", source=["frontend"],
        description="A screen of the backoffice was shown.",
        hypothesis="which sections operators actually use, and in what order",
        decision="reorder the menu, retire sections nobody opens, and put engineering time into the most visited screens",
        consumers="TrackFlow Tech; Ana Whitfield",
        trigger="A route finished rendering (initial load or in-app navigation).",
        emit_at="One handler in the root layout of `uis/backoffice` (and of `uis/website` if its pages are tracked).",
        delivery=BATCH, window="hourly",
        delivery_reason="Menu and roadmap decisions are made over weeks.",
        throttle="One event per route change. Ignore a repeat of the same route within 2 seconds (double render, redirect).",
        pii="none", pii_note="Only the route template, never the real URL: query strings can carry a password-reset token.",
        properties=[
            S("section", "Section of the backoffice.", enum=SECTIONS, how="Map from the route."),
            route_template("Next.js route pattern, without query string."),
            S("referrer_section", "Section the user came from, `external` for another site, or null for a direct visit.", enum=SECTIONS + ["external"], null=True, how="Previous route in the app."),
            S("device_class", "Kind of device.", enum=DEVICE_CLASSES, how="Coarse class from the viewport width and pointer type. The user agent string is not stored."),
            B("is_authenticated", "Whether the user was signed in.", how="Token present in the browser."),
        ],
        example=dict(section="inventory_outbound_order", route_template="/backoffice/inventory/orders/outbound", referrer_section="inventory_products", device_class="tablet", is_authenticated=True),
    ),
    dict(
        type="flow_started", klass="opportunity", category="navigation", source=["frontend"],
        description="A user began a guided task (an order, an incident report, a registration).",
        hypothesis="how many times each guided task is started; this is the denominator of the abandonment rate",
        decision="choose which flow to simplify first",
        consumers="TrackFlow Tech; Ana Whitfield",
        trigger="The user first interacted with a flow's form (focused or edited a field).",
        emit_at="The form components under `uis/backoffice/app/**` (inbound, outbound, incident report, registration, password reset and change).",
        delivery=BATCH, window="hourly",
        delivery_reason="Flow redesign is a roadmap decision taken from days of data.",
        throttle="At most once per form visit.",
        pii="none", pii_note="No personal data and no field values.",
        properties=[
            S("flow_name", "Which guided task.", enum=FLOWS, how="Fixed per form."),
            S("entry_section", "Section where the flow was opened.", enum=SECTIONS, how="Current section."),
        ],
        example=dict(flow_name="outbound_order", entry_section="inventory_products"),
    ),
    dict(
        type="flow_abandoned", klass="opportunity", category="navigation", source=["frontend"],
        description="A user started a guided task and left without finishing it.",
        hypothesis="which flows are left unfinished and at which step",
        decision="redesign the step where people drop out",
        consumers="TrackFlow Tech; Ana Whitfield",
        trigger="The user left the page (navigation, tab close, session end) after `flow_started` and before the flow's success event.",
        emit_at="The same form components, on route change and on `visibilitychange`/`pagehide` (sent with `navigator.sendBeacon`).",
        delivery=BATCH, window="hourly",
        delivery_reason="Redesigning a flow is decided from days of data.",
        throttle="At most once per flow visit.",
        pii="none", pii_note="Only how many fields were touched, never their names beyond the closed list, and never their values.",
        properties=[
            S("flow_name", "Which guided task.", enum=FLOWS, how="Fixed per form."),
            S("last_step", "Last thing the user did before leaving.", enum=["form_opened", "field_edited", "validation_failed", "submit_failed"], how="Last state of the form."),
            I("elapsed_seconds", "Seconds between starting and leaving.", minimum=0, maximum=86400, how="Now minus the `flow_started` time."),
            I("fields_touched", "How many fields the user edited.", minimum=0, maximum=50, how="Count of changed inputs."),
        ],
        example=dict(flow_name="outbound_order", last_step="validation_failed", elapsed_seconds=95, fields_touched=3),
    ),
    dict(
        type="filter_applied", klass="opportunity", category="navigation", source=["frontend"],
        description="A user filtered the incident list.",
        hypothesis="which filters operators use and how narrow the results get",
        decision="set sensible default filters and add saved views for the most common ones",
        consumers="Valentina Cruz (Customer Experience); TrackFlow Tech",
        trigger="The user changed a filter on the incident list and the new results loaded.",
        emit_at="`uis/backoffice/app/backoffice/incidents/page.tsx`, after the list reloads.",
        delivery=BATCH, window="daily",
        delivery_reason="Default filters are a design decision made from weekly usage.",
        throttle="Debounce 800 ms and emit only the final filter state, so changing three selects in a row gives one event.",
        pii="none", pii_note="Filter names only, never the selected values beyond the closed lists.",
        properties=[
            S("surface", "Where the filter was used.", enum=["incident_list"], how="Fixed today."),
            A("filters_active", "Which filters are set after the change.", ["status", "origin", "branch", "category"], min_items=1, how="Current filter state."),
            I("result_count", "How many incidents match.", minimum=0, how="Length of the returned list."),
        ],
        example=dict(surface="incident_list", filters_active=["status", "branch"], result_count=12),
    ),
    dict(
        type="form_validation_failed", klass="opportunity", category="navigation", source=["frontend"],
        description="A form showed validation errors after a submit.",
        hypothesis="which fields cause the most mistakes",
        decision="reword labels, add hints, or relax constraints on those fields",
        consumers="TrackFlow Tech; Ana Whitfield",
        trigger="The user submitted a form and the browser-side or server-side validation produced errors.",
        emit_at="The form components under `uis/backoffice/app/**`, where `fieldErrors` is set.",
        delivery=BATCH, window="daily",
        delivery_reason="Form copy and constraints are refined from weekly patterns.",
        throttle="Once per submit, with the list of fields in error.",
        pii="none", pii_note="Field names from a closed list only. Never the values the user typed.",
        properties=[
            S("form_name", "Which form.", enum=FORMS, how="Fixed per form."),
            A("field_names", "Fields that showed an error.", FORM_FIELDS, min_items=1, how="Keys of the form's error state."),
            I("error_count", "Number of errors shown.", minimum=1, maximum=50, how="Count of errors."),
            I("attempt_number", "How many times the user has submitted this form visit, starting at 1.", minimum=1, maximum=50, how="Counter per form visit."),
        ],
        example=dict(form_name="outbound_order", field_names=["tracking_number", "quantity"], error_count=2, attempt_number=1),
    ),
    # ============================================================ PERFORMANCE ===
    dict(
        type="api_latency_recorded", klass="opportunity", category="performance", source=["backend"],
        description="The time one API request took, with its route and result.",
        hypothesis="whether the API slows down during shifts and which routes are slow",
        decision="optimise or scale the slow routes, and escalate when the 95th percentile breaches the target",
        consumers="TrackFlow Tech",
        trigger="Any API request finished.",
        emit_at="A request middleware in `services/main.py`, after the response is built. It also assigns the `requestId`.",
        delivery=STREAM, window=STREAM_WINDOW,
        delivery_reason="A slow API during a shift stops people working. The alert only helps if it arrives while the shift is still on.",
        throttle="High frequency. Always emit slow requests (above the slow threshold) and every 5xx; sample 1 in 10 of the fast successful ones and record the `sample_rate`, so counts can be re-weighted. Skip `/health`.",
        pii="none", pii_note="No personal data. Neither the query string nor the body is captured.",
        properties=[
            S("method", "HTTP method.", enum=HTTP_METHODS, how="Request method."),
            route_template(),
            I("status_code", "HTTP status of the response.", minimum=100, maximum=599, how="Response status."),
            I("duration_ms", "Time from receiving the request to sending the response.", minimum=0, how="Monotonic clock around the handler."),
            N("sample_rate", "Share of events of this kind that are emitted: 1 when always emitted, 0.1 when sampled one in ten.", exclusive_minimum=0, maximum=1, how="Sampling decision."),
            B("is_slow", "Whether the request was above the slow threshold.", how="`duration_ms` against the threshold (start at 1000 ms)."),
        ],
        example=dict(method="POST", route_template="/inventory/orders/outbound", status_code=201, duration_ms=212, sample_rate=0.1, is_slow=False),
    ),
    dict(
        type="page_load_recorded", klass="opportunity", category="performance", source=["frontend"],
        description="How fast a screen loaded for the user, with the standard web performance measures.",
        hypothesis="how fast pages load on the devices and connections used in each warehouse",
        decision="prioritise frontend optimisation, and decide whether warehouse tablets on slow connections need a lighter interface",
        consumers="TrackFlow Tech",
        trigger="A page finished loading, or an in-app navigation finished.",
        emit_at="One observer in the root layout of `uis/backoffice` using the browser's performance APIs.",
        delivery=BATCH, window="hourly",
        delivery_reason="Frontend optimisation is planned over weeks, so hourly aggregates are enough.",
        throttle="One event per navigation.",
        pii="none", pii_note="No personal data. Coarse device class only.",
        properties=[
            route_template("Next.js route pattern."),
            S("section", "Section of the backoffice.", enum=SECTIONS, how="Map from the route."),
            S("navigation_type", "`initial` for a full page load, `spa` for an in-app navigation.", enum=["initial", "spa"], how="Navigation timing."),
            I("ttfb_ms", "Time to first byte.", minimum=0, null=True, required=False, how="Navigation timing."),
            I("fcp_ms", "First contentful paint.", minimum=0, null=True, required=False, how="Paint timing."),
            I("lcp_ms", "Largest contentful paint.", minimum=0, null=True, required=False, how="Largest-contentful-paint observer."),
            N("cls", "Cumulative layout shift.", minimum=0, null=True, required=False, how="Layout-shift observer."),
            I("inp_ms", "Interaction to next paint, when the user interacted.", minimum=0, null=True, required=False, how="Event timing."),
            S("device_class", "Kind of device.", enum=DEVICE_CLASSES, how="Coarse class from the viewport and pointer type."),
            S("connection_type", "Effective connection type reported by the browser.", enum=CONNECTION_TYPES, how="`navigator.connection.effectiveType`, or `unknown`."),
        ],
        example=dict(route_template="/backoffice/inventory/products", section="inventory_products", navigation_type="initial", ttfb_ms=180, fcp_ms=640, lcp_ms=1210, cls=0.02, inp_ms=96, device_class="tablet", connection_type="4g"),
    ),
    dict(
        type="query_slow_detected", klass="opportunity", category="performance", source=["backend"],
        description="A database statement took longer than the threshold.",
        hypothesis="which statements dominate response time (the list endpoints run several queries per row today)",
        decision="add indexes, pagination or joins to the worst offenders",
        consumers="TrackFlow Tech",
        trigger="A statement ran longer than the slow-query threshold (start at 200 ms), or one request ran an unusual number of statements.",
        emit_at="A SQLAlchemy engine event hook in `services/database.py`, tied to the current request.",
        delivery=BATCH, window="daily",
        delivery_reason="Query tuning is engineering work planned from a day of data.",
        throttle="At most one event per statement fingerprint per minute.",
        pii="none", pii_note="The SQL text and its parameters are never captured, only a hash of the statement without values.",
        notes="Grounded in a real finding: `list_orders()` and `list_products()` run several queries for every row they return (the stock is computed per row).",
        properties=[
            S("operation", "Kind of statement.", enum=["select", "insert", "update", "delete", "other"], how="First keyword of the statement."),
            S("table", "Main table involved.", pattern="^[a-z_]{1,40}$", how="Parsed from the statement."),
            I("duration_ms", "Time the statement took.", minimum=0, how="Before and after the cursor execute event."),
            S("statement_fingerprint", "Hash of the statement with values removed, to group repeats.", pattern=HASH16, how="First 16 hex characters of SHA-256 of the normalised statement."),
            route_template("Route being served when the statement ran."),
            I("query_count_in_request", "How many statements the request had run when this one finished.", minimum=1, how="Per-request counter. A high value signals an N+1 pattern."),
        ],
        example=dict(operation="select", table="stockentry", duration_ms=340, statement_fingerprint="3d5f7a9b1c2e4f60", route_template="/inventory/orders", query_count_in_request=214),
    ),
    # ================================================= ERRORS AND PLATFORM HEALTH ===
    dict(
        type="frontend_error_caught", klass="opportunity", category="errors", source=["frontend"],
        description="An unexpected error was intercepted in the browser.",
        hypothesis="whether a release introduced errors, and which screens crash",
        decision="roll back or hotfix, and prioritise the screens that crash most",
        consumers="TrackFlow Tech",
        trigger="A `window` error, an unhandled promise rejection or a React error boundary fired.",
        emit_at="Global handlers in the root layout of `uis/backoffice` and the boundary in `uis/backoffice/app/error.tsx`.",
        delivery=STREAM, window=STREAM_WINDOW,
        delivery_reason="A regression after a release affects every operator at once. Spotting the spike within the minute shortens the rollback.",
        throttle="Group by `error_fingerprint`: at most one event per fingerprint per session per minute, with `occurrences` holding the count that was folded in.",
        pii="none", pii_note="The error message and the stack are never captured: they can contain user data. Only the error name and a hash of the stack.",
        properties=[
            S("error_source", "Where the error was intercepted.", enum=["window_error", "unhandled_rejection", "react_error_boundary"], how="Which handler fired."),
            S("error_name", "Class of the error, for example `TypeError`.", pattern="^[A-Za-z0-9_$.]{1,80}$", how="`error.name`."),
            S("error_fingerprint", "Hash of the normalised stack, to group the same error.", pattern=HASH16, how="16 hex characters of a 64-bit FNV-1a hash of the normalised stack (file paths without query strings, line numbers kept, values removed). It only groups repeats, so it does not need to be cryptographic and can be computed synchronously at capture time."),
            route_template("Route where it happened."),
            I("occurrences", "How many identical errors this event stands for.", minimum=1, how="Counter of folded duplicates."),
            B("handled_by_boundary", "Whether a React error boundary showed the fallback screen.", how="True when the boundary fired."),
        ],
        example=dict(error_source="react_error_boundary", error_name="TypeError", error_fingerprint="a1b2c3d4e5f60718", route_template="/backoffice/incidents", occurrences=3, handled_by_boundary=True),
    ),
    dict(
        type="api_error_returned", klass="opportunity", category="errors", source=["backend"],
        description="The API answered with a server error.",
        hypothesis="how often and where the API fails with server errors",
        decision="page the engineer on duty and open an incident for the failing route",
        consumers="TrackFlow Tech",
        trigger="A response with status 500 to 599 was sent.",
        emit_at="The exception handlers and the catch-all middleware in `services/main.py`, and `IncidentRoute` in `services/routers/incidents.py`.",
        delivery=STREAM, window=STREAM_WINDOW,
        delivery_reason="A failing route blocks the operators now. The page must go out within the minute.",
        throttle="Group by route and `error_code`: at most one event per group per 10 seconds during a storm; the count stays in `api_latency_recorded`.",
        pii="none", pii_note="The exception message is never captured, only its class name.",
        properties=[
            S("method", "HTTP method.", enum=HTTP_METHODS, how="Request method."),
            route_template(),
            I("status_code", "HTTP status returned.", minimum=500, maximum=599, how="Response status."),
            S("error_code", "Internal error class.", enum=["internal_error", "service_unavailable", "database_unavailable", "account_store_unavailable"], how="Which handler produced the response."),
            S("exception_type", "Class name of the exception, if there was one.", pattern="^[A-Za-z0-9_.]{1,100}$", null=True, required=False, how="`type(exc).__name__`."),
            I("duration_ms", "Time until the error response.", minimum=0, how="Same clock as `api_latency_recorded`."),
        ],
        example=dict(method="POST", route_template="/inventory/orders/outbound", status_code=503, error_code="database_unavailable", exception_type="OperationalError", duration_ms=5003),
    ),
    dict(
        type="network_request_failed", klass="opportunity", category="errors", source=["frontend"],
        description="A call from the browser to the API did not get an answer.",
        hypothesis="whether operators lose connectivity, and at which times",
        decision="invest in the network at the affected warehouse, and add an offline queue for orders if it is frequent",
        consumers="Ana Whitfield; TrackFlow Tech",
        trigger="`fetch` failed with a network error or a timeout.",
        emit_at="`uis/backoffice/lib/api-client.ts` -> `apiRequest()`, in the `catch` around `fetch`.",
        delivery=BATCH, window="hourly",
        delivery_reason="Events from a client that has lost its connection are buffered and sent when it returns, so they cannot be real time. A network investment is a decision taken over days.",
        throttle="Fold repeats of the same route and failure kind within 30 seconds into one event with `occurrences`.",
        pii="none", pii_note="No personal data.",
        properties=[
            route_template(),
            S("failure_kind", "What went wrong.", enum=["unreachable", "timeout"], how="Which `catch` branch."),
            I("duration_ms", "Time spent before giving up.", minimum=0, how="Clock around the call."),
            I("occurrences", "How many identical failures this event stands for.", minimum=1, how="Counter of folded duplicates."),
            B("online", "Whether the browser reported being online.", how="`navigator.onLine`."),
        ],
        example=dict(route_template="/inventory/orders/inbound", failure_kind="timeout", duration_ms=15000, occurrences=2, online=True),
    ),
    dict(
        type="email_delivery_failed", klass="opportunity", category="errors", source=["backend"],
        description="An email (a password-reset link) could not be sent.",
        hypothesis="whether password-reset emails are failing to leave the system",
        decision="fix the provider key or its configuration, or switch provider, before people are locked out",
        consumers="TrackFlow Tech",
        trigger="The mailer returned `False`.",
        emit_at="`services/mailer.py` -> `send_email()`, in each failure branch.",
        delivery=STREAM, window=STREAM_WINDOW,
        delivery_reason="While delivery is broken, a user who forgot their password cannot get back in. The fix is urgent.",
        pii="none", pii_note="No recipient address, no link and no key. Only the failure class and the provider's status code.",
        properties=[
            S("message_type", "Kind of email.", enum=["password_reset"], how="Fixed today."),
            S("provider", "Email provider in use.", enum=["resend", "console", "disabled"], how="`EMAIL_PROVIDER`."),
            S("failure_kind", "What went wrong.", enum=["provider_http_error", "network_error", "not_configured"], how="Branch of `send_email()`."),
            I("http_status", "Status returned by the provider, when there was one.", minimum=100, maximum=599, null=True, required=False, how="Provider response."),
        ],
        example=dict(message_type="password_reset", provider="resend", failure_kind="provider_http_error", http_status=403),
    ),
    dict(
        type="service_started", klass="opportunity", category="errors", source=["backend"],
        description="A service of the platform started.",
        hypothesis="how often services restart and with which configuration",
        decision="detect crash loops and configuration drift between environments",
        consumers="TrackFlow Tech",
        trigger="The service finished starting up.",
        emit_at="`services/main.py` -> the `lifespan` handler, after the startup checks; the UI containers from `uis/start.sh`.",
        delivery=STREAM, window=STREAM_WINDOW,
        delivery_reason="A crash loop shows up as a stream of restarts. Engineering has to see it within minutes, not the next day.",
        pii="none", pii_note="No personal data and no configuration values, only which kind of backend is in use.",
        properties=[
            S("service", "Which service started.", enum=["api", "ui_website", "ui_backoffice"], how="Fixed per service."),
            I("startup_duration_ms", "Time from process start to ready.", minimum=0, how="Clock around the startup."),
            S("database_kind", "Kind of database the API connected to.", enum=["postgresql", "sqlite"], null=True, required=False, how="Scheme of `DATABASE_URL`. API only."),
            S("email_provider", "Email provider configured.", enum=["resend", "console", "disabled"], null=True, required=False, how="`EMAIL_PROVIDER`. API only."),
            B("reload_enabled", "Whether the process runs with hot reload (development).", how="Startup flag."),
        ],
        example=dict(service="api", startup_duration_ms=1840, database_kind="postgresql", email_provider="resend", reload_enabled=False),
    ),
]


# --- storage mapping (Phase 3): how an event becomes a row of `telemetry_events` ----------------

# Severity of the row. Events that report a problem are `warn`; failures of the platform itself are `error`.
LEVELS = {
    "frontend_error_caught": "error",
    "api_error_returned": "error",
    "email_delivery_failed": "error",
    "stock_integrity_violation_detected": "error",
    "network_request_failed": "warn",
    "login_failed": "warn",
    "login_attempt_failed": "warn",
    "session_expired": "warn",
    "access_denied": "warn",
    "password_reset_rejected": "warn",
    "password_change_rejected": "warn",
    "direct_stock_edit_rejected": "warn",
    "inbound_order_rejected": "warn",
    "outbound_order_rejected": "warn",
    "incident_file_rejected": "warn",
    "stock_threshold_triggered": "warn",
    "inventory_discrepancy_detected": "warn",
    "query_slow_detected": "warn",
}

# The numeric property copied to the `value` column, so the next phase can SUM or AVG without opening the JSON.
VALUE_PROPERTY = {
    "inbound_order_created": "quantity",
    "outbound_order_created": "quantity",
    "stock_threshold_triggered": "quantity",
    "direct_stock_edit_rejected": "quantity",
    "inventory_discrepancy_detected": "quantity",
    "inbound_order_rejected": "quantity",
    "outbound_order_rejected": "quantity",
    "stock_integrity_violation_detected": "quantity",
    "login_attempt_failed": "attempt_number",
    "login_failed": "attempts_in_window",
    "password_change_rejected": "attempts_in_window",
    "session_expired": "session_age_seconds",
    "incident_status_changed": "incident_age_seconds",
    "incident_file_analyzed": "duration_ms",
    "filter_applied": "result_count",
    "flow_abandoned": "elapsed_seconds",
    "form_validation_failed": "error_count",
    "api_latency_recorded": "duration_ms",
    "api_error_returned": "duration_ms",
    "network_request_failed": "duration_ms",
    "query_slow_detected": "duration_ms",
    "page_load_recorded": "lcp_ms",
    "frontend_error_caught": "occurrences",
    "service_started": "startup_duration_ms",
}

for _event in EVENTS:
    _event["level"] = LEVELS.get(_event["type"], "info")
    _event["value_property"] = VALUE_PROPERTY.get(_event["type"])
