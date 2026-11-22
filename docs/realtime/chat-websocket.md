# WebSocket chat with the support agent (Real Time Systems, part 2)

Valentina Cruz's Customer Experience team wanted the chat to feel like a conversation: the first-line CX agent's answer arrives **token by token**, and the client can **interrupt it mid-response** and redirect it
(tracking when they meant a return) instead of waiting for the full reply. The agent is the one that already exists: **its logic and tools did not change**, only the channel it talks to the user through.
This is separate from part 1: the chat has its own contract and does not use the RFP notification payloads (a test checks it).

## The contract (CONTEXT-company.md, section 4)

Every frame is a JSON text frame `{"event": <name>, "data": {...}}` with an explicit name. `ChatSession`: `session_id` (`chat_<8 hex>`, also the id the socket is bound to), `agent_id` = `first_line_cx`, `user_id`,
`client_id` (the user's own id unless the session is created for another client), `status` (`active`, `interrupted`, `closed`), `created_at`.

| Direction | Event | `data` |
|---|---|---|
| client → server | `auth` (only when the token is not in the URL) | `{token}` |
| client → server | `user_message` | `{session_id, text}` |
| client → server | `interrupt_requested` | `{session_id, new_input}` (a non-empty `new_input` starts a new turn; empty only aborts) |
| client → server | `ping` | `{}` |
| server → client | `session_snapshot` | `{session_id, agent_id, status, messages: [{message_id, role, text, status, position, token_sequence, …}]}` |
| server → client | `user_message` | `{session_id, message_id, text}` |
| server → client | `generation_started` | `{session_id, message_id}` (added: the assistant message exists before its first token) |
| server → client | `token_chunk` | `{session_id, message_id, token, sequence}` (`message_id` added: which message the token belongs to) |
| server → client | `generation_completed` | `{session_id, message_id, text, outcome}` (`text` = the final text) |
| server → client | `generation_interrupted` | `{session_id, message_id, status: "interrupted", text}` (`text` = what had been generated) |
| server → client | `generation_failed` / `error` / `pong` | `{…, error, reference}` / `{code, message}` / `{}` |

## Where each piece lives

| Piece | Location |
|---|---|
| Contract: names, validation, close codes | `services/chat/protocol.py` |
| Pub/sub: one channel per session, `chat.<session_id>` | `services/chat/pubsub.py` |
| The producer: sessions, turns, generations, interrupt | `services/chat/service.py` |
| Database: `chat_sessions`, `chat_messages` | `services/chat/store.py`, `services/models.py` |
| HTTP + the WebSocket endpoint | `services/routers/chat.py` (same API, same JWT) |
| Streaming and cancelling the agent | `services/agent/streaming.py` (+ `run_agent(on_token, control)`, the `generate` node, `rag.generate_answer`, `model_client.stream_chat`) |
| Chat UI | `uis/backoffice/app/backoffice/knowledge/page.tsx` ("Ask TrackFlow"), `lib/chat-socket.ts`, `lib/chat-state.ts`, `lib/chat.ts` |

## Authentication and binding

`WS /chat/sessions/{session_id}/ws`. The JWT is the same as the backoffice API's and travels as `?token=` (checked **before the handshake is accepted**: a bad token, or somebody else's session, never gets a socket) or, when it
is not in the URL (URLs end up in logs), as the **first frame** `auth`, which must arrive within 5 s; until then nothing is sent and any other first frame closes the socket (`4401`). The socket is bound to ONE existing session of
the token's owner (`4404` otherwise: somebody else's conversation looks like a missing one; `4409` if closed). The browser `Origin` must be one of the API's allowed origins (a handshake is not covered by CORS). A socket closes by itself
after `CHAT_WS_MAX_SECONDS` (1800) so it never outlives its token for long; the client reconnects and presents the token again. The UI uses the first-frame form.

## Token streaming (LangGraph `custom` stream mode)

`run_agent(..., on_token=..., control=...)` runs the graph with `graph.stream(stream_mode=["custom", "values"])`. The `generate` node asks the model for the **same answer** with `stream: true` (`model_client.stream_chat`, the
OpenAI-compatible `/chat/completions` of the same service) and writes every piece with `get_stream_writer()`; the consumer receives them as they are produced, and the final state is what `invoke` would have returned (the
output guard still checks it). Without `on_token`/`control` nothing changes: the normal call is used, which is how the HTTP endpoint and every earlier test still run. An answer that does not come from the model (a guardrail's
refusal, a fixed message) reaches the user the same way, as word-sized `token_chunk`s. The chat does not use the agent's optional conversation memory (`conversation_id`): that path asks the model for a JSON with a memory proposal, which
is not text to stream.

## Interrupt: a real abort

`interrupt_requested` → `ChatService.interrupt` → `Control.cancel()`, which (1) sets the flag that the next token, the next node boundary and the start of generation all check, so **nothing further is produced or requested**, and
(2) **closes the connection to the model service from the calling thread**, so a generation waiting for its next token stops too and the model stops generating (tested: a model that goes silent is woken at once). The token check and
the publish happen under one lock, and `generation_interrupted` is published by the worker after it has stopped, so **no `token_chunk` of the old message ever follows it**. LangGraph's `interrupt()` is not used: it pauses a graph for a
human decision, it does not stop a stream.

After the abort: the partial assistant message is **kept** with the text already shown, marked `interrupted` (never overwritten); the session is `interrupted` until the next turn; a non-empty `new_input` becomes a new user message and a **new assistant
message** (`generation_started` with a new id). The agent answers one question at a time by design, so the new turn is a new question for it: `new_input`. Two quick interrupts never run two generations at once, and the last redirect wins.
One generation per session: a `user_message` while the agent is answering is refused (`generation_in_progress`); the way to say something new is the interrupt, which is what the UI does when you type while it writes.

## Pub/sub: producer and consumers are decoupled

The generation (producer, a worker thread) only calls `bus.publish(session_id, event, data)` on the channel `chat.<session_id>`; each WebSocket connection subscribes to that channel and forwards what it gets. Two tabs of a chat, or a
reconnection, are just more subscribers, and a generation that outlives its connection keeps publishing and persisting. `publish` is thread-safe (`call_soon_threadsafe` into the subscriber's loop); a subscriber that falls 1,000 events
behind is dropped (it reconnects and rehydrates). In memory, one process (accepted by the brief): a Redis backplane would replace `ChatBus` and nothing else.

## Reconnection restores the thread

The client reconnects with progressive backoff (1 s, 2 s, 4 s … 30 s, jitter, reset by a connection that held; the browser's `online` event skips the wait; a silent connection is replaced through client pings), to the **same `session_id`**.
On every connection the server sends `session_snapshot` **before anything else**: the stored message history, and for a message still being written the **text so far and the `token_sequence` it reached**. The client replaces its state with
it and then applies only tokens newer than that sequence, so nothing is lost or repeated (the subscription is made before the snapshot is taken, so no token falls between them). The UI treats the connection as ready only once the snapshot arrived,
and re-attaches to the same conversation after a page reload (`sessionStorage`). A message left `streaming` by a process that stopped is marked `interrupted` at startup and keeps its text.

## Tests and checks

```powershell
python -m pytest tests/pipelines/test_agent_streaming.py   # the agent: streaming, cancel, model stream closed, guardrails still apply
python -m pytest services/tests/test_chat_ws.py            # the socket: contract, auth, binding, interrupt, reconnect, pub/sub, with the REAL agent graph too
node --test uis/backoffice/tests/chat.test.cjs              # the client: state reducer, backoff, reconnection, snapshot rehydration
python scripts/chat_demo_server.py                          # the API with a stand-in model, to try the UI without credentials
```

Checked by hand in the browser (the real UI, the real socket; only the model's token stream is the stand-in of `chat_demo_server.py`): the answer grew token by token; typing a new message while it wrote interrupted it (the partial message stayed, marked
"Interrupted: you changed the subject", and a new message answered the new input); the API was stopped mid-answer and the page showed `Reconnecting in 1 s (attempt 1)`, 2 s, 4 s, 7 s, 13 s, then `Connected` with the whole conversation
restored (the cut-off answer kept as interrupted) and the chat went on.

## Limits, honestly

- **No live model was used.** The tests use a stand-in for the model's token stream (and the demo server too); the real agent graph, guardrails, socket and database are real. Streaming a real provider's `stream: true` response is written against the
  OpenAI-compatible format but was not run against a live one.
- **Streaming and the output guard.** The output guard checks the complete answer, so a leaking answer would have been visible token by token before the guard replaces it; the final `generation_completed.text` and the stored message are the guarded
  text and the UI replaces what it streamed. Holding the stream back until it is checked would remove the streaming. (An injection or a sensitive request is stopped before the model, so this affects only a model that misbehaves on its own.)
- **A generation continues if the user just closes the tab** (until it ends: the answer is stored for when they come back). An explicit stop, an interrupt or closing the conversation aborts it.
- **One API process.** The bus and the generations are in memory; several processes would need a shared broker and shared generation state. Interrupting during retrieval or a tool call takes effect at the next node boundary.
- **A cut-off generation after a server restart is not resumed**: the partial text is kept as `interrupted` and the user asks again.
- **The agent has no memory of earlier turns of the conversation** (by design of the existing agent): the thread the user sees is the stored history; each turn answers the user's latest text. The LangGraph checkpoint of a turn is keyed by its `run_id`
  (stored on the assistant message, so its trace can be read), not by `session_id`.
- The old single-request `Ask TrackFlow` call (`/knowledge/query`) still exists in the API and `lib/knowledge.ts`; the page no longer uses it.
