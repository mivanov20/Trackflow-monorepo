import { clearSession, redirectToLogin } from "./session";

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

/** Nombre del campo al que apunta un `loc` de validación FastAPI (p. ej. ["body", "email"] -> "email"). */
function fieldKey(location: unknown): string {
  const parts = Array.isArray(location) ? location.filter((part) => typeof part === "string" && part !== "body") : [];
  const last = parts[parts.length - 1];
  return typeof last === "string" ? last : "";
}

/** Traduce un error de validación de FastAPI/Pydantic a una frase para el usuario (nunca el texto técnico). */
function plainValidationMessage(item: { type?: unknown; msg?: unknown; ctx?: unknown; loc?: unknown }): string {
  const type = typeof item.type === "string" ? item.type : "";
  const ctx = (item.ctx && typeof item.ctx === "object" ? item.ctx : {}) as Record<string, unknown>;
  if (type === "missing") return "This field is required.";
  if (type === "string_too_short") return typeof ctx.min_length === "number" ? `Use at least ${ctx.min_length} characters.` : "This value is too short.";
  if (type === "string_too_long") return typeof ctx.max_length === "number" ? `Use at most ${ctx.max_length} characters.` : "This value is too long.";
  if (type === "extra_forbidden") return "This field is not accepted here.";
  if (fieldKey(item.loc) === "email") return "Enter a valid email address.";
  // Los validadores propios de la API (por ejemplo el límite de la contraseña) ya escriben frases legibles.
  if (type === "value_error" && typeof item.msg === "string") return item.msg.replace(/^Value error, /, "");
  return "This value is not valid.";
}

/**
 * Lee los errores por campo de una respuesta 4xx: `{ errors: [{ field, message }] }` (API de incidentes)
 * o `{ detail: [{ loc, type, msg }] }` (validación 422 de FastAPI).
 */
function fieldErrorsFrom(body: unknown): Record<string, string> {
  const result: Record<string, string> = {};
  if (!body || typeof body !== "object") return result;
  const record = body as { errors?: unknown; detail?: unknown };
  if (Array.isArray(record.errors)) {
    for (const item of record.errors) {
      if (item && typeof item === "object" && "field" in item && "message" in item) {
        result[String(item.field)] = String(item.message);
      }
    }
  }
  if (Array.isArray(record.detail)) {
    for (const item of record.detail) {
      if (!item || typeof item !== "object") continue;
      const key = fieldKey((item as { loc?: unknown }).loc);
      if (key && !result[key]) result[key] = plainValidationMessage(item as { type?: unknown; msg?: unknown; ctx?: unknown; loc?: unknown });
    }
  }
  return result;
}

export async function apiRequest<T>(
  path: string,
  options: {
    method?: "GET" | "POST" | "PUT" | "PATCH";
    token?: string | null;
    /** JSON-serializable data, or a FormData (sent as multipart, e.g. a file upload). */
    body?: unknown;
    /** How to read a successful response: parsed JSON (default) or the raw text (e.g. a CSV download). */
    as?: "json" | "text";
    /** Abort the request after this many milliseconds (no timeout by default). */
    timeoutMs?: number;
    /** Name used in user-facing messages, for example "inventory" or "incident". */
    serviceName?: string;
  } = {},
): Promise<T> {
  const serviceName = options.serviceName ?? "inventory";
  const headers = new Headers({ Accept: "application/json" });
  const isForm = typeof FormData !== "undefined" && options.body instanceof FormData;
  // Con FormData el navegador pone el Content-Type multipart con su boundary: no hay que fijarlo.
  if (options.body !== undefined && !isForm) headers.set("Content-Type", "application/json");
  if (options.token) headers.set("Authorization", `Bearer ${options.token}`);

  const controller = new AbortController();
  const timer = options.timeoutMs ? setTimeout(() => controller.abort(), options.timeoutMs) : undefined;

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : isForm ? (options.body as FormData) : JSON.stringify(options.body),
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

  const rawBody = await response.text().catch(() => "");
  let parsedBody: unknown = null;
  try {
    parsedBody = rawBody ? JSON.parse(rawBody) : null;
  } catch {
    parsedBody = null; // no es JSON (por ejemplo un CSV o una página de error de un proxy)
  }
  const responseBody = parsedBody;
  if (!response.ok) {
    if (response.status === 401 && options.token && typeof window !== "undefined") {
      // Token caducado o inválido en una llamada protegida: se borra la sesión y se vuelve a /login.
      clearSession();
      redirectToLogin("expired");
    }
    throw new ApiError(errorMessage(responseBody, response.status), response.status, fieldErrorsFrom(responseBody));
  }
  if (options.as === "text") return rawBody as T;
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
