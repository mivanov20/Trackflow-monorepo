"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createLoader } = require("./load-ts.cjs");

const stream = createLoader()("lib/rfp-stream.ts");
const { SseParser, parseTicketCreated, backoffDelay, RfpStreamClient, mergeTickets, feedTicketsFromList, statusText, EVENT_TICKET_CREATED } = stream;

const encoder = new TextEncoder();
const TICKET = (n, extra = {}) => ({ ticket_id: `tkt_${n}`, rfp_id: `rfp_${n}`, client_name: `Client ${n}`, client_country: "US", services_requested: ["warehousing"], departments_needed: ["warehouse"], status: "analyzing", created_at: `2026-07-24T14:3${n}:00Z`, ...extra });
const frame = (ticket, id) => `${id ? `id: ${id}\n` : ""}event: rfp_ticket_created\ndata: ${JSON.stringify(ticket)}\n\n`;

/** A response whose body the test controls: push text, end it, or break it like a dropped connection. Aborting the request breaks the body (as a browser does). */
function openStream(signal) {
  let controller;
  const body = new ReadableStream({ start: (c) => { controller = c; } });
  signal?.addEventListener("abort", () => { try { controller.error(new DOMException("aborted", "AbortError")); } catch { /* already closed */ } });
  return {
    response: { ok: true, status: 200, body },
    push: (text) => controller.enqueue(encoder.encode(text)),
    end: () => controller.close(),
    drop: () => controller.error(new TypeError("network error")),
  };
}

/** A fake `fetch`: each call takes the next scripted behaviour: a stream, a status, or a network failure. */
function scriptedFetch(script) {
  const calls = [];
  const streams = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, headers: init.headers, signal: init.signal });
    const step = script[Math.min(calls.length - 1, script.length - 1)];
    if (step === "network-error") throw new TypeError("Failed to fetch");
    if (typeof step === "number") return { ok: false, status: step, body: null };
    const opened = openStream(init.signal);
    streams.push(opened);
    if (typeof step === "function") step(opened);
    return opened.response;
  };
  return { fetchImpl, calls, streams };
}

const tick = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, label, timeout = 3000) {
  const started = Date.now();
  while (!check()) {
    if (Date.now() - started > timeout) throw new Error(`timed out waiting for ${label}`);
    await tick(5);
  }
}

function makeClient(overrides = {}) {
  const events = [];
  const statuses = [];
  const sleeps = [];
  const connected = [];
  const client = new RfpStreamClient({
    url: "http://api.test/rfp/events",
    getToken: () => "jwt-1",
    onEvent: (ticket, meta) => events.push({ ticket, id: meta.id }),
    onStatus: (status) => statuses.push(status),
    onConnected: (info) => connected.push(info),
    sleep: async (ms) => { sleeps.push(ms); await tick(1); },
    random: () => 0.5,
    backoff: { jitter: 0 },
    ...overrides,
  });
  return { client, events, statuses, sleeps, connected };
}

// ------------------------------------------------------------------------------------------------ the SSE parser
test("a named event with a JSON body is parsed with its id", () => {
  const parser = new SseParser();
  assert.deepEqual(parser.push('id: 7\nevent: rfp_ticket_created\ndata: {"ticket_id":"tkt_1"}\n\n'), [{ event: "rfp_ticket_created", data: '{"ticket_id":"tkt_1"}', id: "7" }]);
  assert.equal(parser.lastEventId, "7");
});

test("the parser copes with any chunking, CRLF, comments, several data lines and a byte order mark", () => {
  const parser = new SseParser();
  const text = '﻿: connected\r\nretry: 3000\r\n\r\nid: a1\r\nevent: rfp_ticket_created\r\ndata: {"a":\r\ndata: 1}\r\n\r\n: keep-alive\n\n';
  const events = [];
  for (const char of text) events.push(...parser.push(char)); // one character at a time: the worst chunking
  assert.deepEqual(events, [{ event: "rfp_ticket_created", data: '{"a":\n1}', id: "a1" }]);
  assert.equal(parser.retry, 3000);
});

test("a carriage return at the end of a chunk is not lost or doubled", () => {
  const parser = new SseParser();
  const first = parser.push("event: x\ndata: 1\r");
  const second = parser.push("\n\r");
  const third = parser.push("\nevent: y\ndata: 2\n\n");
  assert.deepEqual([...first, ...second, ...third].map((e) => [e.event, e.data]), [["x", "1"], ["y", "2"]]);
});

test("comments and events without data produce nothing; an event without a name is a message", () => {
  const parser = new SseParser();
  assert.deepEqual(parser.push(": keep-alive\n\nevent: ping\n\n"), []);
  assert.deepEqual(parser.push("data: hello\n\n"), [{ event: "message", data: "hello", id: null }]);
});

test("the last event id survives events that carry none", () => {
  const parser = new SseParser();
  parser.push("id: 5\nevent: e\ndata: 1\n\n");
  parser.push("event: e\ndata: 2\n\n");
  assert.equal(parser.lastEventId, "5");
});

// ------------------------------------------------------------------------------------------------ the payload
test("only a body that matches the ticket contract becomes a ticket", () => {
  assert.deepEqual(parseTicketCreated(JSON.stringify(TICKET(1))), TICKET(1));
  assert.equal(parseTicketCreated("not json"), null);
  assert.equal(parseTicketCreated("[1,2]"), null);
  assert.equal(parseTicketCreated(JSON.stringify({ rfp_id: "x", status: "analyzing" })), null); // no ticket id
  assert.equal(parseTicketCreated(JSON.stringify({ ticket_id: "t" })), null); // no status
  const loose = parseTicketCreated(JSON.stringify({ ticket_id: "t", status: "analyzing", services_requested: ["a", 3, null] }));
  assert.deepEqual(loose.services_requested, ["a"]);
  assert.equal(loose.client_name, null);
});

// ------------------------------------------------------------------------------------------------ the backoff
test("the backoff doubles from one second up to a cap", () => {
  const delays = [0, 1, 2, 3, 4, 5, 6, 7].map((failures) => backoffDelay(failures, { jitter: 0 }));
  assert.deepEqual(delays, [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000]);
});

test("the jitter spreads clients by plus or minus a fifth", () => {
  assert.equal(backoffDelay(2, { jitter: 0.2 }, () => 0), 3200);
  assert.equal(backoffDelay(2, { jitter: 0.2 }, () => 1), 4800);
  assert.equal(backoffDelay(2, { jitter: 0.2 }, () => 0.5), 4000);
});

// ------------------------------------------------------------------------------------------------ the client: fetch + ReadableStream with the JWT
test("the stream is opened with fetch, the JWT in the Authorization header and the event-stream Accept", async () => {
  const { fetchImpl, calls, streams } = scriptedFetch([(s) => s.push(frame(TICKET(1), "b-1"))]);
  const { client, events, statuses, connected } = makeClient({ fetchImpl });
  void client.start();
  await until(() => events.length === 1, "the first event");
  assert.equal(calls[0].url, "http://api.test/rfp/events");
  assert.equal(calls[0].headers.Authorization, "Bearer jwt-1");
  assert.equal(calls[0].headers.Accept, "text/event-stream");
  assert.deepEqual(Object.keys(calls[0].headers).map((name) => name.toLowerCase()).sort(), ["accept", "authorization"]); // nothing the API's CORS policy would refuse in the preflight
  assert.equal("Last-Event-ID" in calls[0].headers, false); // nothing to recover on the first connection
  assert.deepEqual(events[0], { ticket: TICKET(1), id: "b-1" });
  assert.deepEqual(statuses.map((s) => s.state).slice(0, 2), ["connecting", "connected"]);
  assert.deepEqual(connected, [{ reconnect: false }]);
  client.stop();
  assert.equal(client.current.state, "stopped");
});

test("only rfp_ticket_created events reach the feed, and malformed ones are ignored", async () => {
  const { fetchImpl } = scriptedFetch([(s) => {
    s.push("event: sla_threshold_alert\ndata: {\"ticket_id\":\"other\",\"status\":\"x\"}\n\n");
    s.push("event: rfp_ticket_created\ndata: {broken\n\n");
    s.push(frame(TICKET(2)));
  }]);
  const { client, events } = makeClient({ fetchImpl });
  void client.start();
  await until(() => events.length === 1, "the valid event");
  await tick(20);
  assert.deepEqual(events.map((e) => e.ticket.ticket_id), ["tkt_2"]);
  assert.equal(EVENT_TICKET_CREATED, "rfp_ticket_created");
  client.stop();
});

// ------------------------------------------------------------------------------------------------ reconnection with progressive backoff and recovery
test("after a drop the client reconnects with 1 s, 2 s, 4 s of backoff while the network is down, then recovers", async () => {
  let clock = 0;
  const { fetchImpl, calls, streams } = scriptedFetch([
    (s) => { s.push(frame(TICKET(1), "b-1")); },
    "network-error",
    "network-error",
    "network-error",
    (s) => { s.push(frame(TICKET(1), "b-1") + frame(TICKET(2), "b-2")); }, // the replay of what was missed, starting with a duplicate
  ]);
  const { client, events, statuses, sleeps, connected } = makeClient({ fetchImpl, now: () => (clock += 10) });
  void client.start();
  await until(() => events.length === 1, "the first event");
  streams[0].drop(); // the connection drops
  await until(() => events.length === 3, "the replayed events");
  assert.deepEqual(sleeps, [1000, 2000, 4000, 8000]); // the drop itself waits 1 s, then each failed attempt doubles
  assert.deepEqual(calls.slice(1).map((c) => c.headers["Last-Event-ID"]), ["b-1", "b-1", "b-1", "b-1"]); // every attempt asks for what it missed
  assert.ok(calls.slice(1).every((c) => Object.keys(c.headers).every((name) => ["accept", "authorization", "last-event-id"].includes(name.toLowerCase())))); // and only with headers the CORS policy allows
  assert.deepEqual(connected, [{ reconnect: false }, { reconnect: true }]); // recovery (the list refetch) runs on the reconnection
  assert.ok(statuses.some((s) => s.state === "reconnecting" && s.nextRetryMs === 4000 && s.attempt === 3));
  assert.equal(client.current.state, "connected");
  client.stop();
});

test("the backoff starts over after a connection that held, instead of staying escalated", async () => {
  let clock = 0;
  const { fetchImpl, streams } = scriptedFetch([(s) => s.push(frame(TICKET(1))), "network-error", (s) => s.push(frame(TICKET(2))), (s) => s.push(frame(TICKET(3)))]);
  const { client, events, sleeps } = makeClient({ fetchImpl, now: () => clock, stableAfterMs: 2000 });
  void client.start();
  await until(() => events.length === 1, "the first event");
  clock += 10; streams[0].drop(); // dropped almost at once: a failure
  await until(() => events.length === 2, "the second event");
  clock += 60_000; streams[1].drop(); // this one held for a minute: a success, not a failure
  await until(() => events.length === 3, "the third event");
  assert.deepEqual(sleeps, [1000, 2000, 1000]); // 1 s after the drop, 2 s after the failed attempt, and 1 s again after the connection that held: the counter started over
  client.stop();
});

test("a clean close by the server (its maximum lifetime) reconnects after a short fixed wait", async () => {
  let clock = 0;
  const { fetchImpl, streams, calls } = scriptedFetch([(s) => s.push(frame(TICKET(1), "b-1")), (s) => s.push(frame(TICKET(2), "b-2"))]);
  const { client, events, sleeps } = makeClient({ fetchImpl, now: () => clock, cleanCloseDelayMs: 250 });
  void client.start();
  await until(() => events.length === 1, "the first event");
  clock += 900_000;
  streams[0].end();
  await until(() => events.length === 2, "the event after the reconnection");
  assert.deepEqual(sleeps, [250]);
  assert.equal(calls[1].headers["Last-Event-ID"], "b-1");
  client.stop();
});

test("a silent connection (no bytes, not even keep-alive comments) is replaced", async () => {
  const { fetchImpl, calls } = scriptedFetch([() => {}, (s) => s.push(frame(TICKET(1)))]);
  const { client, events } = makeClient({ fetchImpl, staleAfterMs: 60 });
  void client.start();
  await until(() => events.length === 1, "the event after the watchdog replaced the dead connection");
  assert.equal(calls.length, 2);
  client.stop();
});

test("keep-alive comments keep a quiet connection from being replaced", async () => {
  const { fetchImpl, calls, streams } = scriptedFetch([() => {}]);
  const { client } = makeClient({ fetchImpl, staleAfterMs: 80 });
  void client.start();
  await until(() => streams.length === 1, "the connection");
  for (let i = 0; i < 5; i++) { streams[0].push(": keep-alive\n\n"); await tick(40); }
  assert.equal(calls.length, 1); // 200 ms of silence in terms of events, but alive
  client.stop();
});

test("a 401 stops the client for good: the session is gone, retrying cannot help", async () => {
  const { fetchImpl, calls } = scriptedFetch([401]);
  const { client, statuses, sleeps } = makeClient({ fetchImpl });
  await client.start();
  assert.equal(calls.length, 1);
  assert.equal(client.current.state, "unauthorized");
  assert.deepEqual(sleeps, []);
  assert.ok(statuses.at(-1).state === "unauthorized");
});

test("without a token there is no request at all", async () => {
  const { fetchImpl, calls } = scriptedFetch([(s) => s.end()]);
  const { client } = makeClient({ fetchImpl, getToken: () => null });
  await client.start();
  assert.equal(calls.length, 0);
  assert.equal(client.current.state, "unauthorized");
});

test("a server error is retried with backoff, not treated as the end", async () => {
  const { fetchImpl, calls } = scriptedFetch([503, 503, (s) => s.push(frame(TICKET(1)))]);
  const { client, events, sleeps } = makeClient({ fetchImpl });
  void client.start();
  await until(() => events.length === 1, "the event");
  assert.equal(calls.length, 3);
  assert.deepEqual(sleeps, [1000, 2000]);
  client.stop();
});

test("while the browser says it is offline the status says so, and `nudge` skips the wait when the network returns", async () => {
  let online = false;
  const waits = [];
  const { fetchImpl, calls } = scriptedFetch(["network-error", (s) => s.push(frame(TICKET(1)))]);
  const { client, events, statuses } = makeClient({
    fetchImpl,
    isOnline: () => online,
    sleep: (ms, signal) => new Promise((resolve) => { waits.push(ms); signal.addEventListener("abort", resolve); }), // waits until nudged
  });
  void client.start();
  await until(() => waits.length === 1, "the first wait");
  assert.equal(client.current.state, "offline");
  assert.match(statusText(client.current), /Offline/);
  online = true;
  client.nudge(); // the `online` event of the browser
  await until(() => events.length === 1, "the event right after the network came back");
  assert.equal(calls.length, 2);
  assert.ok(statuses.some((s) => s.state === "offline"));
  client.stop();
});

test("stop() ends a connection that is waiting for data and the loop", async () => {
  const { fetchImpl, streams } = scriptedFetch([() => {}]);
  const { client } = makeClient({ fetchImpl });
  const running = client.start();
  await until(() => streams.length === 1, "the connection");
  client.stop();
  await running; // resolves: nothing is left running
  assert.equal(client.current.state, "stopped");
});

// ------------------------------------------------------------------------------------------------ no duplicates
test("the same ticket never appears twice, whether it comes from the replay, the refetched list or a repeated event", () => {
  const live = (n) => ({ ...TICKET(n), fresh: true, source: "stream" });
  let feed = mergeTickets([], [live(1)]);
  feed = mergeTickets(feed, [live(1)]); // the same event twice
  const list = feedTicketsFromList([
    { ticket_id: "tkt_1", rfp_id: "rfp_1", status: "analyzing", client_name: "Client 1", client_country: "US", departments_needed: ["warehouse"], document_type: "informal_request", created_at: "2026-07-24T14:31:00Z" },
    { ticket_id: "tkt_2", rfp_id: "rfp_2", status: "analyzing", client_name: "Client 2", client_country: "ES", departments_needed: ["warehouse", "reverse"], document_type: "formal_rfp", created_at: "2026-07-24T14:32:00Z" },
    { ticket_id: "tkt_3", rfp_id: "rfp_3", status: "discarded", client_name: null, client_country: null, departments_needed: [], document_type: "vendor_pitch", created_at: "2026-07-24T14:33:00Z" },
    { ticket_id: "tkt_4", rfp_id: "rfp_4", status: "analyzing", client_name: null, client_country: null, departments_needed: [], document_type: null, created_at: "2026-07-24T14:34:00Z" },
  ]);
  feed = mergeTickets(feed, list); // the list refetched on reconnection, which includes the ticket already shown
  feed = mergeTickets(feed, [live(2)]); // and the replay delivers the missed one again
  assert.deepEqual(feed.map((t) => t.ticket_id), ["tkt_2", "tkt_1"]); // a rejected document and an unclassified one are not RFP tickets for the feed
  assert.equal(feed.find((t) => t.ticket_id === "tkt_1").fresh, true); // the first entry is kept: no flicker, no second card
});

test("tickets are ordered by the moment they were created even when the stream and the list write the time differently", () => {
  const a = { ...TICKET(1), ticket_id: "a", created_at: "2026-07-24T14:32:00Z", fresh: false, source: "stream" };
  const b = { ...TICKET(2), ticket_id: "b", created_at: "2026-07-24T14:31:59.999999+00:00", fresh: false, source: "recovered" };
  const c = { ...TICKET(3), ticket_id: "c", created_at: "2026-07-24T14:33:00+00:00", fresh: false, source: "recovered" };
  assert.deepEqual(mergeTickets([], [b, a, c]).map((t) => t.ticket_id), ["c", "a", "b"]);
});

test("the feed is newest first and bounded", () => {
  const many = Array.from({ length: 30 }, (_, i) => ({ ...TICKET(1), ticket_id: `t${i}`, created_at: `2026-07-24T10:${String(i).padStart(2, "0")}:00Z`, fresh: false, source: "stream" }));
  const feed = mergeTickets([], many);
  assert.equal(feed.length, 20);
  assert.equal(feed[0].ticket_id, "t29");
});

test("the connection pill says what is happening in plain words", () => {
  assert.equal(statusText({ state: "connected", attempt: 0, nextRetryMs: null }), "Live");
  assert.equal(statusText({ state: "connecting", attempt: 0, nextRetryMs: null }), "Connecting…");
  assert.equal(statusText({ state: "reconnecting", attempt: 3, nextRetryMs: 4000 }), "Reconnecting in 4 s (attempt 3)");
  assert.match(statusText({ state: "unauthorized", attempt: 0, nextRetryMs: null }), /sign in again/);
});
