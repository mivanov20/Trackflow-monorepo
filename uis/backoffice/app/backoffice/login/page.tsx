"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError } from "../../../lib/api-client";
import { loginUser, registerUser, saveSession } from "../../../lib/auth";

export default function BackofficeLoginPage() {
  const router = useRouter();
  const [isRegistering, setIsRegistering] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (isRegistering) await registerUser({ name, email, password });
      const session = await loginUser({ email, password });
      saveSession(session);
      const next = new URLSearchParams(window.location.search).get("next");
      const allowed = next?.startsWith("/backoffice/inventory/") || next?.startsWith("/backoffice/incidents");
      router.replace(next && allowed ? next : "/backoffice/inventory/products");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to sign in. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-panel" aria-labelledby="login-title">
        <p className="inventory-eyebrow">TRACKFLOW · OPERATIONS</p>
        <h1 id="login-title">{isRegistering ? "Create an operative account" : "Sign in"}</h1>
        <p className="login-subtitle">Access warehouse inventory and stock movement records.</p>
        <form onSubmit={submit}>
          {isRegistering && <label>Name<input autoComplete="name" required value={name} onChange={(event) => setName(event.target.value)} /></label>}
          <label>Email<input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
          <label>Password<input type="password" autoComplete={isRegistering ? "new-password" : "current-password"} minLength={isRegistering ? 8 : 1} required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          {error && <p className="inventory-alert" role="alert">{error}</p>}
          <button className="inventory-primary-button" type="submit" disabled={busy}>{busy ? "Please wait…" : isRegistering ? "Register and sign in" : "Sign in"}</button>
        </form>
        <button className="login-mode-button" type="button" onClick={() => { setError(""); setIsRegistering((value) => !value); }}>
          {isRegistering ? "Already registered? Sign in" : "Need a test account? Register"}
        </button>
      </section>
    </main>
  );
}