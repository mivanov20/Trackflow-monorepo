/**
 * The WebSocket client of the chat (Real Time Systems, part 2): a persistent, bidirectional connection bound to ONE chat session, reconnected with progressive backoff.
 *
 * - Authentication: the same JWT as the rest of the backoffice, sent as the FIRST frame (`{"event":"auth","data":{"token"}}`), so it does not end up in a URL or an access log. The server also
 *   accepts `?token=`; `authMode: "query"` uses that.
 * - Binding and recovery: the URL carries the `session_id`. On EVERY (re)connection the server answers with a `session_snapshot` of the conversation, which the chat state applies BEFORE any further
 *   token: reconnecting restores the thread (history and the text so far of an answer being written); it does not just reopen a socket.
 * - Backoff: 1 s, 2 s, 4 s ... up to 30 s with jitter (the same scheme as the notification stream), reset by a connection that held for a while. A silent connection is replaced: the client pings,
 *   and if nothing at all (not even a `pong`) arrives for `staleAfterMs` it closes and reconnects. The browser's `online` event skips the wait (`nudge`).
 * - Close codes: 4401 (the token was refused) and 4404 / 4409 (no such conversation, or it is closed) stop for good: retrying cannot help. Anything else is a drop and is retried.
 *
 * No React and no DOM: the `WebSocket` implementation, the timers and the clock are injectable, so it is tested in Node.
 */

import { backoffDelay, type BackoffOptions } from "./rfp-stream";
import type { ServerEvent } from "./chat-state";

export type SocketState = "connecting" | "connected" | "reconnecting" | "offline" | "unauthorized" | "unavailable" | "stopped";

export interface SocketStatus {
  state: SocketState;
  /** Consecutive failed attempts so far (0 when connected). */
  attempt: number;
  nextRetryMs: number | null;
}

export interface SocketLike {
  readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  onerror: ((event: unknown) => void) | null;
}

export interface ChatSocketOptions {
  url: string;
  getToken: () => string | null;
  onEvent: (event: ServerEvent) => void;
  onStatus?: (status: SocketStatus) => void;
  /** Called on every connection once the snapshot arrived (the thread is restored). */
  onRestored?: (info: { reconnect: boolean }) => void;
  authMode?: "first_frame" | "query";
  createSocket?: (url: string) => SocketLike;
  backoff?: Partial<BackoffOptions>;
  random?: () => number;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  now?: () => number;
  isOnline?: () => boolean;
  pingIntervalMs?: number;
  staleAfterMs?: number;
  stableAfterMs?: number;
}

export const OPEN = 1;
export const CLOSE_UNAUTHORIZED = 4401;
export const CLOSE_NOT_FOUND = 4404;
export const CLOSE_CLOSED = 4409;

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

type Outcome = { kind: "dropped"; code: number } | { kind: "refused"; code: number } | { kind: "failed" };

export class ChatSocketClient {
  private stopped = false;
  private attempt = 0;
  private everRestored = false;
  private running: Promise<void> | null = null;
  private socket: SocketLike | null = null;
  private waiting: AbortController | null = null;
  private status: SocketStatus = { state: "stopped", attempt: 0, nextRetryMs: null };

  constructor(private readonly options: ChatSocketOptions) {}

  get current(): SocketStatus {
    return this.status;
  }

  /** True when a frame can be sent right now. */
  get open(): boolean {
    return this.socket !== null && this.socket.readyState === OPEN && this.status.state === "connected";
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
    this.waiting?.abort();
    try {
      this.socket?.close(1000, "client closed");
    } catch {
      // already closed
    }
    this.setStatus({ state: "stopped", attempt: this.attempt, nextRetryMs: null });
  }

  nudge(): void {
    this.waiting?.abort();
  }

  /** Sends one event on the open connection. Returns false when there is no connection (the caller says so: the message was NOT sent). */
  send(event: string, data: Record<string, unknown> = {}): boolean {
    if (!this.open) return false;
    this.socket!.send(JSON.stringify({ event, data }));
    return true;
  }

  private setStatus(status: SocketStatus): void {
    this.status = status;
    this.options.onStatus?.(status);
  }

  private async loop(): Promise<void> {
    const sleep = this.options.sleep ?? defaultSleep;
    const now = this.options.now ?? Date.now;
    while (!this.stopped) {
      this.setStatus({ state: this.everRestored || this.attempt > 0 ? "reconnecting" : "connecting", attempt: this.attempt, nextRetryMs: null });
      const startedAt = now();
      const outcome = await this.connectOnce();
      this.socket = null;
      if (this.stopped) return;
      if (outcome.kind === "refused") {
        const state = outcome.code === CLOSE_UNAUTHORIZED ? "unauthorized" : "unavailable";
        this.setStatus({ state, attempt: this.attempt, nextRetryMs: null });
        return;
      }
      const stable = now() - startedAt >= (this.options.stableAfterMs ?? 3000);
      this.attempt = stable ? 0 : this.attempt + 1; // a connection that held was a success; short-lived ones keep the backoff growing
      const delay = backoffDelay(Math.max(0, this.attempt - 1), this.options.backoff, this.options.random);
      const online = this.options.isOnline ? this.options.isOnline() : true;
      this.setStatus({ state: online ? "reconnecting" : "offline", attempt: this.attempt, nextRetryMs: delay });
      this.waiting = new AbortController();
      await sleep(delay, this.waiting.signal);
      this.waiting = null;
    }
  }

  private connectOnce(): Promise<Outcome> {
    const token = this.options.getToken();
    if (!token) return Promise.resolve({ kind: "refused", code: CLOSE_UNAUTHORIZED });
    const url = this.options.authMode === "query" ? `${this.options.url}${this.options.url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : this.options.url;
    return new Promise<Outcome>((resolve) => {
      let socket: SocketLike;
      try {
        socket = (this.options.createSocket ?? ((target: string) => new WebSocket(target) as unknown as SocketLike))(url);
      } catch {
        resolve({ kind: "failed" });
        return;
      }
      this.socket = socket;
      const reconnect = this.everRestored;
      let restored = false;
      let finished = false;
      let ping: ReturnType<typeof setInterval> | undefined;
      let watchdog: ReturnType<typeof setTimeout> | undefined;
      const stale = this.options.staleAfterMs ?? 45_000;
      const arm = () => {
        clearTimeout(watchdog);
        watchdog = setTimeout(() => {
          try {
            socket.close(4000, "no answer");
          } catch {
            // already closed
          }
        }, stale);
      };
      const finish = (outcome: Outcome) => {
        if (finished) return;
        finished = true;
        clearInterval(ping);
        clearTimeout(watchdog);
        resolve(outcome);
      };
      socket.onopen = () => {
        if ((this.options.authMode ?? "first_frame") === "first_frame") socket.send(JSON.stringify({ event: "auth", data: { token } }));
        arm();
        ping = setInterval(() => {
          if (socket.readyState === OPEN) socket.send(JSON.stringify({ event: "ping", data: {} }));
        }, this.options.pingIntervalMs ?? 20_000);
      };
      socket.onmessage = (message) => {
        arm(); // any frame, a pong included, proves the connection is alive
        let parsed: unknown;
        try {
          parsed = JSON.parse(String(message.data));
        } catch {
          return;
        }
        if (!parsed || typeof parsed !== "object" || typeof (parsed as ServerEvent).event !== "string") return;
        const event = parsed as ServerEvent;
        if (event.event === "pong") return;
        this.options.onEvent(event);
        if (event.event === "session_snapshot" && !restored) {
          restored = true;
          this.everRestored = true;
          this.setStatus({ state: "connected", attempt: 0, nextRetryMs: null }); // connected = authenticated, bound and rehydrated
          this.options.onRestored?.({ reconnect });
        }
      };
      socket.onerror = () => undefined; // the close event follows and carries the outcome
      socket.onclose = (event) => {
        if (event.code === CLOSE_UNAUTHORIZED || event.code === CLOSE_NOT_FOUND || event.code === CLOSE_CLOSED) finish({ kind: "refused", code: event.code });
        else finish(restored ? { kind: "dropped", code: event.code } : { kind: "failed" });
      };
    });
  }
}

/** The `ws(s)://` address of a session's socket from the API's `http(s)://` base. */
export function socketUrl(apiBase: string, sessionId: string): string {
  return `${apiBase.replace(/^http/, "ws").replace(/\/$/, "")}/chat/sessions/${encodeURIComponent(sessionId)}/ws`;
}

export function socketStatusText(status: SocketStatus): string {
  switch (status.state) {
    case "connected":
      return "Connected";
    case "connecting":
      return "Connecting…";
    case "reconnecting":
      return status.nextRetryMs ? `Reconnecting in ${Math.max(1, Math.round(status.nextRetryMs / 1000))} s (attempt ${status.attempt})` : "Reconnecting…";
    case "offline":
      return status.nextRetryMs ? `Offline. Trying again in ${Math.max(1, Math.round(status.nextRetryMs / 1000))} s` : "Offline";
    case "unauthorized":
      return "Session expired: sign in again";
    case "unavailable":
      return "This conversation is no longer available";
    default:
      return "Disconnected";
  }
}
