/**
 * Uncaught frontend errors and Web Vitals. Both are cross-cutting: they apply to every page, so they are
 * captured by global handlers installed once, not by the pages.
 */

import { connectionType, deviceClass, fnv1a64Hex, normalizeStack, routeTemplate, safeErrorName, sectionOf } from "./telemetry-domain";
import { track } from "./telemetry";

export type ErrorSource = "window_error" | "unhandled_rejection" | "react_error_boundary";

const FOLD_WINDOW_MS = 60_000;
const openErrors = new Map<string, { folded: number }>();

/**
 * Records an uncaught error. The message and the stack never leave the browser: only the error class and a
 * hash of the normalised stack. Repeats of the same error within a minute are folded into one follow-up event.
 */
export function trackFrontendError(source: ErrorSource, error: unknown, handledByBoundary: boolean): void {
  const name = safeErrorName(error instanceof Error ? error.name : "Error");
  const fingerprint = fnv1a64Hex(normalizeStack(error instanceof Error ? error.stack : undefined, name));
  const route = routeTemplate(window.location.pathname);
  const send = (occurrences: number) =>
    track("frontend_error_caught", { error_source: source, error_name: name, error_fingerprint: fingerprint, route_template: route, occurrences, handled_by_boundary: handledByBoundary });
  const open = openErrors.get(fingerprint);
  if (open) {
    open.folded += 1;
    return;
  }
  openErrors.set(fingerprint, { folded: 0 });
  send(1);
  setTimeout(() => {
    const entry = openErrors.get(fingerprint);
    openErrors.delete(fingerprint);
    if (entry && entry.folded > 0) send(entry.folded);
  }, FOLD_WINDOW_MS);
}

/** Installs `error` and `unhandledrejection` handlers on the window. Returns the cleanup. */
export function installErrorCapture(): () => void {
  const onError = (event: ErrorEvent) => trackFrontendError("window_error", event.error ?? new Error(event.message), false);
  const onRejection = (event: PromiseRejectionEvent) => trackFrontendError("unhandled_rejection", event.reason, false);
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  return () => {
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
  };
}

// --- Web Vitals ----------------------------------------------------------------------------

export interface WebVitalMetric {
  name: string;
  value: number;
}

interface VitalsRecord {
  ttfb_ms: number | null;
  fcp_ms: number | null;
  lcp_ms: number | null;
  cls: number | null;
  inp_ms: number | null;
}

const VITALS_SETTLE_MS = 10_000;
let vitals: VitalsRecord = { ttfb_ms: null, fcp_ms: null, lcp_ms: null, cls: null, inp_ms: null };
let vitalsRoute: string | null = null;
let vitalsReported = false;
let vitalsTimer: ReturnType<typeof setTimeout> | null = null;

/** Sends the vitals collected for the first page load, once. Called when the tab is hidden, on pagehide, or 10 s after the first metric. */
export function reportPageLoad(): void {
  if (vitalsReported || vitalsRoute === null) return;
  vitalsReported = true;
  if (vitalsTimer !== null) clearTimeout(vitalsTimer);
  track("page_load_recorded", {
    route_template: routeTemplate(vitalsRoute),
    section: sectionOf(vitalsRoute),
    navigation_type: "initial",
    ...vitals,
    device_class: deviceClass(),
    connection_type: connectionType(),
  });
}

/** Receives each Web Vitals metric from Next.js and groups them into one `page_load_recorded` event per page load. */
export function recordWebVital(metric: WebVitalMetric): void {
  if (vitalsReported) return;
  if (vitalsRoute === null) {
    vitalsRoute = window.location.pathname;
    vitalsTimer = setTimeout(reportPageLoad, VITALS_SETTLE_MS);
  }
  const value = Number.isFinite(metric.value) ? metric.value : null;
  if (value === null) return;
  if (metric.name === "TTFB") vitals.ttfb_ms = Math.round(value);
  else if (metric.name === "FCP") vitals.fcp_ms = Math.round(value);
  else if (metric.name === "LCP") vitals.lcp_ms = Math.round(value);
  else if (metric.name === "INP") vitals.inp_ms = Math.round(value);
  else if (metric.name === "CLS") vitals.cls = Math.round(value * 1000) / 1000;
}
