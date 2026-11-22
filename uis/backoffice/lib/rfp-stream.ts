/**
 * The real-time stream of RFP notifications (Real Time Systems, part 1): `GET /rfp/events` read with `fetch` + `ReadableStream`, not with `EventSource`.
 *
 * Why `fetch`: the endpoint is protected by the same JWT as the rest of the backoffice API (`Authorization: Bearer ...`) and a browser's `EventSource` cannot set that header.
 * This module holds no React and no DOM: the parser, the backoff, the reconnecting client and the dedupe are plain functions and classes, so they are tested in Node.
 *
 * Reconnection: progressive backoff (1 s, 2 s, 4 s ... up to 30 s, with a little jitter) after a failure; a clean close by the server (it ends a stream after its maximum lifetime) reconnects after a short fixed wait; a silent connection
 * (no bytes, not even the server's keep-alive comments, for `staleAfterMs`) is treated as dropped; a 401 stops for good (the session is gone, retrying with the same token cannot help).
 *
 * Recovery of what happened while disconnected (documented choice): on EVERY connection the client (1) sends `Last-Event-ID` so the server replays what it still holds, and (2) calls `onConnected`, which the dashboard uses
 * to refetch the ticket list once. Events are then deduplicated by `ticket_id`, so the replay and the list overlapping never show a ticket twice.
 */

export const EVENT_TICKET_CREATED = "rfp_ticket_created";

/** The `data:` of `rfp_ticket_created`: ticket fields only, with the names of the RFP system. */
export interface RfpTicketCreated {
  ticket_id: string;
  rfp_id: string;
  client_name: string | null;
  client_country: string | null;
  services_requested: string[];
  departments_needed: string[];
  status: string;
  created_at: string | null;
}

export interface SseEvent {
  event: string;
  data: string;
  id: string | null;
}

/** Incremental parser of the SSE wire format. Feed it decoded text in any chunking; it returns the events completed so far. Comments (`: keep-alive`) are ignored. */
export class SseParser {
  private partial = "";
  private heldCarriageReturn = false;
  private started = false;
  private event = "";
  private data: string[] = [];
  private id: string | null = null;
  /** The last `id:` seen (kept across events, as the spec says), to send as `Last-Event-ID`. */
  lastEventId: string | null = null;
  /** The last `retry:` the server suggested, in milliseconds. */
  retry: number | null = null;

  push(chunk: string): SseEvent[] {
    let text = chunk;
    if (!this.started) {
      this.started = true;
      if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    }
    text = this.partial + (this.heldCarriageReturn ? "\r" : "") + text;
    this.heldCarriageReturn = false;
    this.partial = "";
    if (text.endsWith("\r")) {
      // a lone CR at the end of a chunk may be the first half of CRLF: wait for the next chunk to know
      text = text.slice(0, -1);
      this.heldCarriageReturn = true;
    }
    const lines = text.split(/\r\n|\r|\n/);
    this.partial = lines.pop() ?? "";
    const events: SseEvent[] = [];
    for (const line of lines) {
      if (line === "") {
        const dispatched = this.dispatch();
        if (dispatched) events.push(dispatched);
        continue;
      }
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") this.event = value;
      else if (field === "data") this.data.push(value);
      else if (field === "id" && !value.includes("\0")) this.id = value;
      else if (field === "retry" && /^\d+$/.test(value)) this.retry = Number(value);
    }
    return events;
  }

  private dispatch(): SseEvent | null {
    const data = this.data;
    const event = this.event || "message";
    const id = this.id;
    this.event = "";
    this.data = [];
    this.id = null;
    if (id !== null) this.lastEventId = id;
    return data.length === 0 ? null : { event, data: data.join("\n"), id };
  }
}

/** The ticket of a `rfp_ticket_created` body, or null when the body is not what the contract says (a malformed event is ignored, never shown). */
export function parseTicketCreated(raw: string): RfpTicketCreated | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  if (typeof record.ticket_id !== "string" || !record.ticket_id || typeof record.status !== "string") return null;
  const strings = (item: unknown): string[] => (Array.isArray(item) ? item.filter((entry): entry is string => typeof entry === "string") : []);
  return {
    ticket_id: record.ticket_id,
    rfp_id: typeof record.rfp_id === "string" ? record.rfp_id : "",
    client_name: typeof record.client_name === "string" ? record.client_name : null,
    client_country: typeof record.client_country === "string" ? record.client_country : null,
    services_requested: strings(record.services_requested),
    departments_needed: strings(record.departments_needed),
    status: record.status,
    created_at: typeof record.created_at === "string" ? record.created_at : null,
  };
}

export interface BackoffOptions {
  baseMs: number;
  maxMs: number;
  /** 0.2 means the delay varies by up to +-20%, so many clients do not reconnect in the same instant. */
  jitter: number;
}

export const DEFAULT_BACKOFF: BackoffOptions = { baseMs: 1000, maxMs: 30_000, jitter: 0.2 };

/** Delay before reconnection attempt number `failures` (0 = the first retry): base * 2^failures, capped, with jitter. */
export function backoffDelay(failures: number, options: Partial<BackoffOptions> = {}, random: () => number = Math.random): number {
  const { baseMs, maxMs, jitter } = { ...DEFAULT_BACKOFF, ...options };
  const raw = Math.min(maxMs, baseMs * 2 ** Math.max(0, failures));
  return Math.round(raw * (1 - jitter + random() * 2 * jitter));
}

export type StreamState = "connecting" | "connected" | "reconnecting" | "offline" | "unauthorized" | "stopped";

export interface StreamStatus {
  state: StreamState;
  /** Consecutive failed attempts so far (0 when connected). */
  attempt: number;
  /** How long the client will wait before the next attempt, when reconnecting. */
  nextRetryMs: number | null;
}

export interface StreamClientOptions {
  url: string;
  getToken: () => string | null;
  onEvent: (ticket: RfpTicketCreated, meta: { id: string | null }) => void;
  onStatus?: (status: StreamStatus) => void;
  /** Called on EVERY successful connection, first included: the dashboard refetches the ticket list here (recovery of what was missed). Not awaited: events keep flowing meanwhile. */
  onConnected?: (info: { reconnect: boolean }) => void | Promise<void>;
  fetchImpl?: typeof fetch;
  backoff?: Partial<BackoffOptions>;
  random?: () => number;
  /** Wait between attempts. Replaceable in tests; it must resolve early when the signal aborts. */
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  /** No bytes (not even a keep-alive comment) for this long: the connection is considered dead and replaced. The server sends a comment every 15 s. */
  staleAfterMs?: number;
  /** A connection that lasts less than this is not a success: the failure counter keeps growing (a flapping proxy must not reset the backoff). */
  stableAfterMs?: number;
  /** Wait after the server closes the stream cleanly (its maximum lifetime), before reconnecting with the token again. */
  cleanCloseDelayMs?: number;
  now?: () => number;
  isOnline?: () => boolean;
}

function defaultSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const timer = setTimeout(done, ms);
    function done() {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done);
  });
}

class Unauthorized extends Error {}

export class RfpStreamClient {
  private stopped = false;
  private attempt = 0;
  private connectedOnce = false;
  private running: Promise<void> | null = null;
  private connection: AbortController | null = null;
  private waiting: AbortController | null = null;
  private parser = new SseParser();
  private status: StreamStatus = { state: "stopped", attempt: 0, nextRetryMs: null };

  constructor(private readonly options: StreamClientOptions) {}

  get lastEventId(): string | null {
    return this.parser.lastEventId;
  }

  get current(): StreamStatus {
    return this.status;
  }

  start(): Promise<void> {
    if (!this.running) {
      this.stopped = false;
      this.running = this.loop().finally(() => {
        this.running = null;
      });
    }
    return this.running;
  }

  stop(): void {
    this.stopped = true;
    this.connection?.abort();
    this.waiting?.abort();
    this.setStatus({ state: "stopped", attempt: this.attempt, nextRetryMs: null });
  }

  /** The browser says the network is back: do not wait out the backoff. */
  nudge(): void {
    this.waiting?.abort();
  }

  private setStatus(status: StreamStatus): void {
    this.status = status;
    this.options.onStatus?.(status);
  }

  private async loop(): Promise<void> {
    const sleep = this.options.sleep ?? defaultSleep;
    const now = this.options.now ?? Date.now;
    while (!this.stopped) {
      this.setStatus({ state: this.connectedOnce || this.attempt > 0 ? "reconnecting" : "connecting", attempt: this.attempt, nextRetryMs: null });
      let cleanClose = false;
      const startedAt = now();
      try {
        await this.connectAndRead();
        cleanClose = true;
      } catch (error) {
        if (this.stopped) return;
        if (error instanceof Unauthorized) {
          this.setStatus({ state: "unauthorized", attempt: this.attempt, nextRetryMs: null });
          return;
        }
      }
      if (this.stopped) return;
      const lasted = now() - startedAt;
      const stable = lasted >= (this.options.stableAfterMs ?? 2000);
      this.attempt = stable ? 0 : this.attempt + 1; // a connection that held for a while was a success; short-lived ones keep the backoff growing
      const delay = stable && cleanClose ? (this.options.cleanCloseDelayMs ?? 1000) : backoffDelay(Math.max(0, this.attempt - 1), this.options.backoff, this.options.random);
      const online = this.options.isOnline ? this.options.isOnline() : true;
      this.setStatus({ state: online ? "reconnecting" : "offline", attempt: this.attempt, nextRetryMs: delay });
      this.waiting = new AbortController();
      await sleep(delay, this.waiting.signal);
      this.waiting = null;
    }
  }

  private async connectAndRead(): Promise<void> {
    const token = this.options.getToken();
    if (!token) throw new Unauthorized();
    const controller = new AbortController();
    this.connection = controller;
    // Only headers the API's CORS policy allows (Authorization, Accept, Last-Event-ID): any other one makes the browser's preflight fail and the stream never opens. Caching is turned off with `cache: "no-store"`.
    const headers: Record<string, string> = { Authorization: `Bearer ${token}`, Accept: "text/event-stream" };
    if (this.parser.lastEventId) headers["Last-Event-ID"] = this.parser.lastEventId;
    const response = await (this.options.fetchImpl ?? fetch)(this.options.url, { headers, signal: controller.signal, cache: "no-store" });
    if (response.status === 401 || response.status === 403) throw new Unauthorized();
    if (!response.ok || !response.body) throw new Error(`The stream answered ${response.status}.`);

    const reconnect = this.connectedOnce;
    this.connectedOnce = true;
    this.setStatus({ state: "connected", attempt: 0, nextRetryMs: null });
    if (this.options.onConnected) {
      try {
        void Promise.resolve(this.options.onConnected({ reconnect })).catch(() => undefined);
      } catch {
        // recovery is best effort: the stream itself must go on
      }
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    const arm = () => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => controller.abort(), this.options.staleAfterMs ?? 45_000);
    };
    arm();
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) return;
        arm(); // any bytes, including the server's keep-alive comments, prove the connection is alive
        for (const event of this.parser.push(decoder.decode(value, { stream: true }))) {
          if (event.event !== EVENT_TICKET_CREATED) continue; // other event types are not this feed's business
          const ticket = parseTicketCreated(event.data);
          if (ticket) this.options.onEvent(ticket, { id: event.id });
        }
      }
    } finally {
      clearTimeout(watchdog);
      controller.abort();
      this.connection = null;
    }
  }
}

// ------------------------------------------------------------------------------------------------ the feed's state

export interface FeedTicket extends RfpTicketCreated {
  /** True for the few seconds after it arrived: the dashboard highlights it. */
  fresh: boolean;
  /** How it got here: the live stream, or the list refetched after a (re)connection. */
  source: "stream" | "recovered";
}

export const MAX_FEED = 20;

/** Newest first, one entry per ticket: a ticket received twice (replay + refetch, or a repeated event) keeps its first entry and takes the newest data. */
export function mergeTickets(current: FeedTicket[], incoming: FeedTicket[], limit = MAX_FEED): FeedTicket[] {
  const byId = new Map(current.map((ticket) => [ticket.ticket_id, ticket]));
  for (const ticket of incoming) {
    const known = byId.get(ticket.ticket_id);
    byId.set(ticket.ticket_id, known ? { ...ticket, fresh: known.fresh, source: known.source } : ticket);
  }
  // by the moment, not by the text: the stream writes `...Z` and the list `...+00:00`
  const moment = (ticket: FeedTicket) => (ticket.created_at ? Date.parse(ticket.created_at) || 0 : 0);
  return [...byId.values()].sort((a, b) => moment(b) - moment(a)).slice(0, limit);
}

/** The shape of a row of `GET /rfp/tickets` that the feed needs (kept loose so this module does not import the page's types). */
export interface TicketListRow {
  ticket_id: string;
  rfp_id: string;
  status: string;
  client_name: string | null;
  client_country: string | null;
  departments_needed: string[];
  document_type: string | null;
  created_at: string | null;
}

/** A valid RFP is one the classifier accepted (the same moment the notification fires): rejected and still-unclassified documents are not RFP tickets for the feed. */
export function feedTicketsFromList(rows: TicketListRow[]): FeedTicket[] {
  return rows
    .filter((row) => row.document_type === "formal_rfp" || row.document_type === "informal_request")
    .map((row) => ({
      ticket_id: row.ticket_id,
      rfp_id: row.rfp_id,
      client_name: row.client_name,
      client_country: row.client_country,
      services_requested: [],
      departments_needed: row.departments_needed,
      status: row.status,
      created_at: row.created_at,
      fresh: false,
      source: "recovered" as const,
    }));
}

/** The words for the connection pill. */
export function statusText(status: StreamStatus): string {
  switch (status.state) {
    case "connected":
      return "Live";
    case "connecting":
      return "Connecting…";
    case "reconnecting":
      return status.nextRetryMs ? `Reconnecting in ${Math.max(1, Math.round(status.nextRetryMs / 1000))} s (attempt ${status.attempt})` : "Reconnecting…";
    case "offline":
      return status.nextRetryMs ? `Offline. Trying again in ${Math.max(1, Math.round(status.nextRetryMs / 1000))} s` : "Offline";
    case "unauthorized":
      return "Session expired: sign in again";
    default:
      return "Stopped";
  }
}
