import { apiRequest } from "./api-client";

/** The contract of `/rfp/tickets` (services/routers/rfp.py). The API is the source of truth for every value here. */
export const TICKET_STATUSES = ["analyzing", "discarded", "intake_complete", "failed", "drafting", "under_evaluation", "needs_human_review", "waiting_for_approval", "done"] as const;
export type TicketStatus = (typeof TICKET_STATUSES)[number];

export const STATUS_LABELS: Record<TicketStatus, string> = {
  analyzing: "Analyzing",
  discarded: "Discarded",
  intake_complete: "Intake complete",
  failed: "Failed",
  drafting: "Drafting",
  under_evaluation: "Under evaluation",
  needs_human_review: "Needs human review",
  waiting_for_approval: "Waiting for approval",
  done: "Done",
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
  proposal_state: string;
  approval_state: string;
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
  draft_status: string;
  iterations: number;
  draft_engine: string | null;
  draft_content: string | null;
  evaluation_result: EvaluationResult | null;
  approval_round: number;
  approver: string | null;
  approved_at: string | null;
  approval_comment: string | null;
  approval_history: { round: number; decision: string; comment: string; approver: string; at: string; trigger_id?: string }[];
  commitments: Record<string, unknown>;
  awaiting_approval: boolean;
  responsible: string;
}

export type DecisionKind = "approve" | "request_changes" | "reject";

export interface DecisionInput {
  decision: DecisionKind;
  approver: string;
  comment?: string;
  edited_content?: string;
  commitments?: Record<string, number>;
}

export interface FinalDocument {
  ticket_id: string;
  rfp_id: string;
  currency: string;
  generated_at: string;
  content_markdown: string;
}

export interface TraceEvent {
  seq: number;
  part: number;
  agent: string;
  node: string;
  department_id: string | null;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  at: string;
}

/** The structured result of the three evaluators for the latest draft of a section (services: `evaluation_results`). */
export interface EvaluationResult {
  section_id: string;
  department_id: string;
  iteration: number;
  readability: { pass: boolean; score: number | null; details: { problems?: string[]; word_count?: number } };
  relevance: { pass: boolean; missing_aspects: string[]; unsupported_figures?: string[] };
  compliance: { pass: boolean; rule_ids: string[]; violations: string[] };
  overall_pass: boolean;
  feedback_for_generator: string;
  history?: { iteration: number; overall_pass: boolean; feedback_for_generator: string }[];
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
  part3_handoff: { needs_human_review?: string[] };
  document_ready: boolean;
}

/**
 * True while something is still going to change: the intake or the drafting is running (or queued). The page polls only then.
 * `under_evaluation` is also the resting status of a proposal whose sections all passed, which `proposal_state` tells apart (`ready_for_part_3`).
 */
export function inProgress(ticket: Pick<TicketSummary, "status" | "proposal_state"> & { approval_state?: string }): boolean {
  if (ticket.proposal_state === "queued" || ticket.approval_state === "queued") return true;
  if (ticket.status === "analyzing" || ticket.status === "drafting") return true;
  return ticket.status === "under_evaluation" && ticket.proposal_state !== "ready_for_part_3";
}

/** The page also keeps asking while a ticket waits for its owners: somebody else may decide, and the page should show it. */
export function hasPending(tickets: (Pick<TicketSummary, "status" | "proposal_state"> & { approval_state?: string })[]): boolean {
  return tickets.some((ticket) => inProgress(ticket) || ticket.status === "waiting_for_approval");
}

/** What the badge says. Part 3 statuses do not exist yet, so a proposal whose sections all passed is "Ready for approval" (a label, not a status). */
export function statusLabel(ticket: Pick<TicketSummary, "status" | "proposal_state"> & { approval_state?: string }): string {
  if (ticket.status === "under_evaluation" && ticket.proposal_state === "ready_for_part_3") return "Ready for approval";
  return STATUS_LABELS[ticket.status];
}

export const DRAFT_STATUS_LABELS: Record<string, string> = {
  pending: "Waiting",
  drafting: "Drafting",
  under_evaluation: "Being evaluated",
  passed: "Passed",
  needs_human_review: "Needs human review",
};

export const APPROVAL_STATUS_LABELS: Record<string, string> = { pending: "Waiting for the owner", approved: "Approved", rejected: "Rejected" };

/** Part 3 can be started by hand when the sections are ready and the approvals have not begun (they normally begin by themselves). */
export function canStartApproval(ticket: Pick<TicketSummary, "status" | "proposal_state" | "approval_state">): boolean {
  return (ticket.status === "under_evaluation" || ticket.status === "needs_human_review") && ticket.proposal_state === "ready_for_part_3" && ticket.approval_state === "not_started";
}

/** The browser's check of a decision before it is sent (the API checks again): changes and rejections need a comment that says what to change. */
export function checkDecision(input: { decision: DecisionKind; comment: string; capacity?: string }): string {
  if (input.decision !== "approve" && input.comment.trim().length < 5) return "Say what has to change: a comment is required to reject or to request changes.";
  if (input.capacity && input.capacity.trim() !== "" && !/^[1-9]\d{0,7}$/.test(input.capacity.trim())) return "The committed capacity must be a whole number of orders per month.";
  return "";
}

/** A ticket whose intake is complete can (re)start the proposal when it is not already running. */
export function canStartProposal(ticket: Pick<TicketSummary, "status" | "proposal_state">): boolean {
  return ticket.status === "intake_complete" && ticket.proposal_state !== "queued" && ticket.proposal_state !== "in_progress";
}

export const SERVICE_LABELS: Record<string, string> = {
  warehousing: "Warehousing",
  last_mile: "Last mile",
  reverse_logistics: "Returns (reverse logistics)",
};

export const COUNTRY_LABELS: Record<string, string> = { US: "United States", ES: "Spain" };

export const DEPARTMENT_LABELS: Record<string, string> = { warehouse: "Warehousing", lastmile: "Last mile", reverse: "Returns" };

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
  startApproval(token: string, ticketId: string): Promise<TicketSummary> {
    return apiRequest<TicketSummary>(`/rfp/tickets/${encodeURIComponent(ticketId)}/approval`, { method: "POST", token, timeoutMs: 30_000, serviceName: "RFP intake" });
  },
  /** Registers the owner's decision; the API resumes THAT department's paused branch (and the arbitration and the final document when it was the last one). */
  decide(token: string, ticketId: string, departmentId: string, body: DecisionInput): Promise<TicketSummary> {
    return apiRequest<TicketSummary>(`/rfp/tickets/${encodeURIComponent(ticketId)}/sections/${encodeURIComponent(departmentId)}/decision`, { method: "POST", token, body, timeoutMs: 60_000, serviceName: "RFP intake" });
  },
  document(token: string, ticketId: string): Promise<FinalDocument> {
    return apiRequest<FinalDocument>(`/rfp/tickets/${encodeURIComponent(ticketId)}/document`, { token, timeoutMs: 20_000, serviceName: "RFP intake" });
  },
  trace(token: string, ticketId: string): Promise<{ events: TraceEvent[] }> {
    return apiRequest<{ events: TraceEvent[] }>(`/rfp/tickets/${encodeURIComponent(ticketId)}/trace`, { token, timeoutMs: 20_000, serviceName: "RFP intake" });
  },
  startProposal(token: string, ticketId: string): Promise<TicketSummary> {
    return apiRequest<TicketSummary>(`/rfp/tickets/${encodeURIComponent(ticketId)}/proposal`, { method: "POST", token, timeoutMs: 20_000, serviceName: "RFP intake" });
  },
  get(token: string, ticketId: string): Promise<TicketDetail> {
    return apiRequest<TicketDetail>(`/rfp/tickets/${encodeURIComponent(ticketId)}`, { token, timeoutMs: 20_000, serviceName: "RFP intake" });
  },
};
