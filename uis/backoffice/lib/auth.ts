import { apiRequest } from "./api-client";

export interface AuthUser {
  user_uuid: string;
  name: string;
  email: string;
}

interface AuthResponse {
  access_token: string;
  token_type: "bearer";
  user: AuthUser;
}

const TOKEN_KEY = "trackflow-access-token";
const USER_KEY = "trackflow-auth-user";

/** Lee localStorage sin romper la UI cuando el navegador bloquea el almacenamiento (modo privado, políticas). */
function readStorage(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function getAccessToken(): string | null {
  return readStorage(TOKEN_KEY);
}

export function getCurrentUser(): AuthUser | null {
  const value = readStorage(USER_KEY);
  if (!value) return null;
  try {
    return JSON.parse(value) as AuthUser;
  } catch {
    clearSession();
    return null;
  }
}

export function saveSession(response: AuthResponse): void {
  window.localStorage.setItem(TOKEN_KEY, response.access_token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(response.user));
}

export function clearSession(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
  } catch {
    // Sin acceso al almacenamiento no hay sesión que borrar.
  }
}

export async function registerUser(payload: { name: string; email: string; password: string }): Promise<AuthUser> {
  return apiRequest<AuthUser>("/auth/register", { method: "POST", body: payload });
}

export async function loginUser(payload: { email: string; password: string }): Promise<AuthResponse> {
  return apiRequest<AuthResponse>("/auth/login", { method: "POST", body: payload });
}