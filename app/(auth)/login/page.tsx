"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { authErrorMessage, signIn } from "../../../lib/auth";
import { safeNextPath } from "../../../lib/session";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [expired, setExpired] = useState(false);
  const [resetDone, setResetDone] = useState(false);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);

  // Una redirección por 401 trae ?reason=expired: se explica por qué se volvió a pedir el acceso.
  // La query se lee tras montar (no durante el render) para no desajustar la hidratación.
  useEffect(() => {
    setSearch(window.location.search);
    const params = new URLSearchParams(window.location.search);
    setExpired(params.get("reason") === "expired");
    setResetDone(params.get("reset") === "success");
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

  return (
    <main className="page-shell">
      <section className="page-header">
        <p className="eyebrow">Talent pipeline</p>
        <h1>Sign in</h1>
        <p className="lede">Use your TrackFlow account to see the candidate pipeline.</p>
      </section>
      <section className="list-panel auth-panel" aria-labelledby="login-heading">
        <h2 id="login-heading" className="visually-hidden">Sign in form</h2>
        {resetDone && !error ? <p className="success-state auth-notice" role="status">Your password was updated. Sign in with your new password.</p> : null}
        {expired && !resetDone && !error ? <p className="inline-error auth-notice" role="status">Your session has expired. Please sign in again.</p> : null}
        <form className="candidate-form auth-form" onSubmit={submit} aria-busy={busy}>
          <label>
            <span>Email</span>
            <input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
          </label>
          <label>
            <span>Password</span>
            <input type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          <p className="auth-links auth-forgot">
            <Link href="/forgot-password">Forgot your password?</Link>
          </p>
          {error ? <p className="error-state" role="alert">{error}</p> : null}
          <button type="submit" disabled={busy}>
            {busy ? "Signing in..." : "Sign in"}
          </button>
        </form>
        <p className="auth-links">
          New to TrackFlow? <Link href={`/register${search}`}>Create an account</Link>
        </p>
      </section>
    </main>
  );
}
