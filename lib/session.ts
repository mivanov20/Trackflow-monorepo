/**
 * Sesión del navegador: el token JWT y los datos mínimos del usuario viven en localStorage.
 * Solo se envía a la API de TrackFlow; nunca a la API externa de candidatos.
 */

export const TOKEN_KEY = "trackflow-access-token";
export const USER_KEY = "trackflow-auth-user";
/** Vista principal tras iniciar sesión cuando no hay una página de destino. */
export const DEFAULT_AUTHENTICATED_PATH = "/";
/** Evento que avisa a la interfaz de que los datos del usuario guardados cambiaron. */
export const SESSION_UPDATED_EVENT = "trackflow-session-updated";

const AUTH_PAGES = ["/login", "/register", "/forgot-password", "/reset-password"];

export interface SessionUser {
  id: string;
  name: string;
  email: string;
  role: "admin" | "manager" | "user";
}

/** Lee localStorage sin romper la UI cuando el navegador bloquea el almacenamiento (modo privado, políticas). */
function readStorage(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): boolean {
  try {
    window.localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

/** Token guardado, o null si no hay sesión. */
export function getAccessToken(): string | null {
  return readStorage(TOKEN_KEY);
}

/** Usuario guardado al iniciar sesión; un valor corrupto se descarta junto con la sesión. */
export function getCurrentUser(): SessionUser | null {
  const value = readStorage(USER_KEY);
  if (!value) return null;
  try {
    return JSON.parse(value) as SessionUser;
  } catch {
    clearSession();
    return null;
  }
}

/** Guarda el token y el usuario. Devuelve false si el navegador no permite almacenar (no habría sesión). */
export function saveSession(response: { access_token: string; user: SessionUser }): boolean {
  const stored = writeStorage(TOKEN_KEY, response.access_token) && writeStorage(USER_KEY, JSON.stringify(response.user));
  if (stored) window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
  return stored;
}

/** Actualiza solo los datos del usuario (por ejemplo tras editar el perfil) y avisa a la interfaz. */
export function saveUser(user: SessionUser): void {
  writeStorage(USER_KEY, JSON.stringify(user));
  window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
}

/** Sustituye solo el token (tras cambiar la contraseña la API devuelve uno nuevo). Devuelve false si no se pudo guardar. */
export function replaceToken(token: string): boolean {
  const stored = writeStorage(TOKEN_KEY, token);
  if (stored) window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
  return stored;
}

/** Elimina el token y el usuario guardados. */
export function clearSession(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
  } catch {
    // Sin acceso al almacenamiento no hay sesión que borrar.
  }
  if (typeof window !== "undefined") window.dispatchEvent(new Event(SESSION_UPDATED_EVENT));
}

/**
 * Destino válido tras iniciar sesión. Solo se aceptan rutas relativas del propio sitio:
 * `//evil.com`, `https://evil.com` o `/\evil.com` caen en la vista principal (evita redirecciones abiertas).
 */
export function safeNextPath(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return DEFAULT_AUTHENTICATED_PATH;
  const path = next.split(/[?#]/)[0];
  if (AUTH_PAGES.includes(path)) return DEFAULT_AUTHENTICATED_PATH;
  return next;
}

/** Ruta de `/login` que recuerda la página actual para volver tras autenticarse. */
export function loginUrl(next?: string, reason?: "expired"): string {
  const params = new URLSearchParams();
  if (next) params.set("next", next);
  if (reason) params.set("reason", reason);
  const query = params.toString();
  return query ? `/login?${query}` : "/login";
}

/** Redirección completa a `/login` (la usa el cliente HTTP ante un 401). No hace nada si ya estás en una página de acceso. */
export function redirectToLogin(reason?: "expired"): void {
  if (typeof window === "undefined") return;
  const { pathname, search } = window.location;
  if (AUTH_PAGES.includes(pathname)) return;
  window.location.replace(loginUrl(`${pathname}${search}`, reason));
}
