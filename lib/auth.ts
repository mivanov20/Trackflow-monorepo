/**
 * Cliente de la API de TrackFlow para autenticación: login, registro, cuenta y perfil.
 * Es independiente de `lib/candidates.ts`: la API externa de candidatos NO recibe nunca el token.
 */

import { clearSession, redirectToLogin, saveSession, getAccessToken, type SessionUser } from "./session";

export { clearSession, getAccessToken, getCurrentUser, saveSession, saveUser } from "./session";
export type { SessionUser as AuthUser } from "./session";

export const API_BASE_URL = (process.env.NEXT_PUBLIC_INVENTORY_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export const GENERIC_ERROR_MESSAGE = "Something went wrong on our side. Please try again in a moment.";

const STATUS_MESSAGES: Record<number, string> = {
  401: "Your session has expired. Please sign in again.",
  403: "You do not have permission to do this.",
  404: "The requested record was not found.",
  409: "This change conflicts with existing data. Refresh the page and try again.",
  429: "Too many requests. Wait a moment and try again.",
  503: "The service is temporarily unavailable. Please try again in a moment.",
};

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

export interface Profile {
  id: string;
  user_id: string;
  name: string;
  phone: string;
  address: string;
}

/** Respuesta de `GET /auth/me`: email y rol vienen de User; nombre y contacto, del Profile enlazado. */
export interface Account {
  id: string;
  email: string;
  role: SessionUser["role"];
  is_active: boolean;
  created_at: string;
  profile: Profile;
}

export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: SessionUser;
}

export interface RegisterInput {
  email: string;
  password: string;
  name?: string;
  phone?: string;
  address?: string;
}

export interface ProfileInput {
  name: string;
  phone: string;
  address: string;
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

function fieldErrorsFrom(body: unknown): Record<string, string> {
  const result: Record<string, string> = {};
  const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : undefined;
  if (!Array.isArray(detail)) return result;
  for (const item of detail) {
    if (!item || typeof item !== "object") continue;
    const key = fieldKey((item as { loc?: unknown }).loc);
    if (key && !result[key]) result[key] = plainValidationMessage(item as { type?: unknown; msg?: unknown; ctx?: unknown; loc?: unknown });
  }
  return result;
}

/** Mensaje para el usuario: los 5xx y los cuerpos desconocidos nunca llegan tal cual a la interfaz. */
function errorMessage(body: unknown, status: number): string {
  if (status >= 500) return STATUS_MESSAGES[status] ?? GENERIC_ERROR_MESSAGE;
  const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : undefined;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) return "Some of the information sent is not valid. Please review the form and try again.";
  return STATUS_MESSAGES[status] ?? "The request could not be completed. Please review the information and try again.";
}

async function apiRequest<T>(path: string, options: { method?: "GET" | "POST" | "PUT"; token?: string | null; body?: unknown } = {}): Promise<T> {
  const headers = new Headers({ Accept: "application/json" });
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (options.token) headers.set("Authorization", `Bearer ${options.token}`);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError("We could not reach the TrackFlow service. Check your connection and try again.", 0);
  }

  const responseBody: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && options.token && typeof window !== "undefined") {
      // Token caducado o inválido en una llamada protegida: se borra la sesión y se vuelve a /login.
      clearSession();
      redirectToLogin("expired");
    }
    throw new ApiError(errorMessage(responseBody, response.status), response.status, fieldErrorsFrom(responseBody));
  }
  if (responseBody === null) throw new ApiError(GENERIC_ERROR_MESSAGE, response.status);
  return responseBody as T;
}

/** `POST /users`: crea la cuenta y, en la misma operación, su perfil. */
export function registerUser(payload: RegisterInput): Promise<{ id: string; email: string }> {
  return apiRequest("/users", { method: "POST", body: payload });
}

/** `POST /auth/login`: valida las credenciales y devuelve el token. */
export function loginUser(payload: { email: string; password: string }): Promise<LoginResponse> {
  return apiRequest("/auth/login", { method: "POST", body: payload });
}

/** `GET /auth/me` con el token en la cabecera; un 401 limpia la sesión y redirige a /login. */
export function fetchAccount(token: string | null = getAccessToken()): Promise<Account> {
  return apiRequest("/auth/me", { token });
}

/** `PUT /profiles/me` con el token en la cabecera. */
export function updateMyProfile(payload: Partial<ProfileInput>, token: string | null = getAccessToken()): Promise<Profile> {
  return apiRequest("/profiles/me", { method: "PUT", token, body: payload });
}

/** Usuario que muestra la interfaz a partir de la cuenta validada. */
export function toSessionUser(account: Account): SessionUser {
  return { id: account.id, email: account.email, role: account.role, name: account.profile.name || account.email };
}

/** El navegador no permitió guardar el token (modo privado o datos del sitio bloqueados). */
export class StorageUnavailableError extends Error {
  constructor() {
    super("Browser storage is unavailable.");
    this.name = "StorageUnavailableError";
  }
}

/**
 * Inicia sesión y guarda el token en localStorage.
 * Lanza ApiError si las credenciales no son válidas o la API no responde, y StorageUnavailableError si no se puede guardar.
 */
export async function signIn(email: string, password: string): Promise<LoginResponse> {
  const session = await loginUser({ email, password });
  if (!saveSession(session)) throw new StorageUnavailableError();
  return session;
}

/** Mensaje legible para cualquier error de los flujos de acceso; nunca texto técnico. */
export function authErrorMessage(error: unknown, fallback: string = GENERIC_ERROR_MESSAGE): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof StorageUnavailableError) {
    return "Your browser is blocking site storage, so we could not keep you signed in. Allow site data for this page and try again.";
  }
  return fallback;
}
