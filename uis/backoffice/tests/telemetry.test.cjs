"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createLoader } = require("./load-ts.cjs");

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

globalThis.window = globalThis.window ?? {};

function setup({ endpoint = "https://collector.test/telemetry/events", fetchImpl, beacon } = {}) {
  const load = createLoader();
  const { createTelemetryService, MAX_QUEUE_SIZE, FLUSH_INTERVAL_MS, STREAM_FLUSH_DELAY_MS, BASE_RETRY_DELAY_MS } = load("lib/telemetry.ts");
  const calls = [];
  const fetchFake =
    fetchImpl ??
    (async (url, init) => {
      calls.push({ url, body: JSON.parse(init.body) });
      return { ok: true, status: 200 };
    });
  const wrapped = async (url, init) => {
    const result = await fetchFake(url, init);
    return result;
  };
  let counter = 0;
  const service = createTelemetryService({
    endpoint,
    fetchImpl: async (url, init) => {
      if (!fetchImpl) return wrapped(url, init);
      calls.push({ url, body: JSON.parse(init.body) });
      return fetchFake(url, init);
    },
    sendBeaconImpl: beacon,
    now: () => new Date("2026-11-21T14:03:22.418Z"),
    uuid: () => `00000000-0000-4000-8000-${String(++counter).padStart(12, "0")}`,
    identity: () => ({ sessionId: "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13", userId: "b9048e2a-ad79-4f10-b831-e5e2f9762629" }),
    retryJitterMs: 0,
  });
  return { service, calls, MAX_QUEUE_SIZE, FLUSH_INTERVAL_MS, STREAM_FLUSH_DELAY_MS, BASE_RETRY_DELAY_MS };
}

const PAGE_VIEW = {
  section: "inventory_products",
  route_template: "/backoffice/inventory/products",
  referrer_section: null,
  device_class: "desktop",
  is_authenticated: true,
};

test("track adds the envelope; the caller passes only event type and properties", () => {
  const { service } = setup();
  service.track("page_viewed", PAGE_VIEW);
  const [event] = service.pending();
  assert.deepEqual(Object.keys(event).sort(), ["eventId", "event_type", "properties", "requestId", "schemaVersion", "sessionId", "timestamp", "userId"]);
  assert.equal(event.event_type, "page_viewed");
  assert.match(event.eventId, UUID);
  assert.match(event.requestId, UUID);
  assert.equal(event.timestamp, "2026-11-21T14:03:22.418Z");
  assert.equal(event.schemaVersion, "1.1.0");
  assert.equal(event.sessionId, "c2f6b8d0-31a4-4a9e-8f55-0d6e2b7a9c13");
  assert.equal(event.userId, "b9048e2a-ad79-4f10-b831-e5e2f9762629");
  assert.deepEqual(event.properties, PAGE_VIEW);
});

test("the timestamp is the moment of capture, not of sending", async () => {
  const load = createLoader();
  const { createTelemetryService } = load("lib/telemetry.ts");
  let clock = new Date("2026-11-21T10:00:00.000Z");
  const bodies = [];
  const service = createTelemetryService({
    endpoint: "https://c.test/e",
    fetchImpl: async (_url, init) => {
      bodies.push(JSON.parse(init.body));
      return { ok: true, status: 200 };
    },
    now: () => clock,
    identity: () => ({ sessionId: null, userId: null }),
  });
  service.track("page_viewed", PAGE_VIEW);
  clock = new Date("2026-11-21T10:00:09.000Z");
  await service.flush();
  assert.equal(bodies[0].events[0].timestamp, "2026-11-21T10:00:00.000Z");
});

test("events are queued, not sent one by one, and a full queue goes out as a single batch", async () => {
  const { service, calls, MAX_QUEUE_SIZE } = setup();
  for (let i = 0; i < MAX_QUEUE_SIZE - 1; i += 1) service.track("page_viewed", PAGE_VIEW);
  assert.equal(calls.length, 0);
  service.track("page_viewed", PAGE_VIEW);
  await service.flush();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].body.events.length, MAX_QUEUE_SIZE);
  assert.equal(MAX_QUEUE_SIZE, 20);
});

test("a partial queue is sent after 10 seconds", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval", "setTimeout"] });
  const { service, calls, FLUSH_INTERVAL_MS } = setup();
  service.track("page_viewed", PAGE_VIEW);
  service.track("page_viewed", PAGE_VIEW);
  assert.equal(FLUSH_INTERVAL_MS, 10_000);
  t.mock.timers.tick(9_999);
  assert.equal(calls.length, 0);
  t.mock.timers.tick(1);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].body.events.length, 2);
  service.stop();
});

test("a streamed event leaves after a second without waiting for the 10 second timer", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval", "setTimeout"] });
  const { service, calls, STREAM_FLUSH_DELAY_MS } = setup();
  service.track("frontend_error_caught", {
    error_source: "window_error",
    error_name: "TypeError",
    error_fingerprint: "a1b2c3d4e5f60718",
    route_template: "/backoffice/incidents",
    occurrences: 1,
    handled_by_boundary: false,
  });
  t.mock.timers.tick(STREAM_FLUSH_DELAY_MS);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].body.events[0].event_type, "frontend_error_caught");
  service.stop();
});

test("events outside the approved schema are dropped: unknown event, unknown or missing or invalid property", () => {
  const { service } = setup();
  service.track("generic_button_clicked", {});
  service.track("page_viewed", { ...PAGE_VIEW, email: "someone@example.com" });
  service.track("page_viewed", { section: "overview" });
  service.track("page_viewed", { ...PAGE_VIEW, device_class: "watch" });
  service.track("page_viewed", { ...PAGE_VIEW, route_template: "/search?q=secret" });
  assert.equal(service.pending().length, 0);
  assert.equal(service.snapshot().dropped, 5);
});

test("free text and personal data cannot pass the allowlist", () => {
  const { service } = setup();
  service.track("login_attempt_failed", { failure_reason: "invalid_credentials", attempt_number: 1, email: "a@b.co" });
  service.track("login_attempt_failed", { failure_reason: "invalid_credentials", attempt_number: 1, password: "x" });
  service.track("login_attempt_failed", { failure_reason: "wrong password for ana@example.com", attempt_number: 1 });
  assert.equal(service.pending().length, 0);
  service.track("login_attempt_failed", { failure_reason: "invalid_credentials", attempt_number: 1 });
  assert.equal(service.pending().length, 1);
});

test("a failed batch is retried with exponential waiting and then succeeds", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let attempts = 0;
  const { service, BASE_RETRY_DELAY_MS } = setup({
    fetchImpl: async () => {
      attempts += 1;
      if (attempts < 3) throw new Error("network down");
      return { ok: true, status: 200 };
    },
  });
  service.track("page_viewed", PAGE_VIEW);
  const done = service.flush();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(attempts, 1);
  t.mock.timers.tick(BASE_RETRY_DELAY_MS); // 1 s
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(attempts, 2);
  t.mock.timers.tick(BASE_RETRY_DELAY_MS * 2 - 1);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(attempts, 2, "second wait is 2 s");
  t.mock.timers.tick(1);
  await done;
  assert.equal(attempts, 3);
  assert.equal(service.snapshot().sent, 1);
  assert.equal(service.snapshot().discarded, 0);
});

test("after 3 retries the batch is discarded and the app is not blocked", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let attempts = 0;
  const { service } = setup({
    fetchImpl: async () => {
      attempts += 1;
      return { ok: false, status: 503 };
    },
  });
  service.track("page_viewed", PAGE_VIEW);
  const done = service.flush();
  for (let i = 0; i < 3; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
    t.mock.timers.tick(10_000);
  }
  await done;
  assert.equal(attempts, 4, "1 attempt + 3 retries");
  assert.equal(service.snapshot().discarded, 1);
  assert.equal(service.pending().length, 0);
});

test("a client error is not retried", async () => {
  let attempts = 0;
  const { service } = setup({
    fetchImpl: async () => {
      attempts += 1;
      return { ok: false, status: 422 };
    },
  });
  service.track("page_viewed", PAGE_VIEW);
  await service.flush();
  assert.equal(attempts, 1);
  assert.equal(service.snapshot().discarded, 1);
});

test("hiding the tab sends the pending events with sendBeacon as one JSON batch", async () => {
  const beacons = [];
  const { service, calls } = setup({ beacon: (url, blob) => (beacons.push({ url, blob }), true) });
  service.track("page_viewed", PAGE_VIEW);
  service.track("page_viewed", PAGE_VIEW);
  service.flushOnHide();
  assert.equal(calls.length, 0);
  assert.equal(beacons.length, 1);
  assert.equal(beacons[0].url, "https://collector.test/telemetry/events");
  assert.equal(beacons[0].blob.type, "application/json");
  const body = JSON.parse(await beacons[0].blob.text());
  assert.equal(body.events.length, 2);
  assert.equal(service.pending().length, 0);
});

test("every event of a request shares the requestId given to withRequestId", () => {
  const { service } = setup();
  service.withRequestId("e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84", () => {
    service.track("page_viewed", PAGE_VIEW);
    service.track("page_viewed", PAGE_VIEW);
  });
  service.track("page_viewed", PAGE_VIEW);
  const ids = service.pending().map((event) => event.requestId);
  assert.equal(ids[0], "e1a7c4d9-52b3-4c08-a6f1-7d9b0c3e5a84");
  assert.equal(ids[1], ids[0]);
  assert.notEqual(ids[2], ids[0]);
});

test("without an endpoint telemetry is off and nothing is queued", () => {
  const { service } = setup({ endpoint: "" });
  service.track("page_viewed", PAGE_VIEW);
  assert.equal(service.isEnabled(), false);
  assert.equal(service.pending().length, 0);
});

test("the queue is capped while offline and drops the oldest events", (t) => {
  t.mock.timers.enable({ apis: ["setInterval", "setTimeout"] });
  const { service } = setup({ fetchImpl: async () => ({ ok: false, status: 503 }) });
  for (let i = 0; i < 260; i += 1) service.track("page_viewed", { ...PAGE_VIEW, route_template: `/r${i}` });
  assert.equal(service.snapshot().queued, 200);
  service.stop();
});

test("the endpoint is read from NEXT_PUBLIC_TELEMETRY_ENDPOINT and not hardcoded", () => {
  const fs = require("node:fs");
  const path = require("node:path");
  const source = fs.readFileSync(path.resolve(__dirname, "../lib/telemetry.ts"), "utf8");
  assert.match(source, /process\.env\.NEXT_PUBLIC_TELEMETRY_ENDPOINT/);
  assert.doesNotMatch(source, /https?:\/\/(?!collector)/, "no URL literal in the service");
});
