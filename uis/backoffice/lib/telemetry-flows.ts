/**
 * Navigation and form telemetry, kept in one place so no page tracks anything itself:
 * page views, guided flows (started, abandoned) and form validation failures.
 *
 * A route is mapped to a flow and a form here. Pages stay untouched: the provider forwards route changes
 * and DOM events, and the events that complete a flow are learned from `track()` itself.
 */

import { getAccessToken } from "./session";
import { deviceClass, routeTemplate, sectionOf, type Section } from "./telemetry-domain";
import { onTracked, track } from "./telemetry";

export type FlowName = "inbound_order" | "outbound_order" | "incident_report" | "incident_file_analysis" | "registration" | "password_reset" | "password_change" | "stock_count";
type FormName =
  | "inbound_order" | "outbound_order" | "incident_report" | "registration" | "login" | "password_reset_request"
  | "password_reset" | "password_change" | "profile_edit" | "stock_count";
type LastStep = "form_opened" | "field_edited" | "validation_failed" | "submit_failed";

interface RouteTracking {
  flow?: FlowName;
  form?: FormName;
}

const ROUTES: Record<string, RouteTracking> = {
  "/backoffice/inventory/orders/inbound": { flow: "inbound_order", form: "inbound_order" },
  "/backoffice/inventory/orders/outbound": { flow: "outbound_order", form: "outbound_order" },
  "/backoffice/inventory/stock-count": { flow: "stock_count", form: "stock_count" },
  "/backoffice/incidents/new": { flow: "incident_report", form: "incident_report" },
  "/backoffice/incidents/analyze": { flow: "incident_file_analysis" },
  "/register": { flow: "registration", form: "registration" },
  "/reset-password": { flow: "password_reset", form: "password_reset" },
  "/forgot-password": { form: "password_reset_request" },
  "/account/change-password": { flow: "password_change", form: "password_change" },
  "/account/profile": { form: "profile_edit" },
  "/login": { form: "login" },
  "/backoffice/login": { form: "login" },
};

/** The event that proves a flow reached its end. */
const COMPLETED_BY: Record<string, FlowName> = {
  inbound_order_created: "inbound_order",
  outbound_order_created: "outbound_order",
  incident_created: "incident_report",
  incident_file_analyzed: "incident_file_analysis",
  user_registered: "registration",
  password_changed: "password_change",
  inventory_discrepancy_detected: "stock_count",
};

/** Events that mean the user tried to submit and the system said no. */
const SUBMIT_FAILURES = new Set(["inbound_order_rejected", "outbound_order_rejected", "incident_file_rejected", "network_request_failed", "login_attempt_failed"]);

/** Closed list of field names the schema allows, in the order they are matched against an input's id, name or label. */
const FIELD_PATTERNS: [RegExp, string][] = [
  [/current[-_ ]?password/i, "current_password"],
  [/new[-_ ]?password/i, "new_password"],
  [/confirm/i, "confirm_password"],
  [/password/i, "password"],
  [/e-?mail/i, "email"],
  [/phone|tel\b/i, "phone"],
  [/address/i, "address"],
  [/tracking/i, "tracking_number"],
  [/quantity|qty|counted/i, "quantity"],
  [/reference/i, "reference"],
  [/exit[-_ ]?type/i, "exit_type"],
  [/product|sku/i, "product"],
  [/title/i, "title"],
  [/description/i, "description"],
  [/category/i, "category"],
  [/origin/i, "origin"],
  [/branch/i, "branch"],
  [/status/i, "status"],
  [/name/i, "name"],
];

/** Closed-list name of a form field, or null when it is not one of them. Values are never read. */
export function fieldNameOf(element: Element): string | null {
  const labelText = element.id ? (element.ownerDocument.querySelector(`label[for="${CSS.escape(element.id)}"]`)?.textContent ?? "") : "";
  const wrapping = element.closest("label")?.textContent ?? "";
  const candidates = [element.id, element.getAttribute("name") ?? "", labelText, wrapping];
  for (const text of candidates) {
    const match = FIELD_PATTERNS.find(([pattern]) => pattern.test(text));
    if (match) return match[1];
  }
  return null;
}

interface ActiveRoute {
  flow: FlowName | null;
  form: FormName | null;
  section: Section;
  started: boolean;
  startedAt: number;
  completed: boolean;
  lastStep: LastStep;
  touched: Set<string>;
  submitAttempts: number;
}

let active: ActiveRoute | null = null;
let previousSection: Section | null = null;
let lastView: { route: string; at: number } | null = null;
let firstView = true;
let invalidFields = new Set<string>();
let validationTimer: ReturnType<typeof setTimeout> | null = null;

const REPEAT_VIEW_WINDOW_MS = 2_000;
const VALIDATION_SETTLE_MS = 150;

// --- page views ----------------------------------------------------------------------------

function referrerSection(): Section | "external" | null {
  if (typeof document === "undefined") return null;
  if (!firstView) return previousSection;
  if (!document.referrer) return null;
  try {
    const referrer = new URL(document.referrer);
    return referrer.origin === window.location.origin ? sectionOf(referrer.pathname) : "external";
  } catch {
    return null;
  }
}

function trackPageView(pathname: string): void {
  const route = routeTemplate(pathname);
  const nowMs = Date.now();
  // A double render or a redirect shows the same route twice within moments: count it once.
  if (lastView && lastView.route === route && nowMs - lastView.at < REPEAT_VIEW_WINDOW_MS) return;
  lastView = { route, at: nowMs };
  const section = sectionOf(pathname);
  track("page_viewed", {
    section,
    route_template: route,
    referrer_section: referrerSection(),
    device_class: deviceClass(),
    is_authenticated: getAccessToken() !== null,
  });
  previousSection = section;
  firstView = false;
}

// --- flows ---------------------------------------------------------------------------------

function abandonActiveFlow(): void {
  const current = active;
  if (!current?.flow || !current.started || current.completed) return;
  current.completed = true; // a flow is reported once
  track("flow_abandoned", {
    flow_name: current.flow,
    last_step: current.lastStep,
    elapsed_seconds: Math.min(86_400, Math.max(0, Math.round((Date.now() - current.startedAt) / 1000))),
    fields_touched: Math.min(50, current.touched.size),
  });
}

/** Called on every route change: closes the previous flow (abandoned if unfinished), opens the new route and records the view. */
export function enterRoute(pathname: string, search = ""): void {
  if (search.includes("reset=success")) completeFlow("password_reset");
  abandonActiveFlow();
  invalidFields = new Set();
  const config = ROUTES[routeTemplate(pathname)];
  active = {
    flow: config?.flow ?? null,
    form: config?.form ?? null,
    section: sectionOf(pathname),
    started: false,
    startedAt: 0,
    completed: false,
    lastStep: "form_opened",
    touched: new Set(),
    submitAttempts: 0,
  };
  trackPageView(pathname);
}

/** Marks the flow as finished so leaving the page is not reported as abandonment. */
export function completeFlow(flow: FlowName): void {
  if (active?.flow === flow) active.completed = true;
}

/** The user left the page (tab closed, reload, navigation to another site). */
export function leavePage(): void {
  abandonActiveFlow();
}

function startFlowIfNeeded(): void {
  if (!active?.flow || active.started) return;
  active.started = true;
  active.startedAt = Date.now();
  track("flow_started", { flow_name: active.flow, entry_section: active.section });
}

/** First focus or edit inside the page content starts the flow; edits also count the fields touched. */
function onInteraction(event: Event): void {
  const target = event.target;
  if (!active || !(target instanceof Element) || !target.closest("main, form")) return;
  if (!target.matches("input, select, textarea")) return;
  startFlowIfNeeded();
  if (event.type === "input" || event.type === "change") {
    const field = fieldNameOf(target) ?? target.id ?? target.getAttribute("name") ?? "field";
    active.touched.add(field);
    if (active.lastStep !== "validation_failed") active.lastStep = "field_edited";
  }
}

// --- form validation -----------------------------------------------------------------------

function reportValidation(form: Element | null): void {
  validationTimer = null;
  const current = active;
  if (!current?.form) return;
  const invalid = new Set(invalidFields);
  invalidFields = new Set();
  // Forms validated in the page mark each wrong field with aria-invalid; native validation reports through `invalid` events.
  form?.querySelectorAll('[aria-invalid="true"]').forEach((element) => {
    const name = fieldNameOf(element);
    if (name) invalid.add(name);
  });
  if (invalid.size === 0) return;
  current.submitAttempts += 1;
  current.lastStep = "validation_failed";
  const fieldNames = [...invalid];
  track("form_validation_failed", {
    form_name: current.form,
    field_names: fieldNames,
    error_count: Math.min(50, fieldNames.length),
    attempt_number: Math.min(50, current.submitAttempts),
  });
}

function scheduleValidationCheck(form: Element | null): void {
  if (validationTimer !== null) clearTimeout(validationTimer);
  validationTimer = setTimeout(() => reportValidation(form), VALIDATION_SETTLE_MS);
}

function onSubmit(event: Event): void {
  if (event.target instanceof HTMLFormElement) {
    startFlowIfNeeded();
    scheduleValidationCheck(event.target);
  }
}

function onInvalid(event: Event): void {
  if (!(event.target instanceof Element)) return;
  const name = fieldNameOf(event.target);
  if (name) invalidFields.add(name);
  scheduleValidationCheck(event.target.closest("form"));
}

/** Learns from the event stream: a completion event ends the flow, a refusal marks the last step as a failed submit. */
function onEventTracked(eventType: string): void {
  const completed = COMPLETED_BY[eventType];
  if (completed) completeFlow(completed);
  if (SUBMIT_FAILURES.has(eventType) && active && active.lastStep !== "validation_failed") active.lastStep = "submit_failed";
}

/** Attaches the DOM listeners (capture phase, so they see events the page stops). Returns the cleanup. */
export function installFlowListeners(): () => void {
  const unsubscribe = onTracked(onEventTracked);
  document.addEventListener("focusin", onInteraction, true);
  document.addEventListener("input", onInteraction, true);
  document.addEventListener("change", onInteraction, true);
  document.addEventListener("submit", onSubmit, true);
  document.addEventListener("invalid", onInvalid, true);
  return () => {
    unsubscribe();
    document.removeEventListener("focusin", onInteraction, true);
    document.removeEventListener("input", onInteraction, true);
    document.removeEventListener("change", onInteraction, true);
    document.removeEventListener("submit", onSubmit, true);
    document.removeEventListener("invalid", onInvalid, true);
    if (validationTimer !== null) clearTimeout(validationTimer);
  };
}
