"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createLoader } = require("./load-ts.cjs");

const load = createLoader();
const state = load("lib/chat-state.ts");
const socketModule = load("lib/chat-socket.ts");
const { applyEvent, initialChatState, streamingMessage } = state;
const { ChatSocketClient, socketUrl, socketStatusText } = socketModule;

const SID = "chat_0219";
const ev = (event, data) => ({ event, data: { session_id: SID, ...data } });
const tick = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, label, timeout = 3000) {
  const started = Date.now();
  while (!check()) {
    if (Date.now() - started > timeout) throw new Error(`timed out waiting for ${label}`);
    await tick(5);
  }
}
const run = (events, from = initialChatState(SID)) => events.reduce(applyEvent, from);

// ------------------------------------------------------------------------------------------------ the chat state
test("a turn builds a user message and an assistant message token by token, and the final text wins", () => {
  let current = run([ev("user_message", { message_id: "msg_1", text: "Where is my parcel?" }), ev("generation_started", { message_id: "msg_2" })]);
  assert.equal(current.generating, true);
  current = run([ev("token_chunk", { message_id: "msg_2", token: "Your ", sequence: 1 }), ev("token_chunk", { message_id: "msg_2", token: "parcel ", sequence: 2 })], current);
  assert.equal(current.messages[1].text, "Your parcel "); // the typing effect: the text grows with every token
  assert.equal(streamingMessage(current).message_id, "msg_2");
  current = run([ev("generation_completed", { message_id: "msg_2", text: "Your parcel is on its way." })], current);
  assert.deepEqual(current.messages.map((m) => [m.role, m.status, m.text]), [["user", "completed", "Where is my parcel?"], ["assistant", "completed", "Your parcel is on its way."]]);
  assert.equal(current.generating, false);
});

test("a repeated user message or a repeated start does not duplicate anything", () => {
  const once = [ev("user_message", { message_id: "msg_1", text: "hi" }), ev("generation_started", { message_id: "msg_2" })];
  const current = run([...once, ...once]);
  assert.equal(current.messages.length, 2);
});

test("old, repeated and unknown tokens are ignored", () => {
  let current = run([ev("generation_started", { message_id: "msg_2" }), ev("token_chunk", { message_id: "msg_2", token: "a", sequence: 1 }), ev("token_chunk", { message_id: "msg_2", token: "b", sequence: 2 })]);
  current = run([ev("token_chunk", { message_id: "msg_2", token: "b", sequence: 2 }), ev("token_chunk", { message_id: "msg_2", token: "a", sequence: 1 }), ev("token_chunk", { message_id: "msg_9", token: "x", sequence: 1 })], current);
  assert.equal(current.messages[0].text, "ab");
  assert.equal(current.messages.length, 1);
});

test("an interrupted message is kept with its text and no later token can change it; the redirected reply is a new message", () => {
  let current = run([
    ev("user_message", { message_id: "msg_1", text: "Where is my order?" }),
    ev("generation_started", { message_id: "msg_2" }),
    ev("token_chunk", { message_id: "msg_2", token: "Let me ", sequence: 1 }),
    ev("token_chunk", { message_id: "msg_2", token: "check ", sequence: 2 }),
    ev("generation_interrupted", { message_id: "msg_2", status: "interrupted", text: "Let me check " }),
  ]);
  assert.equal(current.status, "interrupted");
  assert.equal(current.generating, false);
  current = run([ev("token_chunk", { message_id: "msg_2", token: "your tracking number", sequence: 3 })], current); // a straggler of the old generation
  assert.equal(current.messages[1].text, "Let me check ");
  assert.equal(current.messages[1].status, "interrupted");
  current = run([ev("user_message", { message_id: "msg_3", text: "wait, I want to make a return" }), ev("generation_started", { message_id: "msg_4" }), ev("token_chunk", { message_id: "msg_4", token: "Sure ", sequence: 1 })], current);
  assert.deepEqual(current.messages.map((m) => [m.message_id, m.status, m.text]), [
    ["msg_1", "completed", "Where is my order?"],
    ["msg_2", "interrupted", "Let me check "], // still there, untouched
    ["msg_3", "completed", "wait, I want to make a return"],
    ["msg_4", "streaming", "Sure "],
  ]);
  assert.equal(current.status, "active");
});

test("a snapshot restores the thread, and the tokens it already contains are not applied twice", () => {
  const snapshot = ev("session_snapshot", {
    status: "active",
    messages: [
      { message_id: "msg_1", role: "user", text: "Where is my parcel?", status: "completed", token_sequence: 0 },
      { message_id: "msg_2", role: "assistant", text: "Your parcel is ", status: "streaming", token_sequence: 3 },
    ],
  });
  let current = run([snapshot], initialChatState(SID));
  assert.equal(current.hydrated, true);
  assert.equal(current.generating, true);
  current = run([ev("token_chunk", { message_id: "msg_2", token: "is ", sequence: 3 }), ev("token_chunk", { message_id: "msg_2", token: "on ", sequence: 4 }), ev("token_chunk", { message_id: "msg_2", token: "its way.", sequence: 5 })], current);
  assert.equal(current.messages[1].text, "Your parcel is on its way."); // the repeated token 3 did not duplicate text; 4 and 5 continued
});

test("a snapshot replaces what the client had: a reconnection shows the conversation as the server has it", () => {
  const stale = run([ev("user_message", { message_id: "msg_old", text: "stale local message" })]);
  const current = run([ev("session_snapshot", { status: "interrupted", messages: [{ message_id: "msg_1", role: "user", text: "kept on the server", status: "completed" }] })], stale);
  assert.deepEqual(current.messages.map((m) => m.message_id), ["msg_1"]);
  assert.equal(current.status, "interrupted");
});

test("a failed generation keeps its text and reports the error, and errors from the server are surfaced", () => {
  let current = run([ev("generation_started", { message_id: "msg_2" }), ev("token_chunk", { message_id: "msg_2", token: "part", sequence: 1 }), ev("generation_failed", { message_id: "msg_2", error: "The support agent could not answer right now." })]);
  assert.equal(current.messages[0].status, "failed");
  assert.equal(current.messages[0].text, "part");
  assert.equal(current.lastError, "The support agent could not answer right now.");
  assert.equal(current.generating, false);
  current = run([{ event: "error", data: { code: "generation_in_progress", message: "The agent is still answering." } }], current);
  assert.equal(current.lastError, "The agent is still answering.");
  assert.equal(run([{ event: "pong", data: {} }, { event: "whatever", data: {} }]).messages.length, 0);
});

// ------------------------------------------------------------------------------------------------ the socket client
function harness(overrides = {}) {
  const sockets = [];
  const events = [];
  const statuses = [];
  const sleeps = [];
  const restored = [];
  class FakeSocket {
    constructor(url) {
      this.url = url;
      this.readyState = 0;
      this.sent = [];
      this.closedWith = null;
      this.onopen = this.onmessage = this.onclose = this.onerror = null;
      sockets.push(this);
    }
    send(data) { this.sent.push(JSON.parse(data)); }
    close(code = 1000) { this.closedWith = code; this.readyState = 3; this.onclose?.({ code }); }
    open() { this.readyState = 1; this.onopen?.({}); }
    receive(event) { this.onmessage?.({ data: JSON.stringify(event) }); }
    drop(code = 1006) { this.readyState = 3; this.onclose?.({ code }); }
  }
  const client = new ChatSocketClient({
    url: socketUrl("http://api.test", SID),
    getToken: () => "jwt-1",
    onEvent: (event) => events.push(event),
    onStatus: (status) => statuses.push(status),
    onRestored: (info) => restored.push(info),
    createSocket: (url) => new FakeSocket(url),
    sleep: async (ms) => { sleeps.push(ms); await tick(1); },
    random: () => 0.5,
    backoff: { jitter: 0 },
    pingIntervalMs: 1_000_000,
    staleAfterMs: 1_000_000,
    ...overrides,
  });
  return { client, sockets, events, statuses, sleeps, restored };
}
const SNAPSHOT = (messages = []) => ev("session_snapshot", { status: "active", messages });

test("the socket address is the session's own and the JWT is not in it", () => {
  assert.equal(socketUrl("http://localhost:8000", "chat_0219"), "ws://localhost:8000/chat/sessions/chat_0219/ws");
  assert.equal(socketUrl("https://api.example.com/", "chat_0219"), "wss://api.example.com/chat/sessions/chat_0219/ws");
});

test("the JWT goes as the first frame, and the client is connected only once the thread has been restored", async () => {
  const { client, sockets, statuses, events, restored } = harness();
  void client.start();
  await until(() => sockets.length === 1, "the socket");
  assert.equal(sockets[0].url, "ws://api.test/chat/sessions/chat_0219/ws"); // no token in the URL
  sockets[0].open();
  assert.deepEqual(sockets[0].sent[0], { event: "auth", data: { token: "jwt-1" } });
  assert.equal(client.send("user_message", { session_id: SID, text: "hello" }), false); // not before the thread is restored
  assert.equal(client.current.state, "connecting");
  sockets[0].receive(SNAPSHOT());
  assert.equal(client.current.state, "connected");
  assert.deepEqual(restored, [{ reconnect: false }]);
  assert.equal(client.send("user_message", { session_id: SID, text: "hello" }), true);
  assert.deepEqual(sockets[0].sent.at(-1), { event: "user_message", data: { session_id: SID, text: "hello" } });
  assert.equal(events[0].event, "session_snapshot");
  assert.deepEqual(statuses.map((s) => s.state).slice(0, 2), ["connecting", "connected"]);
  client.stop();
});

test("the query mode puts the token in the URL and sends no auth frame", async () => {
  const { client, sockets } = harness({ authMode: "query" });
  void client.start();
  await until(() => sockets.length === 1, "the socket");
  sockets[0].open();
  assert.equal(sockets[0].url, "ws://api.test/chat/sessions/chat_0219/ws?token=jwt-1");
  assert.deepEqual(sockets[0].sent, []);
  client.stop();
});

test("events reach the state; pongs and garbage do not", async () => {
  const { client, sockets, events } = harness();
  void client.start();
  await until(() => sockets.length === 1, "the socket");
  sockets[0].open();
  sockets[0].receive(SNAPSHOT());
  sockets[0].receive({ event: "pong", data: {} });
  sockets[0].onmessage({ data: "not json" });
  sockets[0].onmessage({ data: JSON.stringify([1, 2]) });
  sockets[0].receive(ev("token_chunk", { message_id: "msg_2", token: "Your", sequence: 7 }));
  assert.deepEqual(events.map((e) => e.event), ["session_snapshot", "token_chunk"]);
  client.stop();
});

test("after a drop it reconnects to the SAME session with 1 s, 2 s, 4 s of backoff and restores the thread from the snapshot", async () => {
  let clock = 0;
  const { client, sockets, sleeps, restored, events } = harness({ now: () => (clock += 10) });
  void client.start();
  await until(() => sockets.length === 1, "the first socket");
  sockets[0].open();
  sockets[0].receive(SNAPSHOT([{ message_id: "msg_1", role: "user", text: "hi", status: "completed" }]));
  sockets[0].drop(1006); // the connection drops
  await until(() => sockets.length === 2, "the second socket");
  sockets[1].drop(1006); // the network is still down: it never even opens
  await until(() => sockets.length === 3, "the third socket");
  sockets[2].drop(1006);
  await until(() => sockets.length === 4, "the fourth socket");
  sockets[3].open();
  sockets[3].receive(SNAPSHOT([{ message_id: "msg_1", role: "user", text: "hi", status: "completed" }, { message_id: "msg_2", role: "assistant", text: "Hello!", status: "completed" }]));
  assert.deepEqual(sleeps, [1000, 2000, 4000]); // 1 s after the drop, then each failed attempt doubles
  assert.ok(sockets.every((socket) => socket.url === sockets[0].url)); // always the same session
  assert.deepEqual(restored, [{ reconnect: false }, { reconnect: true }]);
  assert.equal(client.current.state, "connected");
  const final = events.reduce(applyEvent, initialChatState(SID));
  assert.deepEqual(final.messages.map((m) => m.text), ["hi", "Hello!"]); // the thread, not an empty chat
  client.stop();
});

test("a connection that held for a while starts the backoff over", async () => {
  let clock = 0;
  const { client, sockets, sleeps } = harness({ now: () => clock, stableAfterMs: 3000 });
  void client.start();
  await until(() => sockets.length === 1, "socket 1");
  sockets[0].open();
  sockets[0].receive(SNAPSHOT());
  clock += 60_000;
  sockets[0].drop(1006);
  await until(() => sockets.length === 2, "socket 2");
  sockets[1].drop(1006);
  await until(() => sockets.length === 3, "socket 3");
  assert.deepEqual(sleeps, [1000, 1000]);
  client.stop();
});

test("a refused token or a missing conversation stops for good; a missing token never even connects", async () => {
  for (const [code, state] of [[4401, "unauthorized"], [4404, "unavailable"], [4409, "unavailable"]]) {
    const { client, sockets, sleeps } = harness();
    const running = client.start();
    await until(() => sockets.length === 1, "the socket");
    sockets[0].open();
    sockets[0].drop(code);
    await running;
    assert.equal(client.current.state, state);
    assert.equal(sockets.length, 1);
    assert.deepEqual(sleeps, []);
  }
  const { client, sockets } = harness({ getToken: () => null });
  await client.start();
  assert.equal(sockets.length, 0);
  assert.equal(client.current.state, "unauthorized");
});

test("the client pings, and a silent connection is replaced", async () => {
  const { client, sockets } = harness({ pingIntervalMs: 15, staleAfterMs: 80 });
  void client.start();
  await until(() => sockets.length === 1, "the socket");
  sockets[0].open();
  sockets[0].receive(SNAPSHOT());
  await until(() => sockets[0].sent.some((frame) => frame.event === "ping"), "a ping");
  await until(() => sockets.length === 2, "the replacement of the silent connection"); // no pong, nothing at all: after staleAfterMs it is closed and replaced
  assert.equal(sockets[0].closedWith, 4000);
  client.stop();
});

test("a pong keeps a quiet connection alive", async () => {
  const { client, sockets } = harness({ pingIntervalMs: 1_000_000, staleAfterMs: 90 });
  void client.start();
  await until(() => sockets.length === 1, "the socket");
  sockets[0].open();
  sockets[0].receive(SNAPSHOT());
  for (let i = 0; i < 5; i++) { sockets[0].receive({ event: "pong", data: {} }); await tick(40); }
  assert.equal(sockets.length, 1);
  client.stop();
});

test("nothing can be sent while reconnecting, and the browser coming back online skips the wait", async () => {
  let online = false;
  const waits = [];
  const { client, sockets } = harness({ isOnline: () => online, sleep: (ms, signal) => new Promise((resolve) => { waits.push(ms); signal.addEventListener("abort", resolve); }) });
  void client.start();
  await until(() => sockets.length === 1, "the socket");
  sockets[0].open();
  sockets[0].receive(SNAPSHOT());
  sockets[0].drop(1006);
  await until(() => waits.length === 1, "the wait");
  assert.equal(client.current.state, "offline");
  assert.equal(client.send("user_message", { text: "lost?" }), false); // the caller is told: it was NOT sent
  online = true;
  client.nudge();
  await until(() => sockets.length === 2, "the immediate reconnection");
  client.stop();
});

test("stop() closes the socket and the loop", async () => {
  const { client, sockets } = harness();
  const running = client.start();
  await until(() => sockets.length === 1, "the socket");
  sockets[0].open();
  sockets[0].receive(SNAPSHOT());
  client.stop();
  await running;
  assert.equal(sockets[0].closedWith, 1000);
  assert.equal(client.current.state, "stopped");
});

test("the connection pill says what is happening in plain words", () => {
  assert.equal(socketStatusText({ state: "connected", attempt: 0, nextRetryMs: null }), "Connected");
  assert.equal(socketStatusText({ state: "reconnecting", attempt: 3, nextRetryMs: 4000 }), "Reconnecting in 4 s (attempt 3)");
  assert.match(socketStatusText({ state: "offline", attempt: 1, nextRetryMs: 1000 }), /Offline/);
  assert.match(socketStatusText({ state: "unauthorized", attempt: 0, nextRetryMs: null }), /sign in again/);
});

// ------------------------------------------------------------------------------------------------ the client and the state together: the reconnection in the middle of an answer
test("a reconnection in the middle of an answer ends with the right text, no repeats, and the history in place", async () => {
  let clock = 0;
  const { client, sockets, events } = harness({ now: () => (clock += 10) });
  void client.start();
  await until(() => sockets.length === 1, "socket 1");
  sockets[0].open();
  sockets[0].receive(SNAPSHOT());
  for (const e of [ev("user_message", { message_id: "msg_1", text: "Where is my parcel?" }), ev("generation_started", { message_id: "msg_2" }), ev("token_chunk", { message_id: "msg_2", token: "Your ", sequence: 1 }), ev("token_chunk", { message_id: "msg_2", token: "parcel ", sequence: 2 }), ev("token_chunk", { message_id: "msg_2", token: "is ", sequence: 3 })]) sockets[0].receive(e);
  sockets[0].drop(1006);
  await until(() => sockets.length === 2, "socket 2");
  sockets[1].open();
  sockets[1].receive(SNAPSHOT([{ message_id: "msg_1", role: "user", text: "Where is my parcel?", status: "completed" }, { message_id: "msg_2", role: "assistant", text: "Your parcel is on ", status: "streaming", token_sequence: 4 }]));
  for (const e of [ev("token_chunk", { message_id: "msg_2", token: "on ", sequence: 4 }), ev("token_chunk", { message_id: "msg_2", token: "its way.", sequence: 5 }), ev("generation_completed", { message_id: "msg_2", text: "Your parcel is on its way." })]) sockets[1].receive(e);
  const final = events.reduce(applyEvent, initialChatState(SID));
  assert.deepEqual(final.messages.map((m) => [m.message_id, m.status, m.text]), [["msg_1", "completed", "Where is my parcel?"], ["msg_2", "completed", "Your parcel is on its way."]]);
  client.stop();
});
