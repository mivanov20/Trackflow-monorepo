import { apiRequest } from "./api-client";

/** The contract of `/rfp/tickets` (services/routers/rfp.py). The API is the source of truth for every value here. */
export const TICKET_STATUSES = ["analyzing", "discarded", "intake_complete", "failed"] as const;
export type TicketStatus = (typeof TICKET_STATUSES)[number];

export const STATUS_LABELS: Record<TicketStatus, string> = {
  analyzing: "Analyzing",
  discarded: "Discarded",
  intake_complete: "Intake complete",
  failed: "Failed",
};

export const MAX_UPLOAD_MB = 10;
/** How often the page asks the API for news while some ticket is still being analysed. */
export const POLL_INTERVAL_MS = 2500;

export interface TicketSummary {
  ticket_id: string;
  rfp_id: string;
  status: TicketStatus;
  current_step: string;
  original_filename: string;
  client_name: string | null;
  client_country: string | null;
  departments_needed: string[];
  document_type: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface KeyAspects {
  summary: string;
  requirements: string[];
  stated_figures: Record<string, unknown>;
  asks_of_owner: string[];
  open_questions: string[];
  constraints: string[];
  contributes: string;
}

export interface DepartmentSection {
  department_id: string;
  department: string;
  owner: string;
  key_aspects: KeyAspects;
  approval_status: string;
  engine: string | null;
}

export interface TicketDetail extends TicketSummary {
  raw_pdf_path: string;
  progress: { step: string; detail: string; engine?: string | null; at: string }[];
  error_message: string | null;
  engine: string | null;
  classification: { is_rfp: boolean | null; document_type: string | null; reason: string | null; confidence: number | null };
  metadata: {
    client_name: string | null;
    client_country: string | null;
    currency: string | null;
    client_contact: { name?: string; title?: string; company?: string; email?: string };
    services_requested: string[];
    monthly_volume: number | null;
    deadline: string | null;
    budget_range: string | null;
    departments_needed: string[];
  };
  open_questions: string[];
  readability: {
    word_count?: number;
    estimated_tokens?: number;
    cost_band?: string;
    flesch_kincaid_grade?: number | null;
    gunning_fog?: number | null;
    flesch_reading_ease?: number | null;
    ari?: number | null;
    note?: string;
  };
  summary: string | null;
  sections: DepartmentSection[];
  handoff_state: string;
}

/** A ticket is final when nothing more will change: the page stops polling. */
export function isFinal(status: TicketStatus): boolean {
  return status !== "analyzing";
}

export function hasPending(tickets: Pick<TicketSummary, "status">[]): boolean {
  return tickets.some((ticket) => !isFinal(ticket.status));
}

export const SERVICE_LABELS: Record<string, string> = {
  warehousing: "Warehousing",
  last_mile: "Last mile",
  reverse_logistics: "Returns (reverse logistics)",
};

export const COUNTRY_LABELS: Record<string, string> = { US: "United States", ES: "Spain" };

/** The browser's first check of a file before it travels: the API checks again (the real PDF header and size). */
export function checkPdf(file: { name: string; size: number }): string {
  if (!file.name.toLowerCase().endsWith(".pdf")) return "Choose a PDF file.";
  if (file.size === 0) return "The file is empty.";
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `The PDF is too large (the limit is ${MAX_UPLOAD_MB} MB).`;
  return "";
}

/** `{"skus": 15000}` -> "SKUs: 15,000": the figures a department found in the RFP, written for people. */
export function figureLines(figures: Record<string, unknown>): string[] {
  const labels: Record<string, string> = {
    skus: "SKUs",
    pallets: "Pallets",
    monthly_orders: "Orders per month",
    preferred_location: "Preferred location",
    destinations: "Destinations",
    carrier_selection_requested: "Carrier selection requested",
    process_steps_requested: "Steps requested",
  };
  return Object.entries(figures).map(([key, value]) => {
    const shown = typeof value === "number" ? value.toLocaleString("en-US") : Array.isArray(value) ? value.join(", ") : value === true ? "yes" : String(value);
    return `${labels[key] ?? key.replace(/_/g, " ")}: ${shown}`;
  });
}

export function scoreText(value: number | null | undefined): string {
  return typeof value === "number" ? value.toFixed(1) : "not available";
}

export const rfpApi = {
  /** Uploads one PDF. The API answers at once (202) with the ticket in `analyzing`; the analysis continues on the server. */
  upload(token: string, file: File): Promise<TicketSummary> {
    const body = new FormData();
    body.append("file", file);
    return apiRequest<TicketSummary>("/rfp/tickets", { method: "POST", token, body, timeoutMs: 60_000, serviceName: "RFP intake" });
  },
  list(token: string): Promise<TicketSummary[]> {
    return apiRequest<TicketSummary[]>("/rfp/tickets", { token, timeoutMs: 20_000, serviceName: "RFP intake" });
  },
  get(token: string, ticketId: string): Promise<TicketDetail> {
    return apiRequest<TicketDetail>(`/rfp/tickets/${encodeURIComponent(ticketId)}`, { token, timeoutMs: 20_000, serviceName: "RFP intake" });
  },
};
