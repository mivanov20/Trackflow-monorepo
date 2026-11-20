import { ApiError, apiRequest } from "./api-client";

// Mirrors packages/shared/trackflow_shared/incidents.py; the API is the source of truth.
export const STATUSES = ["open", "in_progress", "resolved", "discarded"] as const;
export const ORIGINS = ["customer", "branch", "internal"] as const;
export const CATEGORIES = [
  "lost_parcel",
  "delivery_failure",
  "inventory_discrepancy",
  "carrier_issue",
  "returns_issue",
  "warehouse_incident",
  "system_failure",
  "client_complaint",
  "other",
] as const;
export const BRANCHES = ["central", "la_warehouse", "la_office", "zaragoza_warehouse", "zaragoza_office"] as const;

export type IncidentStatus = (typeof STATUSES)[number];
export type IncidentOrigin = (typeof ORIGINS)[number];
export type IncidentCategory = (typeof CATEGORIES)[number];
export type IncidentBranch = (typeof BRANCHES)[number];

export const STATUS_LABELS: Record<IncidentStatus, string> = {
  open: "Open",
  in_progress: "In progress",
  resolved: "Resolved",
  discarded: "Discarded",
};

/** Lifecycle: resolved and discarded are final states. */
export const STATUS_TRANSITIONS: Record<IncidentStatus, IncidentStatus[]> = {
  open: ["in_progress", "discarded"],
  in_progress: ["resolved", "discarded"],
  resolved: [],
  discarded: [],
};

export const ORIGIN_LABELS: Record<IncidentOrigin, string> = {
  customer: "Customer",
  branch: "Branch",
  internal: "Internal",
};

export const CATEGORY_LABELS: Record<IncidentCategory, string> = {
  lost_parcel: "Lost parcel",
  delivery_failure: "Delivery failure",
  inventory_discrepancy: "Inventory discrepancy",
  carrier_issue: "Carrier issue",
  returns_issue: "Returns issue",
  warehouse_incident: "Warehouse incident",
  system_failure: "System failure",
  client_complaint: "Client complaint",
  other: "Other",
};

export const BRANCH_LABELS: Record<IncidentBranch, string> = {
  central: "Central",
  la_warehouse: "Los Angeles — Warehouse",
  la_office: "Los Angeles — Office",
  zaragoza_warehouse: "Zaragoza — Warehouse",
  zaragoza_office: "Zaragoza — Office",
};

export interface Incident {
  id: number;
  title: string;
  description: string;
  category: IncidentCategory;
  status: IncidentStatus;
  origin: IncidentOrigin;
  branch: IncidentBranch;
  created_at: string;
  updated_at: string;
}

export interface IncidentInput {
  title: string;
  description: string;
  category: string;
  origin: string;
  branch: string;
  status: string;
}

export interface IncidentFilters {
  status?: string;
  origin?: string;
  branch?: string;
  category?: string;
}

export interface IncidentSummary {
  total: number;
  by_status: Record<IncidentStatus, number>;
  by_category: Record<IncidentCategory, number>;
  by_origin: Record<IncidentOrigin, number>;
  by_branch: Record<IncidentBranch, number>;
}

const REQUEST_OPTIONS = { timeoutMs: 15000, serviceName: "incident" } as const;
const GENERIC_ERROR = "Something went wrong on our side. Please try again in a moment.";

const ALL_LABELS: Record<string, string> = {
  ...STATUS_LABELS,
  ...ORIGIN_LABELS,
  ...CATEGORY_LABELS,
  ...BRANCH_LABELS,
};

/** Replaces internal codes with display labels in "must be one of: ..." style messages. */
function humanize(message: string): string {
  return message.replace(/(one of|move to): ([^.]*)\./, (_match, lead: string, list: string) => {
    const labels = list.split(", ").map((code) => ALL_LABELS[code] ?? code);
    return `${lead}: ${labels.join(", ")}.`;
  });
}

/**
 * Turns any error into text a user can act on: server errors and unexpected failures
 * become a generic message, validation messages are shown in plain language.
 */
export function userMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) {
      // Network failures read as developer text ("check that the API is running"); soften them here.
      return error.message.startsWith("Cannot reach")
        ? "We could not reach the incident service. Check your connection and try again."
        : error.message;
    }
    if (error.status === 400) return humanize(error.message);
    if (error.status === 404) return error.message;
  }
  return GENERIC_ERROR;
}

/** Field-level validation messages from a 400 response, with labels instead of codes. */
export function fieldErrorsOf(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError)) return {};
  return Object.fromEntries(Object.entries(error.fieldErrors).map(([field, message]) => [field, humanize(message)]));
}

export const incidentsApi = {
  list(token: string, filters: IncidentFilters = {}): Promise<Incident[]> {
    const query = new URLSearchParams(
      Object.entries(filters).filter((entry): entry is [string, string] => Boolean(entry[1])),
    ).toString();
    return apiRequest<Incident[]>(`/api/incidents${query ? `?${query}` : ""}`, { token, ...REQUEST_OPTIONS });
  },
  create(token: string, payload: IncidentInput): Promise<Incident> {
    return apiRequest<Incident>("/api/incidents", { method: "POST", token, body: payload, ...REQUEST_OPTIONS });
  },
  updateStatus(token: string, id: number, status: IncidentStatus): Promise<Incident> {
    return apiRequest<Incident>(`/api/incidents/${id}/status`, {
      method: "PATCH",
      token,
      body: { status },
      ...REQUEST_OPTIONS,
    });
  },
  summary(token: string): Promise<IncidentSummary> {
    return apiRequest<IncidentSummary>("/api/incidents/summary", { token, ...REQUEST_OPTIONS });
  },
};
