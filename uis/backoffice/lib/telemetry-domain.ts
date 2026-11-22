/**
 * Pure helpers that turn what the application knows (warehouse codes, client names, routes, stack traces)
 * into the closed vocabularies of the telemetry plan. No event is recorded here.
 */

import { getCurrentUser } from "./session";

export type WarehouseName = "los_angeles" | "zaragoza";
export type CountryCode = "US" | "ES";
export type ActorRole = "admin" | "manager" | "user";

const WAREHOUSES: Record<string, { name: WarehouseName; country: CountryCode }> = {
  LA: { name: "los_angeles", country: "US" },
  ZGZ: { name: "zaragoza", country: "ES" },
};

/** `LA` -> `los_angeles`, `ZGZ` -> `zaragoza`; null for anything else. */
export function warehouseName(code: string | null | undefined): WarehouseName | null {
  return code ? (WAREHOUSES[code]?.name ?? null) : null;
}

/** Country of a warehouse code, so dashboards can compare the United States with Spain without a join. */
export function countryOf(code: string | null | undefined): CountryCode | null {
  return code ? (WAREHOUSES[code]?.country ?? null) : null;
}

/** Stable client key until a clients table exists: the lowercase slug of the brand name (`Fashion Co` -> `fashion-co`). */
export function clientIdOf(clientName: string | null | undefined): string | null {
  const slug = (clientName ?? "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/-+$/, "");
  return slug === "" ? null : slug;
}

/** Role of the signed-in user, `user` when the session is not readable. */
export function actorRole(): ActorRole {
  const role = getCurrentUser()?.role;
  return role === "admin" || role === "manager" ? role : "user";
}

/** Turns ids in a path into `{id}` and drops the query string: `/inventory/products/12?x=1` -> `/inventory/products/{id}`. */
export function routeTemplate(path: string): string {
  const withoutQuery = (path.split(/[?#]/)[0] || "/").replace(/\/+$/, "") || "/";
  const templated = withoutQuery
    .split("/")
    .map((segment) => (/^\d+$/.test(segment) || /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(segment) ? "{id}" : segment))
    .join("/")
    .replace(/[^A-Za-z0-9_{}./-]/g, "")
    .slice(0, 120);
  return templated.startsWith("/") ? templated : `/${templated}`;
}

export type Section =
  | "overview" | "inventory_products" | "inventory_inbound_order" | "inventory_outbound_order" | "inventory_order_history"
  | "inventory_stock_count" | "telemetry_report" | "incidents_list" | "incidents_new" | "incidents_file_analysis" | "account_profile"
  | "account_change_password" | "auth_login" | "auth_register" | "auth_forgot_password" | "auth_reset_password" | "other";

const SECTIONS: Record<string, Section> = {
  "/": "overview",
  "/backoffice/inventory/products": "inventory_products",
  "/backoffice/inventory/orders/inbound": "inventory_inbound_order",
  "/backoffice/inventory/orders/outbound": "inventory_outbound_order",
  "/backoffice/inventory/orders": "inventory_order_history",
  "/backoffice/inventory/stock-count": "inventory_stock_count",
  "/backoffice/telemetry": "telemetry_report",
  "/backoffice/incidents": "incidents_list",
  "/backoffice/incidents/new": "incidents_new",
  "/backoffice/incidents/analyze": "incidents_file_analysis",
  "/account/profile": "account_profile",
  "/account/change-password": "account_change_password",
  "/login": "auth_login",
  "/backoffice/login": "auth_login",
  "/register": "auth_register",
  "/forgot-password": "auth_forgot_password",
  "/reset-password": "auth_reset_password",
};

/** Section of the backoffice a route belongs to. */
export function sectionOf(pathname: string): Section {
  return SECTIONS[routeTemplate(pathname)] ?? "other";
}

export type DeviceClass = "desktop" | "tablet" | "mobile";

/** Coarse class from the viewport width and the pointer type. The user agent string is never read or stored. */
export function deviceClass(): DeviceClass {
  if (typeof window === "undefined") return "desktop";
  const width = window.innerWidth;
  if (width < 640) return "mobile";
  const coarse = typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
  return width < 1100 || coarse ? "tablet" : "desktop";
}

export type ConnectionType = "4g" | "3g" | "2g" | "slow-2g" | "unknown";

export function connectionType(): ConnectionType {
  const type = (typeof navigator !== "undefined" ? (navigator as Navigator & { connection?: { effectiveType?: string } }).connection?.effectiveType : undefined) ?? "unknown";
  return type === "4g" || type === "3g" || type === "2g" || type === "slow-2g" ? type : "unknown";
}

/** 64-bit FNV-1a hash as 16 hex characters. It only groups repeated errors; it is not cryptographic. */
export function fnv1a64Hex(text: string): string {
  let hash = 0xcbf29ce484222325n;
  const prime = 0x100000001b3n;
  const mask = 0xffffffffffffffffn;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= BigInt(text.charCodeAt(index));
    hash = (hash * prime) & mask;
  }
  return hash.toString(16).padStart(16, "0");
}

/** Stack without file origins, query strings or messages, so the same defect hashes the same on every machine. */
export function normalizeStack(stack: string | undefined, fallback: string): string {
  const frames = (stack ?? "")
    .split("\n")
    .filter((line) => /^\s*at\s|@/.test(line))
    .slice(0, 8)
    .map((line) => line.replace(/https?:\/\/[^/\s)]+/g, "").replace(/\?[^:)\s]*/g, "").replace(/\s+/g, " ").trim());
  return frames.length > 0 ? frames.join("|") : fallback;
}

/** Error class name restricted to the characters the schema allows. */
export function safeErrorName(name: unknown): string {
  const text = typeof name === "string" ? name : "";
  return /^[A-Za-z0-9_$.]{1,80}$/.test(text) ? text : "Error";
}

/** SKU code as the schema accepts it, or null when it holds characters outside the closed alphabet. */
export function productIdOf(sku: string | null | undefined): string | null {
  return sku && /^[A-Za-z0-9._-]{1,64}$/.test(sku) ? sku : null;
}

/** Seconds since an API timestamp. The API sends naive UTC times, so a missing zone is read as UTC. */
export function secondsSince(timestamp: string): number {
  const zoned = /(Z|[+-]\d{2}:?\d{2})$/.test(timestamp) ? timestamp : `${timestamp}Z`;
  const elapsed = Math.round((Date.now() - Date.parse(zoned)) / 1000);
  return Number.isFinite(elapsed) ? Math.max(0, elapsed) : 0;
}
