"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { ApiError, authErrorMessage, requestPasswordReset } from "../../../lib/auth";

/** Mismo texto para cualquier dirección: la página no puede revelar qué correos están registrados. */
const CONFIRMATION = "If that address is registered, you will receive a reset link shortly. Check your inbox and your spam folder.";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [fieldError, setFieldError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || submitted) return;
    setError("");
    const address = email.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(address)) {
      setFieldError("Enter a valid email address.");
      document.getElementById("forgot-email")?.focus();
      return;
    }
    setFieldError("");

    setBusy(true);
    try {
      await requestPasswordReset(address);
      setSubmitted(true); // el formulario queda desactivado para no repetir la petición
    } catch (caught) {
      if (caught instanceof ApiError && caught.fieldErrors.email) setFieldError(caught.fieldErrors.email);
      else setError(authErrorMessage(caught, "We could not send the request. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="page-shell">
      <section className="page-header">
        <p className="eyebrow">Talent pipeline</p>
        <h1>Forgot your password?</h1>
        <p className="lede">Enter the email of your account and we will send you a link to choose a new password.</p>
      </section>
      <section className="list-panel auth-panel" aria-labelledby="forgot-heading">
        <h2 id="forgot-heading" className="visually-hidden">Password reset request</h2>
        {submitted ? <p className="success-state auth-notice" role="status">{CONFIRMATION}</p> : null}
        <form className="candidate-form auth-form" onSubmit={submit} noValidate aria-busy={busy}>
          <label>
            <span>Email</span>
            <input
              id="forgot-email"
              type="email"
              autoComplete="email"
              value={email}
              disabled={busy || submitted}
              aria-invalid={fieldError ? true : undefined}
              onChange={(event) => {
                setEmail(event.target.value);
                setFieldError("");
              }}
            />
            {fieldError ? <strong>{fieldError}</strong> : null}
          </label>
          {error ? <p className="error-state" role="alert">{error}</p> : null}
          <button type="submit" disabled={busy || submitted}>
            {busy ? "Sending..." : submitted ? "Link requested" : "Send reset link"}
          </button>
        </form>
        <p className="auth-links">
          <Link href="/login">Back to sign in</Link>
        </p>
      </section>
    </main>
  );
}
