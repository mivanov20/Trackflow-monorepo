"use strict";
/**
 * Every instrumented call site records an event that the approved schema accepts: the fake `track` below
 * validates each event with the same allowlist code the real service uses, and fails the test otherwise.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const { createLoader } = require("./load-ts.cjs");

const USER = { id: "b9048e2a-ad79-4f10-b831-e5e2f9762629", name: "Ana", email: "ana@example.com", role: "manager" };
const store = new Map([
  ["trackflow-access-token", "token-123"],
  ["trackflow-auth-user", JSON.stringify(USER)],
]);
globalThis.window = {
  localStorage: {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  },
  location: { pathname: "/backoffice/inventory/orders/outbound", search: "", replace() {} },
  dispatchEvent() {},
};

const realTelemetry = createLoader()("lib/telemetry.ts");
let uuidCounter = 0;

/** A test environment: real modules, a fake API and a fake `track` that validates against the schema. */
function environment() {
  store.set("trackflow-access-token", "token-123");
  store.set("trackflow-auth-user", JSON.stringify(USER));
  const events = [];
  const completedFlows = [];
  const requestIds = [];
  const fakeTelemetry = {
    track(eventType, properties) {
      const schema = realTelemetry.EVENT_SCHEMAS?.[eventType];
      assert.ok(schema, `unknown event ${eventType}`);
      const problem = realTelemetry.propertiesProblem(schema, properties);
      assert.equal(problem, null, `${eventType}: ${problem} -> ${JSON.stringify(properties)}`);
      events.push({ eventType, properties, requestId: currentRequestId });
    },
    withRequestId(id, run) {
      const previous = currentRequestId;
      currentRequestId = id;
      try {
        return run();
      } finally {
        currentRequestId = previous;
      }
    },
    newRequestId: () => {
      const id = `00000000-0000-4000-8000-${String(++uuidCounter).padStart(12, "0")}`;
      requestIds.push(id);
      return id;
    },
    sessionAgeSeconds: () => 120,
    onTracked: () => () => undefined,
  };
  let currentRequestId = null;
  const load = createLoader({ telemetry: fakeTelemetry, "telemetry-flows": { completeFlow: (flow) => completedFlows.push(flow) } });
  return { load, events, completedFlows, requestIds };
}

realTelemetry.EVENT_SCHEMAS = createLoader()("lib/telemetry-schema.generated.ts").EVENT_SCHEMAS;

function sku(overrides = {}) {
  return { id: 7, name: "Basic tee", sku: "FSH-TEE-M", client_name: "Fashion Co", category: "fashion", warehouse: "LA", current_stock: 44, ...overrides };
}

function order(overrides = {}) {
  return {
    id: 1042, sku_id: 7, quantity: 120, order_type: "inbound", reference: "PO-2024-0098", exit_type: null, tracking_number: null,
    warehouse: "LA", created_at: "2026-11-21T14:00:00", user_uid: USER.id, sku: sku(), ...overrides,
  };
}

/** Replaces the module `fetch` the real api-client calls with a scripted answer. */
function fakeFetch(handler) {
  globalThis.fetch = async (url, init) => {
    const result = await handler(String(url), init);
    const body = result.body === undefined ? "" : typeof result.body === "string" ? result.body : JSON.stringify(result.body);
    return { ok: result.status >= 200 && result.status < 300, status: result.status, text: async () => body };
  };
}

const names = (events) => events.map((event) => event.eventType);

// --- inventory ----------------------------------------------------------------------------

test("a receipt records inbound_order_created with the aggregation dimensions", async () => {
  const { load, events } = environment();
  fakeFetch(() => ({ status: 201, body: order() }));
  const { inventoryApi } = load("lib/inventory.ts");
  await inventoryApi.createInboundOrder("t", { sku_id: 7, quantity: 120, reference: "PO-2024-0098", warehouse: "LA" });
  assert.deepEqual(names(events), ["inbound_order_created"]);
  assert.deepEqual(events[0].properties, {
    warehouse: "los_angeles", country: "US", client_id: "fashion-co", product_id: "FSH-TEE-M", product_category: "fashion",
    order_id: "1042", quantity: 120, stock_after: 44, has_client_reference: true,
  });
});

test("the reference text, tracking number and client name are never recorded", async () => {
  const { load, events } = environment();
  fakeFetch(() => ({ status: 201, body: order({ reference: "PO-SECRET", tracking_number: "TRK123" }) }));
  const { inventoryApi } = load("lib/inventory.ts");
  await inventoryApi.createInboundOrder("t", { sku_id: 7, quantity: 1, reference: "PO-SECRET", warehouse: "LA" });
  const text = JSON.stringify(events);
  assert.ok(!text.includes("PO-SECRET") && !text.includes("TRK123") && !text.includes("Fashion Co"));
});

test("a dispatch records outbound_order_created and, when stock falls under the minimum, stock_threshold_triggered once", async () => {
  const { load, events } = environment();
  fakeFetch(() => ({ status: 201, body: order({ order_type: "outbound", quantity: 3, exit_type: "dispatch", reference: null, tracking_number: "TRK1", sku: sku({ current_stock: 4 }) }) }));
  const { inventoryApi } = load("lib/inventory.ts");
  await inventoryApi.createOutboundOrder("t", { sku_id: 7, quantity: 3, exit_type: "dispatch", tracking_number: "TRK1", warehouse: "LA" });
  assert.deepEqual(names(events), ["outbound_order_created", "stock_threshold_triggered"]);
  assert.equal(events[0].properties.exit_type, "dispatch");
  assert.equal(events[0].properties.stock_after, 4);
  assert.deepEqual(events[1].properties, {
    warehouse: "los_angeles", country: "US", client_id: "fashion-co", product_id: "FSH-TEE-M", product_category: "fashion",
    quantity: 4, threshold: 6, severity: "below_minimum", order_id: "1042",
  });
});

test("the threshold is edge-triggered: no event while the stock was already low, one more when it reaches zero", async () => {
  const { load, events } = environment();
  const { inventoryApi } = load("lib/inventory.ts");
  const outbound = (quantity, after) => fakeFetch(() => ({ status: 201, body: order({ order_type: "outbound", quantity, exit_type: "loss", reference: null, sku: sku({ current_stock: after }) }) }));
  const payload = { sku_id: 7, quantity: 1, exit_type: "loss", tracking_number: null, warehouse: "LA" };
  outbound(2, 3); // 5 -> 3: both under 6, no new crossing
  await inventoryApi.createOutboundOrder("t", payload);
  assert.deepEqual(names(events), ["outbound_order_created"]);
  events.length = 0;
  outbound(3, 0); // 3 -> 0: reached zero
  await inventoryApi.createOutboundOrder("t", payload);
  assert.equal(events.find((e) => e.eventType === "stock_threshold_triggered").properties.severity, "out_of_stock");
});

test("a stock-count adjustment does not count as goods flow", async () => {
  const { load, events } = environment();
  fakeFetch(() => ({ status: 201, body: order({ order_type: "outbound", quantity: 1, exit_type: "loss", reference: null, sku: sku({ current_stock: 80 }) }) }));
  const { inventoryApi } = load("lib/inventory.ts");
  await inventoryApi.createOutboundOrder("t", { sku_id: 7, quantity: 1, exit_type: "loss", tracking_number: null, warehouse: "LA" }, { adjustment: true });
  assert.deepEqual(names(events), []);
});

test("an order refused for insufficient stock records outbound_order_rejected with what the system had", async () => {
  const { load, events } = environment();
  const { inventoryApi } = load("lib/inventory.ts");
  fakeFetch(() => ({ status: 200, body: [sku()] }));
  await inventoryApi.listProducts("t");
  fakeFetch(() => ({ status: 400, body: { detail: "Insufficient stock for SKU 'FSH-TEE-M'. Available: 12, requested: 30." } }));
  await assert.rejects(inventoryApi.createOutboundOrder("t", { sku_id: 7, quantity: 30, exit_type: "dispatch", tracking_number: "TRK9", warehouse: "LA" }));
  assert.deepEqual(names(events), ["outbound_order_rejected"]);
  assert.deepEqual(events[0].properties, {
    warehouse: "los_angeles", country: "US", client_id: "fashion-co", product_id: "FSH-TEE-M", product_category: "fashion",
    rejection_reason: "insufficient_stock", http_status: 400, exit_type: "dispatch", quantity: 30, available_quantity: 12, actor_role: "manager",
  });
});

test("an inbound receipt for an unknown SKU records inbound_order_rejected; an expired session records nothing here", async () => {
  const { load, events } = environment();
  const { inventoryApi } = load("lib/inventory.ts");
  fakeFetch(() => ({ status: 404, body: { detail: "SKU 999 was not found." } }));
  await assert.rejects(inventoryApi.createInboundOrder("t", { sku_id: 999, quantity: 5, reference: "x", warehouse: "ZGZ" }));
  assert.equal(events[0].eventType, "inbound_order_rejected");
  assert.equal(events[0].properties.rejection_reason, "unknown_product");
  assert.equal(events[0].properties.client_id, null);
  assert.equal(events[0].properties.warehouse, "zaragoza");
  events.length = 0;
  fakeFetch(() => ({ status: 401, body: { detail: "Your session is invalid or has expired. Please log in again." } }));
  await assert.rejects(inventoryApi.createInboundOrder("t", { sku_id: 7, quantity: 5, reference: "x", warehouse: "LA" }));
  assert.ok(!names(events).includes("inbound_order_rejected"));
});

test("a product payload that carries a stock value is refused before any request and recorded as direct_stock_edit_rejected", async () => {
  const { load, events } = environment();
  let requests = 0;
  fakeFetch(() => (requests += 1, { status: 201, body: sku() }));
  const { inventoryApi } = load("lib/inventory.ts");
  await assert.rejects(
    inventoryApi.createProduct("t", { name: "Tee", sku: "FSH-TEE-M", client_name: "Fashion Co", category: "fashion", warehouse: "LA", current_stock: 500 }),
    /Stock cannot be edited directly/,
  );
  assert.equal(requests, 0);
  assert.deepEqual(events[0].properties, {
    warehouse: "los_angeles", country: "US", client_id: "fashion-co", product_id: "FSH-TEE-M", product_category: "fashion",
    quantity: 500, attempted_operation: "set_stock", attempt_channel: "stock_field_in_payload", actor_role: "manager", http_method: "POST",
  });
});

test("a normal product creation records product_created", async () => {
  const { load, events } = environment();
  fakeFetch(() => ({ status: 201, body: sku({ current_stock: 0 }) }));
  const { inventoryApi } = load("lib/inventory.ts");
  await inventoryApi.createProduct("t", { name: "Tee", sku: "FSH-TEE-M", client_name: "Fashion Co", category: "fashion", warehouse: "LA" });
  assert.deepEqual(names(events), ["product_created"]);
});

test("a count below the system stock is settled with a loss order and recorded as a shortage", async () => {
  const { load, events } = environment();
  fakeFetch((url) => {
    if (url.endsWith("/inventory/products/7")) return { status: 200, body: sku({ current_stock: 80 }) };
    return { status: 201, body: order({ id: 55, order_type: "outbound", quantity: 6, exit_type: "loss", reference: null, sku: sku({ current_stock: 74 }) }) };
  });
  const { inventoryApi } = load("lib/inventory.ts");
  const result = await inventoryApi.recordStockCount("t", { skuId: 7, countedQuantity: 74, detectionSource: "cycle_count" });
  assert.equal(result.difference, -6);
  assert.deepEqual(names(events), ["inventory_discrepancy_detected"]);
  const p = events[0].properties;
  assert.equal(p.quantity, 6);
  assert.equal(p.system_quantity, 80);
  assert.equal(p.counted_quantity, 74);
  assert.equal(p.direction, "shortage");
  assert.match(p.count_id, /^CNT-\d{4}-[0-9A-F]{8}$/);
  assert.equal(p.detection_source, "cycle_count");
});

test("a count above the system stock is settled with a receipt and recorded as a surplus; a matching count records nothing", async () => {
  const { load, events, completedFlows } = environment();
  let stock = 10;
  fakeFetch((url) => {
    if (url.endsWith("/inventory/products/7")) return { status: 200, body: sku({ current_stock: stock }) };
    return { status: 201, body: order({ id: 56, quantity: 4, reference: "CNT", sku: sku({ current_stock: 14 }) }) };
  });
  const { inventoryApi } = load("lib/inventory.ts");
  await inventoryApi.recordStockCount("t", { skuId: 7, countedQuantity: 14, detectionSource: "spot_check" });
  assert.deepEqual(names(events), ["inventory_discrepancy_detected"]);
  assert.equal(events[0].properties.direction, "surplus");
  events.length = 0;
  stock = 14;
  await inventoryApi.recordStockCount("t", { skuId: 7, countedQuantity: 14, detectionSource: "spot_check" });
  assert.deepEqual(names(events), []);
  assert.deepEqual(completedFlows, ["stock_count"]);
});

test("a movement recorded in another warehouse than its SKU's is reported as an integrity violation", async () => {
  const { load, events } = environment();
  fakeFetch(() => ({ status: 201, body: order({ warehouse: "ZGZ", sku: sku({ warehouse: "LA" }) }) }));
  const { inventoryApi } = load("lib/inventory.ts");
  await inventoryApi.createInboundOrder("t", { sku_id: 7, quantity: 5, reference: "x", warehouse: "ZGZ" });
  const violation = events.find((e) => e.eventType === "stock_integrity_violation_detected");
  assert.equal(violation.properties.violation_type, "warehouse_mismatch");
  assert.equal(violation.properties.expected_warehouse, "los_angeles");
  assert.equal(violation.properties.observed_warehouse, "zaragoza");
});

test("events of one API call share the request id sent as X-Request-ID", async () => {
  const { load, events } = environment();
  let header = null;
  fakeFetch((_url, init) => ((header = init.headers.get("X-Request-ID")), { status: 201, body: order() }));
  const { inventoryApi } = load("lib/inventory.ts");
  await inventoryApi.createInboundOrder("t", { sku_id: 7, quantity: 1, reference: "x", warehouse: "LA" });
  assert.ok(header);
  assert.equal(events[0].requestId, header);
});

// --- incidents ----------------------------------------------------------------------------

function incident(overrides = {}) {
  return { id: 17, title: "Parcel lost for Ana Smith", description: "ana@example.com called", category: "lost_parcel", status: "open", origin: "customer", branch: "la_warehouse", created_at: "2026-11-21T12:00:00", updated_at: "2026-11-21T12:00:00", ...overrides };
}

test("creating an incident records only the closed categories, never the title or description", async () => {
  const { load, events } = environment();
  fakeFetch(() => ({ status: 201, body: incident() }));
  const { incidentsApi } = load("lib/incidents.ts");
  await incidentsApi.create("t", { title: "Parcel lost for Ana Smith", description: "ana@example.com called", category: "lost_parcel", origin: "customer", branch: "la_warehouse", status: "open" });
  assert.deepEqual(events[0].properties, { category: "lost_parcel", origin: "customer", branch: "la_warehouse", initial_status: "open" });
  assert.ok(!JSON.stringify(events).includes("ana@example.com"));
});

test("a status change records the status before and after, using the incident seen in the list", async () => {
  const { load, events } = environment();
  const { incidentsApi } = load("lib/incidents.ts");
  fakeFetch(() => ({ status: 200, body: [incident()] }));
  await incidentsApi.list("t", {});
  fakeFetch(() => ({ status: 200, body: incident({ status: "in_progress" }) }));
  await incidentsApi.updateStatus("t", 17, "in_progress");
  const change = events.find((e) => e.eventType === "incident_status_changed");
  assert.equal(change.properties.from_status, "open");
  assert.equal(change.properties.to_status, "in_progress");
  assert.equal(change.properties.category, "lost_parcel");
  assert.ok(change.properties.incident_age_seconds >= 0);
});

test("filters are recorded once, debounced, with names only", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { load, events } = environment();
  const { incidentsApi } = load("lib/incidents.ts");
  fakeFetch(() => ({ status: 200, body: [incident(), incident({ id: 18 })] }));
  await incidentsApi.list("t", {});
  await incidentsApi.list("t", { status: "open" });
  await incidentsApi.list("t", { status: "open", branch: "la_warehouse" });
  t.mock.timers.tick(799);
  assert.equal(events.length, 0);
  t.mock.timers.tick(1);
  assert.deepEqual(events.map((e) => e.properties), [{ surface: "incident_list", filters_active: ["status", "branch"], result_count: 2 }]);
  await incidentsApi.list("t", { status: "open", branch: "la_warehouse" }); // refresh with the same filters
  t.mock.timers.tick(2000);
  assert.equal(events.length, 1);
});

// --- incident file analysis ----------------------------------------------------------------

test("an analysed file records aggregates only", async () => {
  const { load, events } = environment();
  const summary = {
    source_file: "incidents-trackflow.csv", total_records: 100, valid_records: 95, invalid_records: 5,
    invalid_breakdown: [{ rule: "invalid_email", label: "x", description: "y", count: 2 }, { rule: "brand_new_rule", label: "x", description: "y", count: 1 }],
    satisfaction: { average: 3.06 },
  };
  fakeFetch(() => ({ status: 200, body: summary }));
  const { analysisApi } = load("lib/incident-analysis.ts");
  await analysisApi.analyze("t", new File(["x".repeat(11392)], "incidents-trackflow.csv"));
  const p = events[0].properties;
  assert.equal(events[0].eventType, "incident_file_analyzed");
  assert.deepEqual(p.invalid_by_rule, { invalid_email: 2 });
  assert.equal(p.file_size_bytes, 11392);
  assert.ok(!JSON.stringify(events).includes("incidents-trackflow.csv"));
});

test("a refused file records why, from the status and the wording of the answer", async () => {
  const { load, events } = environment();
  const { analysisApi } = load("lib/incident-analysis.ts");
  const cases = [
    [422, "The file is missing required column(s): status.", "missing_columns"],
    [413, "The file is larger than the 250 MB limit.", "too_large"],
    [415, "Only .csv files can be analysed.", "not_csv"],
    [400, "The file is empty.", "empty_file"],
  ];
  for (const [status, detail, reason] of cases) {
    fakeFetch(() => ({ status, body: { detail } }));
    await assert.rejects(analysisApi.analyze("t", new File(["x".repeat(2048)], "a.csv")));
    assert.equal(events.at(-1).eventType, "incident_file_rejected");
    assert.equal(events.at(-1).properties.rejection_reason, reason);
  }
});

// --- authentication and the API client -----------------------------------------------------

test("a failed sign-in records the class of failure and a counter, never the email or the password", async () => {
  const { load, events } = environment();
  const { signIn } = load("lib/auth.ts");
  fakeFetch(() => ({ status: 401, body: { detail: "Email or password is incorrect." } }));
  await assert.rejects(signIn("ana@example.com", "hunter2-secret"));
  await assert.rejects(signIn("ana@example.com", "hunter2-secret"));
  fakeFetch(() => { throw new TypeError("fetch failed"); });
  await assert.rejects(signIn("ana@example.com", "hunter2-secret"));
  assert.deepEqual(events.filter((e) => e.eventType === "login_attempt_failed").map((e) => e.properties), [
    { failure_reason: "invalid_credentials", attempt_number: 1 },
    { failure_reason: "invalid_credentials", attempt_number: 2 },
    { failure_reason: "network_error", attempt_number: 3 },
  ]);
  const text = JSON.stringify(events);
  assert.ok(!text.includes("ana@example.com") && !text.includes("hunter2-secret"));
});

test("registration records only which optional fields were filled in", async () => {
  const { load, events } = environment();
  fakeFetch(() => ({ status: 201, body: { id: "x", email: "new@example.com" } }));
  const { registerUser } = load("lib/auth.ts");
  await registerUser({ email: "new@example.com", password: "a-long-password", name: "Neo" });
  assert.deepEqual(events[0].properties, { initial_role: "user", has_name: true, has_phone: false, has_address: false, registration_channel: "users_endpoint" });
  assert.ok(!JSON.stringify(events).includes("new@example.com"));
});

test("an expired session is recorded before the session is cleared, with a reason taken from the answer", async () => {
  const { load, events } = environment();
  fakeFetch(() => ({ status: 401, body: { detail: "Your password was changed. Please sign in again." } }));
  const { apiRequest } = load("lib/api-client.ts");
  await assert.rejects(apiRequest("/inventory/products", { token: "token-123" }));
  const expired = events.find((e) => e.eventType === "session_expired");
  assert.deepEqual(expired.properties, { reason: "password_changed", session_age_seconds: 120, route_template: "/backoffice/inventory/orders/outbound" });
  store.set("trackflow-access-token", "token-123");
  store.set("trackflow-auth-user", JSON.stringify(USER));
});

test("repeated network failures to the same route within 30 s are folded into one event", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { load, events } = environment();
  globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
  const { apiRequest } = load("lib/api-client.ts");
  for (let i = 0; i < 3; i += 1) await assert.rejects(apiRequest("/inventory/products/12", { token: "t" }));
  assert.equal(events.length, 1);
  assert.deepEqual(events[0].properties.route_template, "/inventory/products/{id}");
  assert.equal(events[0].properties.failure_kind, "unreachable");
  t.mock.timers.tick(30_000);
  assert.equal(events.length, 2);
  assert.equal(events[1].properties.occurrences, 2);
});

// --- helpers and generated schema ----------------------------------------------------------

test("domain helpers map application values to the closed vocabularies", () => {
  const domain = createLoader()("lib/telemetry-domain.ts");
  assert.equal(domain.warehouseName("LA"), "los_angeles");
  assert.equal(domain.countryOf("ZGZ"), "ES");
  assert.equal(domain.clientIdOf("Électro Brand & Co."), "electro-brand-co");
  assert.equal(domain.clientIdOf("  "), null);
  assert.equal(domain.routeTemplate("/backoffice/incidents/17?token=secret"), "/backoffice/incidents/{id}");
  assert.equal(domain.sectionOf("/backoffice/inventory/stock-count"), "inventory_stock_count");
  assert.match(domain.fnv1a64Hex("TypeError|at render"), /^[0-9a-f]{16}$/);
  assert.equal(domain.productIdOf("bad sku!"), null);
  assert.equal(domain.safeErrorName("Type Error!"), "Error");
});

test("the browser's schema file is the one generated from the plan", () => {
  const generated = createLoader()("lib/telemetry-schema.generated.ts");
  assert.equal(generated.TELEMETRY_SCHEMA_VERSION, "1.2.0");
  for (const mandatory of ["inbound_order_created", "outbound_order_created", "stock_threshold_triggered", "direct_stock_edit_rejected", "inventory_discrepancy_detected"]) {
    assert.ok(generated.EVENT_SCHEMAS[mandatory], mandatory);
  }
});

test("a successful sign-in records login_succeeded with the failures of this visit, and no email", async () => {
  const { load, events } = environment();
  const { signIn } = load("lib/auth.ts");
  fakeFetch(() => ({ status: 401, body: { detail: "Email or password is incorrect." } }));
  await assert.rejects(signIn("ana@example.com", "wrong-pass-1"));
  fakeFetch(() => ({ status: 200, body: { access_token: "t2", token_type: "bearer", expires_in: 100, user: USER } }));
  await signIn("ana@example.com", "right-pass-1");
  const success = events.find((e) => e.eventType === "login_succeeded");
  assert.deepEqual(success.properties, { user_role: "manager", method: "password", failures_before_success: 1 });
  assert.ok(!JSON.stringify(events).includes("ana@example.com"));
});
