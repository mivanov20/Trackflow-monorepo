export const API_BASE_URL = (process.env.NEXT_PUBLIC_INVENTORY_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly fieldErrors: Record<string, string> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const GENERIC_ERROR_MESSAGE = "Something went wrong on our side. Please try again in a moment.";

const STATUS_MESSAGES: Record<number, string> = {
  401: "Your session has expired. Please sign in again.",
  403: "You do not have permission to do this.",
  404: "The requested record was not found. It may have been removed.",
  409: "This change conflicts with existing data. Refresh the page and try again.",
  429: "Too many requests. Wait a moment and try again.",
  503: "The service is temporarily unavailable. Please try again in a moment.",
};

/** Convierte un `loc` de validación FastAPI (p. ej. ["body", "tracking_number"]) en una etiqueta legible. */
function fieldLabel(location: unknown): string {
  const parts = Array.isArray(location) ? location.filter((part) => typeof part === "string" && part !== "body") : [];
  const last = parts[parts.length - 1];
  return typeof last === "string" ? last.replace(/_/g, " ") : "";
}

/**
 * Builds a message a user can act on. Server failures (5xx) and unknown bodies never reach the UI
 * as-is: they are replaced by plain-language text, so status codes, HTML error pages or validation
 * internals are not shown.
 */
function errorMessage(body: unknown, status: number): string {
  if (status >= 500) return STATUS_MESSAGES[status] ?? GENERIC_ERROR_MESSAGE;
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    const detail = record.detail ?? record.message;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      const fields = [...new Set(detail.map((item) => (item && typeof item === "object" ? fieldLabel((item as { loc?: unknown }).loc) : "")).filter(Boolean))];
      return fields.length > 0
        ? `Please check these fields and try again: ${fields.join(", ")}.`
        : "Some of the information sent is not valid. Please review the form and try again.";
    }
  }
  return STATUS_MESSAGES[status] ?? "The request could not be completed. Please review the information and try again.";
}

/** Reads `{ errors: [{ field, message }] }` (incident API validation errors) into a field map. */
function fieldErrorsFrom(body: unknown): Record<string, string> {
  const result: Record<string, string> = {};
  if (body && typeof body === "object" && Array.isArray((body as { errors?: unknown }).errors)) {
    for (const item of (body as { errors: unknown[] }).errors) {
      if (item && typeof item === "object" && "field" in item && "message" in item) {
        result[String(item.field)] = String(item.message);
      }
    }
  }
  return result;
}

const PROTECTED_PREFIXES = ["/backoffice/inventory/", "/backoffice/incidents"];

export async function apiRequest<T>(
  path: string,
  options: {
    method?: "GET" | "POST" | "PATCH";
    token?: string | null;
    body?: unknown;
    /** Abort the request after this many milliseconds (no timeout by default). */
    timeoutMs?: number;
    /** Name used in user-facing messages, for example "inventory" or "incident". */
    serviceName?: string;
  } = {},
): Promise<T> {
  const serviceName = options.serviceName ?? "inventory";
  const headers = new Headers({ Accept: "application/json" });
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (options.token) headers.set("Authorization", `Bearer ${options.token}`);

  const controller = new AbortController();
  const timer = options.timeoutMs ? setTimeout(() => controller.abort(), options.timeoutMs) : undefined;

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    });
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === "AbortError") {
      throw new ApiError(`The ${serviceName} service is taking too long to answer. Please try again.`, 0);
    }
    throw new ApiError(`Cannot reach the ${serviceName} service. Check your connection and try again.`, 0);
  } finally {
    if (timer) clearTimeout(timer);
  }

  const responseBody: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && options.token && typeof window !== "undefined") {
      try {
        window.localStorage.removeItem("trackflow-access-token");
        window.localStorage.removeItem("trackflow-auth-user");
      } catch {
        // Almacenamiento bloqueado: igualmente se redirige al login.
      }
      if (PROTECTED_PREFIXES.some((prefix) => window.location.pathname.startsWith(prefix))) {
        window.location.replace(`/backoffice/login?next=${encodeURIComponent(window.location.pathname)}`);
      }
    }
    throw new ApiError(errorMessage(responseBody, response.status), response.status, fieldErrorsFrom(responseBody));
  }
  if (responseBody === null && response.status !== 204) {
    // Una respuesta 2xx que no es JSON (proxy, página HTML) no debe llegar a la UI como datos.
    throw new ApiError(GENERIC_ERROR_MESSAGE, response.status);
  }
  return responseBody as T;
}

/** Message to show for any caught error: API messages are already user-safe; anything else is generic. */
export function messageFor(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}
