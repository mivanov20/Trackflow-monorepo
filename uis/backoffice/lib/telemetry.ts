/**
 * TelemetryService: the only way the backoffice records usage events.
 *
 * Everything goes through `track(eventType, properties)`. The service adds the envelope, validates the
 * event against the allowlist generated from docs/telemetry/event-schemas.json, keeps it in a local
 * queue and sends the queue in batches. Components never build envelopes or call `fetch` for telemetry.
 *
 * - Batch + debounce: a batch leaves every 10 seconds or when the queue reaches 20 events.
 * - Reliable flush: when the tab is hidden or closed the queue goes out with `navigator.sendBeacon`.
 * - Retry with backoff: a failed batch is retried up to 3 times (1 s, 2 s, 4 s) and then discarded;
 *   telemetry is never critical and never blocks the app.
 * - The address comes from NEXT_PUBLIC_TELEMETRY_ENDPOINT. Without it telemetry is off.
 */

import { getAccessToken, getCurrentUser } from "./session";
import { EVENT_SCHEMAS, TELEMETRY_SCHEMA_VERSION, type EventSchema, type PropertySchema } from "./telemetry-schema.generated";

export { TELEMETRY_SCHEMA_VERSION };

export const FLUSH_INTERVAL_MS = 10_000;
export const MAX_QUEUE_SIZE = 20;
export const MAX_BATCH_SIZE = 100;
export const MAX_RETRIES = 3;
export const BASE_RETRY_DELAY_MS = 1_000;
/** Streamed events (see `delivery` in the schemas) leave after this short pause so a burst shares one request. */
export const STREAM_FLUSH_DELAY_MS = 1_000;
/** While the network is down the queue is capped; the oldest events are dropped first. */
export const MAX_BUFFERED_EVENTS = 200;
const BEACON_CHUNK_SIZE = 50;
const SESSION_STORAGE_KEY = "trackflow-telemetry-session";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** The standard envelope of docs/telemetry/telemetry-plan.md, section 4.2. */
export interface TelemetryEnvelope {
  eventId: string;
  timestamp: string;
  sessionId: string | null;
  userId: string | null;
  event_type: string;
  schemaVersion: string;
  requestId: string;
  properties: Record<string, unknown>;
}

export interface TelemetryStats {
  queued: number;
  sent: number;
  discarded: number;
  dropped: number;
}

export interface TelemetryDeps {
  endpoint: string;
  schemas?: Record<string, EventSchema>;
  fetchImpl?: typeof fetch;
  sendBeaconImpl?: (url: string, data: Blob) => boolean;
  now?: () => Date;
  uuid?: () => string;
  identity?: () => { sessionId: string | null; userId: string | null };
  retryJitterMs?: number;
}

// --- validation against the generated allowlist --------------------------------------------

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Checks one value against the JSON Schema subset the generator emits (type, enum, pattern, bounds, arrays, objects). */
export function matchesSchema(schema: PropertySchema, value: unknown): boolean {
  const types = Array.isArray(schema.type) ? schema.type : [schema.type];
  if (value === null) return types.includes("null") && (!schema.enum || schema.enum.includes(null));
  if (schema.enum && !schema.enum.includes(value as string)) return false;
  if (typeof value === "string") {
    if (!types.includes("string")) return false;
    if (schema.maxLength !== undefined && value.length > schema.maxLength) return false;
    return schema.pattern === undefined || new RegExp(schema.pattern).test(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return false;
    if (!(types.includes("number") || (types.includes("integer") && Number.isInteger(value)))) return false;
    if (schema.minimum !== undefined && value < schema.minimum) return false;
    if (schema.maximum !== undefined && value > schema.maximum) return false;
    return schema.exclusiveMinimum === undefined || value > schema.exclusiveMinimum;
  }
  if (typeof value === "boolean") return types.includes("boolean");
  if (Array.isArray(value)) {
    if (!types.includes("array")) return false;
    if (schema.minItems !== undefined && value.length < schema.minItems) return false;
    if (schema.uniqueItems && new Set(value).size !== value.length) return false;
    return schema.items === undefined || value.every((item) => matchesSchema(schema.items as PropertySchema, item));
  }
  if (isPlainObject(value)) {
    if (!types.includes("object")) return false;
    const allowed = schema.properties ?? {};
    return Object.entries(value).every(([key, item]) => (key in allowed ? matchesSchema(allowed[key], item) : schema.additionalProperties !== false));
  }
  return false;
}

/**
 * Why a property bag is not valid for its event, or null when it is. The allowlist is strict: a key that is not
 * in the schema makes the whole event invalid, so a developer cannot leak a field by passing a whole object.
 */
export function propertiesProblem(schema: EventSchema, properties: Record<string, unknown>): string | null {
  for (const key of Object.keys(properties)) {
    if (!(key in schema.properties)) return `property "${key}" is not allowed`;
  }
  for (const key of schema.required) {
    if (!(key in properties)) return `property "${key}" is required`;
  }
  for (const [key, value] of Object.entries(properties)) {
    if (!matchesSchema(schema.properties[key], value)) return `property "${key}" has an invalid value`;
  }
  return null;
}

// --- session identity ----------------------------------------------------------------------

interface StoredSession {
  id: string;
  startedAt: number;
}

let memorySession: StoredSession | null = null;

function randomUuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined") crypto.getRandomValues(bytes);
  else for (let i = 0; i < 16; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function readStoredSession(): StoredSession | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as StoredSession) : null;
    return parsed && UUID_PATTERN.test(parsed.id) ? parsed : null;
  } catch {
    return memorySession;
  }
}

function writeStoredSession(session: StoredSession | null): void {
  memorySession = session;
  try {
    if (session) window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session));
    else window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    // Storage is blocked: the in-memory copy keeps the session for this page load.
  }
}

/**
 * The session starts when someone is signed in and ends at sign-out. It lives in sessionStorage, so it
 * survives a reload of the tab but never reaches localStorage or another tab.
 */
function currentIdentity(): { sessionId: string | null; userId: string | null } {
  if (typeof window === "undefined" || !getAccessToken()) {
    if (typeof window !== "undefined" && (memorySession || readStoredSession())) writeStoredSession(null);
    return { sessionId: null, userId: null };
  }
  let session = readStoredSession();
  if (!session) {
    session = { id: randomUuid(), startedAt: Date.now() };
    writeStoredSession(session);
  }
  const user = getCurrentUser();
  return { sessionId: session.id, userId: user && UUID_PATTERN.test(user.id) ? user.id : null };
}

/** Seconds since the current session started, or null when nobody is signed in. */
export function sessionAgeSeconds(): number | null {
  if (typeof window === "undefined" || !getAccessToken()) return null;
  const session = readStoredSession();
  return session ? Math.max(0, Math.round((Date.now() - session.startedAt) / 1000)) : null;
}

// --- the service ---------------------------------------------------------------------------

type SendResult = "ok" | "retry" | "drop";

export function createTelemetryService(deps: TelemetryDeps) {
  const schemas = deps.schemas ?? EVENT_SCHEMAS;
  const now = deps.now ?? (() => new Date());
  const uuid = deps.uuid ?? randomUuid;
  const identity = deps.identity ?? currentIdentity;
  const jitterMs = deps.retryJitterMs ?? 250;

  let queue: TelemetryEnvelope[] = [];
  let inFlight: TelemetryEnvelope[] = [];
  let flushing = false;
  let intervalId: ReturnType<typeof setInterval> | null = null;
  let streamTimer: ReturnType<typeof setTimeout> | null = null;
  let activeRequestId: string | null = null;
  let started = false;
  const listeners = new Set<(eventType: string) => void>();
  const stats: TelemetryStats = { queued: 0, sent: 0, discarded: 0, dropped: 0 };

  const enabled = () => deps.endpoint !== "";
  const doFetch = (): typeof fetch => deps.fetchImpl ?? fetch;

  function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function post(batch: TelemetryEnvelope[]): Promise<SendResult> {
    try {
      const response = await doFetch()(deps.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events: batch }),
      });
      if (response.ok) return "ok";
      // A 4xx (except timeouts and rate limits) would fail again: do not retry it.
      return response.status >= 400 && response.status < 500 && response.status !== 408 && response.status !== 429 ? "drop" : "retry";
    } catch {
      return "retry";
    }
  }

  async function sendWithRetry(batch: TelemetryEnvelope[]): Promise<boolean> {
    for (let attempt = 0; attempt <= MAX_RETRIES; attempt += 1) {
      if (attempt > 0) await sleep(BASE_RETRY_DELAY_MS * 2 ** (attempt - 1) + Math.random() * jitterMs);
      const result = await post(batch);
      if (result === "ok") return true;
      if (result === "drop") return false;
    }
    return false;
  }

  /** Sends everything queued, one batch at a time, in capture order. Safe to call at any moment. */
  async function flush(): Promise<void> {
    if (flushing || !enabled()) return;
    flushing = true;
    try {
      while (queue.length > 0) {
        const batch = queue.splice(0, MAX_BATCH_SIZE);
        inFlight = batch;
        const delivered = await sendWithRetry(batch);
        inFlight = [];
        if (delivered) stats.sent += batch.length;
        else stats.discarded += batch.length;
      }
    } finally {
      flushing = false;
    }
  }

  /**
   * Hands the pending events to the browser with `sendBeacon`, which survives the page being destroyed.
   * An event that was mid-retry is included again: the collector de-duplicates by `eventId`.
   */
  function flushOnHide(): void {
    if (!enabled()) return;
    const pending = [...inFlight, ...queue];
    if (pending.length === 0) return;
    queue = [];
    const beacon = deps.sendBeaconImpl ?? (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function" ? (url: string, data: Blob) => navigator.sendBeacon(url, data) : null);
    for (let index = 0; index < pending.length; index += BEACON_CHUNK_SIZE) {
      const chunk = pending.slice(index, index + BEACON_CHUNK_SIZE);
      const body = new Blob([JSON.stringify({ events: chunk })], { type: "application/json" });
      if (beacon && beacon(deps.endpoint, body)) {
        stats.sent += chunk.length;
        continue;
      }
      // No beacon or the browser refused it: a keepalive request is the best remaining option.
      void doFetch()(deps.endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: body, keepalive: true }).catch(() => {
        stats.discarded += chunk.length;
      });
      stats.sent += chunk.length;
    }
  }

  function ensureTimer(): void {
    if (intervalId === null) {
      intervalId = setInterval(() => void flush(), FLUSH_INTERVAL_MS);
      // Node (tests) would otherwise stay alive for this timer; browsers return a number and ignore this.
      (intervalId as unknown as { unref?: () => void }).unref?.();
    }
  }

  function scheduleStreamFlush(): void {
    if (streamTimer !== null) return;
    streamTimer = setTimeout(() => {
      streamTimer = null;
      void flush();
    }, STREAM_FLUSH_DELAY_MS);
  }

  /** Records an event. Never throws and never blocks: an invalid event is dropped and counted. */
  function track(eventType: string, properties: Record<string, unknown>): void {
    try {
      if (!enabled() || typeof window === "undefined") return;
      const schema = schemas[eventType];
      if (!schema || propertiesProblem(schema, properties) !== null) {
        stats.dropped += 1;
        return;
      }
      const { sessionId, userId } = identity();
      queue.push({
        eventId: uuid(),
        timestamp: now().toISOString(),
        sessionId,
        userId,
        event_type: eventType,
        schemaVersion: TELEMETRY_SCHEMA_VERSION,
        requestId: activeRequestId ?? uuid(),
        properties: { ...properties },
      });
      stats.queued += 1;
      if (queue.length > MAX_BUFFERED_EVENTS) {
        queue.splice(0, queue.length - MAX_BUFFERED_EVENTS);
        stats.discarded += 1;
      }
      ensureTimer();
      if (queue.length >= MAX_QUEUE_SIZE) void flush();
      else if (schema.delivery === "stream") scheduleStreamFlush();
      for (const listener of listeners) listener(eventType);
    } catch {
      stats.dropped += 1;
    }
  }

  /** Runs `run` so that every event it records carries `requestId`, the same id the API call received as X-Request-ID. */
  function withRequestId<T>(requestId: string, run: () => T): T {
    const previous = activeRequestId;
    activeRequestId = requestId;
    try {
      return run();
    } finally {
      activeRequestId = previous;
    }
  }

  /** Starts the page lifecycle hooks (flush when the tab is hidden or closed). Returns the cleanup. */
  function start(): () => void {
    if (typeof document === "undefined" || started) return () => undefined;
    started = true;
    const onVisibility = () => {
      if (document.visibilityState === "hidden") flushOnHide();
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", flushOnHide);
    return () => {
      started = false;
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", flushOnHide);
    };
  }

  function stop(): void {
    if (intervalId !== null) clearInterval(intervalId);
    if (streamTimer !== null) clearTimeout(streamTimer);
    intervalId = null;
    streamTimer = null;
  }

  return {
    track,
    flush,
    flushOnHide,
    start,
    stop,
    withRequestId,
    /** Lets the flow tracker learn that an event was recorded (for example that an order completed its flow). */
    onTracked: (listener: (eventType: string) => void): (() => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    newRequestId: uuid,
    isEnabled: enabled,
    snapshot: (): TelemetryStats => ({ ...stats, queued: queue.length }),
    pending: (): TelemetryEnvelope[] => [...inFlight, ...queue],
  };
}

// process.env.NEXT_PUBLIC_* is replaced at build time, so it has to be read with this exact spelling.
const service = createTelemetryService({ endpoint: (process.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT ?? "").trim() });

/** Records a usage event. `eventType` becomes the envelope field `event_type`; `properties` must match the event's allowlist. */
export function track(eventType: string, properties: Record<string, unknown>): void {
  service.track(eventType, properties);
}

/** Runs `run` so the events it records share `requestId` with the API request that caused them. */
export const withRequestId = service.withRequestId;
/** A fresh UUID for an API request (sent as X-Request-ID and shared with the events it causes). */
export const newRequestId = service.newRequestId;
export const onTracked = service.onTracked;
export const startTelemetry = service.start;
export const flushTelemetry = service.flush;
export const isTelemetryEnabled = service.isEnabled;
export const telemetrySnapshot = service.snapshot;
