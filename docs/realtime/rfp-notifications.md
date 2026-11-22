# Real-time RFP notifications (Real Time Systems, part 1)

Miguel Torres's team used to find out about a new RFP only by checking the dashboard. Now the dashboard shows it by itself the moment the ticket is registered, and keeps working when the
connection drops. This part is a **communication layer**: no model and no agent is called anywhere in it (a test checks the imports and runs the intake with the model client forbidden).

## What is notified, and when

CONTEXT-company.md: the notification fires **the exact moment a new ticket enters the system with `status = analyzing`, meaning the document was classified as a valid RFP and the flow starts processing it.**
In the intake that is right after the classifier accepts the document and its metadata is read (`runner.py`, after the `analyze` node): the verdict and metadata are stored first (`store.record_valid_rfp`,
the ticket stays `analyzing`), then the event is published, so what the notification says is what the API shows. A rejected document (the carrier pitch) never notifies anybody; a ticket is registered at upload but it is
not an RFP ticket until it is classified.

```
event: rfp_ticket_created
id: 3f9a1c2e-7
data: {"ticket_id":"…","rfp_id":"TF-RFP-2026-0004","client_name":"Luna Cosmetics","client_country":"US","services_requested":["warehousing","last_mile"],"departments_needed":["warehouse","lastmile"],"status":"analyzing","created_at":"2026-07-24T14:32:00Z"}
```

A **named** event (never the generic `message`), with the ticket fields as the JSON `data:` (no `{"event","data"}` envelope). The field names are the ones the RFP system already uses (`ticket_id`, `rfp_id`, `client_name`,
`client_country`, `services_requested`, `status`, `created_at`); `departments_needed` is added because it is what tells the Commercial team which owners are involved. `status` is `analyzing`, the entry status of
the CONTEXT, and the dashboard says "Analyzing · needs processing". **One difference from the example of the CONTEXT:** its `services_requested` shows `["warehouse","lastmile"]`, which are the department ids; our system stores
`services_requested` as `warehousing / last_mile / reverse_logistics` since Part 1 of the RFP workflow and `departments_needed` as the ids. The event carries both, with the stored values, so one name never means two things.

## Backend

| Piece | Location |
|---|---|
| The notification (name, payload, publisher hook) | `data/pipelines/rfp_intake/notifications.py`; raised by `runner.py` |
| The event bus: fan-out to every stream, bounded replay buffer, ids | `services/events.py` |
| The SSE endpoint `GET /rfp/events` | `services/routers/rfp_events.py` |

- **Headers**: `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`, `Connection: keep-alive`, `X-Accel-Buffering: no` (a reverse proxy must not buffer it).
- **Keep-alive**: a comment frame `: keep-alive` every `RFP_SSE_KEEPALIVE_SECONDS` (default 15), after an opening `retry: 3000` and `: connected`.
- **Same JWT as the backoffice**: `Authorization: Bearer …` through the same `get_current_user`; no token or a bad one is a `401` with no event stream, and the token in the URL is not accepted. The stream ends by itself after
  `RFP_SSE_MAX_SECONDS` (900) so it never outlives a session for long: the client reconnects and the token is checked again.
- **CORS**: the preflight allows `Authorization`, `Accept` and `Last-Event-ID` (the browser would refuse the stream otherwise; found and fixed while checking the dashboard in a real browser).
- **Thread safety**: the intake publishes from a worker thread; the bus hands each event to the stream's event loop with `call_soon_threadsafe`. A client that falls 1,000 events behind is dropped (it reconnects and recovers).
- **Replay**: every event has an id `<boot>-<n>` and stays in a ring buffer (`RFP_EVENT_BUFFER`, default 500). A client that reconnects with `Last-Event-ID` receives what it missed; an id from another run of the server
  (other boot token) replays nothing, because the buffer is in memory.

## Frontend (`uis/backoffice`)

| Piece | Location |
|---|---|
| Parser, backoff, reconnecting client, dedupe (no React, tested in Node) | `lib/rfp-stream.ts` |
| The hook | `app/components/useRfpTicketFeed.ts` |
| The panel "New RFP tickets" on the Operations desk | `app/components/RfpLiveFeed.tsx`, used by `app/dashboard.tsx` |
| The ticket list of "RFP intake" also updates live | `app/backoffice/rfp/page.tsx` |

- **`fetch` + `ReadableStream`**, with the JWT in `Authorization` (and `Accept: text/event-stream`): not `EventSource`, which cannot set the header.
- **Distinct from the rest of the dashboard**: its own panel with a connection pill, and a card per ticket (RFP reference, client and country, the scope, the status "Analyzing · needs processing", a "NEW" badge, a
  highlight that fades after 12 s, an "Open" link to the ticket). An event adds a card: **no reload and no refetch per event**.
- **Reconnection with progressive backoff**: after a failure the wait is 1 s, 2 s, 4 s, 8 s … up to 30 s, with ±20% jitter; the pill says "Reconnecting in N s (attempt k)". A connection that held for 2 s or more resets the
  counter (a flapping proxy does not). A silent connection (no bytes, not even keep-alive comments, for 45 s) is replaced. The browser's `online` event skips the wait. A clean close by the server reconnects after 1 s.
  A `401` stops for good (the session is gone) and the pill says so.
- **Recovery (documented choice)**: on **every** connection the client (1) sends `Last-Event-ID` so the server replays what it still holds and (2) **refetches the ticket list once** (`GET /rfp/tickets`), which is the
  recovery that also survives a server restart (the replay buffer does not). From then on SSE carries only what is new. Tickets that were not on screen before a *re*connection are highlighted "MISSED WHILE OFFLINE".
- **No duplicates**: everything is merged by `ticket_id` (`mergeTickets`), so the replay, the refetched list and a repeated event can overlap and a ticket still appears once; ordering is by the moment, not by the text.

## Verification

```powershell
python -m pytest services/tests/test_rfp_events_api.py     # the wire: headers, `event:`, JSON `data:`, keep-alive, JWT, CORS, replay, the bus (starts the real app on a local port)
node --test uis/backoffice/tests/rfp-stream.test.cjs        # parser, backoff, reconnection, recovery, dedupe, watchdog, 401
```

The wire tests start the application on a local port and read the stream with a streaming HTTP client, so they assert what a browser's `fetch` receives, and the event is raised by the **real intake** (a PDF is uploaded). The
client tests drive the real `RfpStreamClient` with a scripted `fetch` that returns controllable `ReadableStream`s (drop, close, silence, 401, network errors) and an injected clock and sleep, so the backoff sequence is asserted exactly.

Checked by hand in the browser (Operations desk open, nothing touched): a PDF registered from outside appeared as a card by itself; the API was stopped and the pill showed `Reconnecting in 1 s (attempt 1)`, `2 s`, `4 s`, `7 s`
… while a ticket was registered with the API down; after the API came back the pill returned to `Live`, the missed ticket appeared as "MISSED WHILE OFFLINE", the earlier one was not duplicated, and a ticket registered afterwards arrived live again.
A rejected document produced no card.

## Limits, honestly

- **One API process.** The bus and its replay buffer live in memory. Several API processes would need a shared broker (Redis pub/sub or Postgres `LISTEN/NOTIFY`) so an event raised in one reaches the streams of the others.
- **Only `rfp_ticket_created` is streamed.** The statuses on a card are those at arrival (or at the last refetch); opening the ticket shows the current one. The optional events of the CONTEXT (`sla_threshold_alert`,
  `ticket_escalated_to_senior`) are not implemented.
- **After a server restart the replay is empty** by design; recovery is the list refetch, which has no "since" filter (it returns the 50 most recent tickets, of which the valid RFPs are shown).
- **Waiting after a server comes back** can be up to 30 s (the cap of the backoff) unless the browser reports the network as back.
- A re-run of the intake for the same ticket (`scripts/rfp_intake.py reprocess`) raises the event again; the dashboard shows the ticket once.
- Notifications are not authorised per user: any signed-in account of the backoffice sees them, as with the ticket list.
