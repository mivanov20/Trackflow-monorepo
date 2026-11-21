"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { authErrorMessage, signIn } from "../../lib/auth";
import { safeNextPath } from "../../lib/session";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [expired, setExpired] = useState(false);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);

  // Una redirección por 401 trae ?reason=expired: se explica por qué se volvió a pedir el acceso.
  // La query se lee tras montar (no durante el render) para no desajustar la hidratación.
  useEffect(() => {
    setSearch(window.location.search);
    setExpired(new URLSearchParams(window.location.search).get("reason") === "expired");
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await signIn(email.trim(), password);
      router.replace(safeNextPath(new URLSearchParams(window.location.search).get("next")));
    } catch (caught) {
      setError(authErrorMessage(caught, "Unable to sign in. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  const registerHref = `/register${search}`;

  return (
    <main className="login-page">
      <section className="login-panel" aria-labelledby="login-title">
        <p className="inventory-eyebrow">TRACKFLOW · OPERATIONS</p>
        <h1 id="login-title">Sign in</h1>
        <p className="login-subtitle">Use your TrackFlow account to reach warehouse inventory and incident records.</p>
        {expired && !error && <p className="inventory-warning auth-notice" role="status">Your session has expired. Please sign in again.</p>}
        <form className="incident-form" onSubmit={submit} aria-busy={busy}>
          <div className="incident-field">
            <label htmlFor="login-email">Email</label>
            <input id="login-email" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
          </div>
          <div className="incident-field">
            <label htmlFor="login-password">Password</label>
            <input id="login-password" type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
          </div>
          {error && <p className="inventory-alert" role="alert">{error}</p>}
          <button className="inventory-primary-button" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
        <p className="auth-links">New to TrackFlow? <Link href={registerHref}>Create an account</Link></p>
      </section>
    </main>
  );
}
