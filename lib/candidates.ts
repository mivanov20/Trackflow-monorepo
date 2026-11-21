import {
  Candidate,
  CandidateFilters,
  CandidateListResponse,
  CandidateNote,
  CandidateNotesResponse,
  CandidatePayload,
} from "../types/candidates";

const API_BASE_URL = "https://playground.4geeks.com/tracker/api/v1";

export class CandidateApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "CandidateApiError";
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
}

const GENERIC_ERROR_MESSAGE = "Something went wrong while talking to the candidate service. Please try again in a moment.";

/** Mensajes legibles por código HTTP: el código nunca se muestra al usuario. */
function messageForStatus(status: number): string {
  if (status === 400 || status === 422) return "Some of the information is not valid. Review the form and try again.";
  if (status === 404) return "This candidate record could not be found. It may have been removed.";
  if (status === 409) return "This record was changed by someone else. Refresh the page and try again.";
  if (status === 429) return "Too many requests. Wait a moment and try again.";
  return GENERIC_ERROR_MESSAGE;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: HeadersInit = { Accept: "application/json" };
  if (options.body) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: options.method || "GET",
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
      ...(options.method || options.body ? {} : { next: { revalidate: 60 } }),
    });
  } catch {
    // Fallo de red/DNS/CORS: fetch rechaza con un TypeError técnico ("Failed to fetch").
    throw new CandidateApiError("We couldn't reach the candidate service. Check your connection and try again.", 0);
  }

  if (!response.ok) {
    throw new CandidateApiError(messageForStatus(response.status), response.status);
  }

  if (response.status === 204) return undefined as T;

  let text: string;
  try {
    text = await response.text();
  } catch {
    throw new CandidateApiError(GENERIC_ERROR_MESSAGE, response.status);
  }
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new CandidateApiError("The candidate service sent an unexpected response. Please try again.", response.status);
  }
}

/**
 * Mensaje seguro para la UI: `action` dice qué falló y la segunda frase qué hacer.
 * Los errores de la API ya son legibles; cualquier otro error (bug, TypeError) se resume genéricamente.
 */
export function candidateErrorMessage(error: unknown, action: string): string {
  return `${action} ${error instanceof CandidateApiError ? error.message : "Please try again."}`;
}

function buildQuery(filters?: CandidateFilters): string {
  const params = new URLSearchParams();
  Object.entries(filters || {}).forEach(([key, value]) => {
    if (value && value.trim()) params.set(key, value.trim());
  });
  const query = params.toString();
  return query ? `?${query}` : "";
}

export async function getCandidates(filters?: CandidateFilters): Promise<Candidate[]> {
  const payload = await request<CandidateListResponse | Candidate[]>(`/records${buildQuery(filters)}`);
  if (Array.isArray(payload)) return payload;
  return Array.isArray(payload.data) ? payload.data : [];
}

export async function getCandidate(id: string): Promise<Candidate> {
  return request<Candidate>(`/records/${encodeURIComponent(id)}`);
}

export async function createCandidate(payload: CandidatePayload): Promise<Candidate> {
  return request<Candidate>("/records", { method: "POST", body: payload });
}

export async function replaceCandidate(id: string | number, payload: CandidatePayload): Promise<Candidate> {
  return request<Candidate>(`/records/${encodeURIComponent(id)}`, { method: "PUT", body: payload });
}

export async function updateCandidateStatus(id: string | number, status: string): Promise<Candidate> {
  return request<Candidate>(`/records/${encodeURIComponent(id)}`, { method: "PATCH", body: { status } });
}

export async function updateCandidateStage(id: string | number, stage: string): Promise<Candidate> {
  return request<Candidate>(`/records/${encodeURIComponent(id)}`, { method: "PATCH", body: { stage } });
}

export async function getCandidateNotes(id: string | number): Promise<CandidateNote[]> {
  const payload = await request<CandidateNotesResponse | CandidateNote[]>(`/records/${encodeURIComponent(id)}/notes`);
  if (Array.isArray(payload)) return payload;
  return Array.isArray(payload.data) ? payload.data : [];
}

export async function addCandidateNote(id: string | number, content: string): Promise<CandidateNote> {
  return request<CandidateNote>(`/records/${encodeURIComponent(id)}/notes`, { method: "POST", body: { content } });
}

export async function deleteCandidateNote(id: string | number, noteId: string | number): Promise<void> {
  await request<void>(`/records/${encodeURIComponent(id)}/notes/${encodeURIComponent(noteId)}`, { method: "DELETE" });
}

export function candidateName(candidate: Candidate): string {
  return candidate.full_name || candidate.name || "Unnamed candidate";
}

export function candidatePosition(candidate: Candidate): string {
  return candidate.position || candidate.position_applied_for || candidate.role || "Position not provided";
}

export function candidateNoteContent(note: CandidateNote): string {
  return note.content || note.body || "";
}

export function humanize(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not provided";
  return String(value).replace(/_/g, " ");
}

export function formatDate(value: unknown): string {
  if (typeof value !== "string" || !value) return "Not provided";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
}