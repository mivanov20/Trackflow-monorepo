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

function errorMessage(body: unknown, statusText: string): string {
  if (typeof body === "string" && body.trim()) return body;
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    const detail = record.detail ?? record.message ?? record.error;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((item) => {
        if (item && typeof item === "object" && "msg" in item) return String(item.msg);
        return String(item);
      }).join(" ");
    }
  }
  return statusText || "The request failed. Please try again.";
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
    throw new ApiError(`Cannot reach the ${serviceName} service. Check that the API is running.`, 0);
  } finally {
    if (timer) clearTimeout(timer);
  }

  const responseBody: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && options.token && typeof window !== "undefined") {
      window.localStorage.removeItem("trackflow-access-token");
      window.localStorage.removeItem("trackflow-auth-user");
      if (PROTECTED_PREFIXES.some((prefix) => window.location.pathname.startsWith(prefix))) {
        window.location.replace(`/backoffice/login?next=${encodeURIComponent(window.location.pathname)}`);
      }
    }
    throw new ApiError(errorMessage(responseBody, response.statusText), response.status, fieldErrorsFrom(responseBody));
  }
  return responseBody as T;
}
