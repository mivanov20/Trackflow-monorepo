import { apiRequest } from "./api-client";

export interface AuthUser {
  id: string;
  name: string;
  email: string;
  role: "admin" | "manager" | "user";
}

interface AuthResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
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

export async function registerUser(payload: { name: string; email: string; password: string }): Promise<{ id: string; email: string }> {
  return apiRequest<{ id: string; email: string }>("/users", { method: "POST", body: payload });
}

export async function loginUser(payload: { email: string; password: string }): Promise<AuthResponse> {
  return apiRequest<AuthResponse>("/auth/login", { method: "POST", body: payload });
}