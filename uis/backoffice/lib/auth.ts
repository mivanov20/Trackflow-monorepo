import { ApiError, GENERIC_ERROR_MESSAGE, apiRequest } from "./api-client";
import { getAccessToken, replaceToken, saveSession, type SessionUser } from "./session";
import { actorRole } from "./telemetry-domain";
import { newRequestId, track, withRequestId } from "./telemetry";

export { clearSession, getAccessToken, getCurrentUser, saveSession, saveUser } from "./session";
export type { SessionUser as AuthUser } from "./session";

export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: SessionUser;
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

/** `POST /users`: crea la cuenta y, en la misma operación, su perfil. */
export async function registerUser(payload: RegisterInput): Promise<{ id: string; email: string }> {
  const requestId = newRequestId();
  const created = await apiRequest<{ id: string; email: string }>("/users", { method: "POST", body: payload, requestId });
  // Only whether the optional fields were filled in: their content is never recorded.
  withRequestId(requestId, () =>
    track("user_registered", {
      initial_role: "user",
      has_name: Boolean(payload.name?.trim()),
      has_phone: Boolean(payload.phone?.trim()),
      has_address: Boolean(payload.address?.trim()),
      registration_channel: "users_endpoint",
    }),
  );
  return created;
}

/** `POST /auth/login`: valida las credenciales y devuelve el token. */
export async function loginUser(payload: { email: string; password: string }): Promise<LoginResponse> {
  return apiRequest<LoginResponse>("/auth/login", { method: "POST", body: payload });
}

/** `GET /auth/me` con el token en la cabecera; un 401 limpia la sesión y redirige a /login. */
export async function fetchAccount(token: string | null = getAccessToken()): Promise<Account> {
  return apiRequest<Account>("/auth/me", { token });
}

/** `PUT /profiles/me` con el token en la cabecera. */
export async function updateMyProfile(payload: Partial<ProfileInput>, token: string | null = getAccessToken()): Promise<Profile> {
  return apiRequest<Profile>("/profiles/me", { method: "PUT", token, body: payload });
}

export interface MessageResponse {
  message: string;
}

/** `POST /auth/forgot-password`: la API responde 200 con el mismo mensaje exista o no el email. */
export async function requestPasswordReset(email: string): Promise<MessageResponse> {
  return apiRequest<MessageResponse>("/auth/forgot-password", { method: "POST", body: { email } });
}

/** `POST /auth/reset-password`: 400 si el enlace es inválido, caducó o ya se usó. */
export async function resetPassword(token: string, newPassword: string): Promise<MessageResponse> {
  return apiRequest<MessageResponse>("/auth/reset-password", { method: "POST", body: { token, new_password: newPassword } });
}

/**
 * `POST /auth/change-password` con el token en la cabecera. Las sesiones anteriores caducan, así que
 * la respuesta trae un token nuevo que sustituye al guardado para que esta sesión continúe.
 */
export async function changePassword(currentPassword: string, newPassword: string): Promise<MessageResponse> {
  const requestId = newRequestId();
  const response = await apiRequest<MessageResponse & { access_token: string }>("/auth/change-password", {
    method: "POST",
    token: getAccessToken(),
    body: { current_password: currentPassword, new_password: newPassword },
    requestId,
  });
  replaceToken(response.access_token);
  withRequestId(requestId, () => track("password_changed", { user_role: actorRole(), sessions_revoked: true }));
  return { message: response.message };
}

/** Guarda en localStorage el usuario que muestra la interfaz a partir de la cuenta validada. */
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
  try {
    const session = await loginUser({ email, password });
    if (!saveSession(session)) throw new StorageUnavailableError();
    // Recorded after the session is saved so the event carries its sessionId and userId. The role is a closed value.
    track("login_succeeded", { user_role: session.user.role, method: "password", failures_before_success: failedSignIns });
    failedSignIns = 0;
    return session;
  } catch (caught) {
    failedSignIns += 1;
    // Only the failure class and a counter are recorded: never the email or the password that was typed.
    track("login_attempt_failed", { failure_reason: signInFailureReason(caught), attempt_number: Math.min(failedSignIns, 50) });
    throw caught;
  }
}

/** Consecutive failed sign-ins in this page visit; reset by a success. */
let failedSignIns = 0;

/** What the browser can tell about a failed sign-in from the way it failed. */
function signInFailureReason(error: unknown): "invalid_credentials" | "network_error" | "rate_limited" | "service_unavailable" | "storage_unavailable" {
  if (error instanceof StorageUnavailableError) return "storage_unavailable";
  if (error instanceof ApiError) {
    if (error.status === 0) return "network_error";
    if (error.status === 429) return "rate_limited";
    if (error.status >= 500) return "service_unavailable";
  }
  return "invalid_credentials";
}

/** Mensaje legible para cualquier error de los flujos de acceso; nunca texto técnico. */
export function authErrorMessage(error: unknown, fallback: string = GENERIC_ERROR_MESSAGE): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof StorageUnavailableError) {
    return "Your browser is blocking site storage, so we could not keep you signed in. Allow site data for this page and try again.";
  }
  return fallback;
}
