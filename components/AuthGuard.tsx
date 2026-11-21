"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ApiError, fetchAccount, getAccessToken, saveUser, toSessionUser } from "../lib/auth";
import { TOKEN_KEY, loginUrl } from "../lib/session";

type GuardState = "checking" | "authorized" | "unreachable";

/**
 * Protege una vista: sin token en localStorage redirige a /login, y con token lo valida contra
 * `GET /auth/me` antes de mostrar nada. Un token caducado o inválido (401) lo gestiona el cliente HTTP
 * (borra la sesión y redirige); si la API no responde se ofrece reintentar sin cerrar la sesión.
 */
export default function AuthGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [state, setState] = useState<GuardState>("checking");
  const [attempt, setAttempt] = useState(0);

  const goToLogin = useCallback(() => {
    const search = typeof window === "undefined" ? "" : window.location.search;
    router.replace(loginUrl(`${pathname}${search}`));
  }, [pathname, router]);

  // Sin token no hay vista: se comprueba en cada cambio de ruta dentro del layout.
  useEffect(() => {
    if (!getAccessToken()) goToLogin();
  }, [goToLogin]);

  // Si el token se borra en otra pestaña (cerrar sesión allí), esta también vuelve al login.
  useEffect(() => {
    function onStorage(event: StorageEvent) {
      if (event.key === TOKEN_KEY && !event.newValue) goToLogin();
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [goToLogin]);

  // El token se valida una vez al entrar (y al reintentar), no en cada navegación interna.
  useEffect(() => {
    const token = getAccessToken();
    if (!token) return;
    let active = true;
    setState("checking");
    fetchAccount(token)
      .then((account) => {
        if (!active) return;
        saveUser(toSessionUser(account));
        setState("authorized");
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (caught instanceof ApiError && caught.status === 401) return; // el cliente HTTP ya limpió la sesión y redirige
        setState("unreachable");
      });
    return () => {
      active = false;
    };
  }, [attempt]);

  if (state === "unreachable") {
    return (
      <main className="page-shell">
        <section className="list-panel" role="alert">
          <p className="error-state">We could not verify your session because the service did not answer. Your sign-in has not been changed.</p>
          <div className="management-bar">
            <button type="button" onClick={() => setAttempt((current) => current + 1)}>
              Try again
            </button>
            <Link className="button-link" href="/login">
              Back to sign in
            </Link>
          </div>
        </section>
      </main>
    );
  }
  if (state !== "authorized") {
    return (
      <main className="page-shell">
        <p className="empty-state" role="status">
          Checking your session...
        </p>
      </main>
    );
  }
  return <>{children}</>;
}
