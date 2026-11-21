import { ApiError, GENERIC_ERROR_MESSAGE, apiRequest } from "./api-client";
import { getAccessToken, saveSession, type SessionUser } from "./session";

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
  return apiRequest<{ id: string; email: string }>("/users", { method: "POST", body: payload });
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
