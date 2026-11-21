"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { clearSession, getCurrentUser, type AuthUser } from "../lib/auth";
import { SESSION_UPDATED_EVENT } from "../lib/session";

/** Barra superior de las vistas protegidas: quién ha iniciado sesión, enlace a su cuenta y cerrar sesión. */
export default function AccountBar() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);

  // El nombre mostrado se actualiza cuando el perfil cambia (evento de sesión) sin recargar la página.
  useEffect(() => {
    const refresh = () => setUser(getCurrentUser());
    refresh();
    window.addEventListener(SESSION_UPDATED_EVENT, refresh);
    return () => window.removeEventListener(SESSION_UPDATED_EVENT, refresh);
  }, []);

  function signOut() {
    clearSession();
    router.replace("/login");
  }

  return (
    <nav className="account-bar" aria-label="Account">
      <Link href="/">Candidates</Link>
      <Link href="/ai-driven-engineering">AI guide</Link>
      <span className="account-bar-spacer" />
      <span className="account-bar-user">{user?.name ?? "Signed-in user"}</span>
      <Link href="/account/profile">My account</Link>
      <button type="button" onClick={signOut}>
        Sign out
      </button>
    </nav>
  );
}
