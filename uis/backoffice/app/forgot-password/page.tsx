"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { ApiError } from "../../lib/api-client";
import { authErrorMessage, requestPasswordReset } from "../../lib/auth";

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
    <main className="login-page">
      <section className="login-panel" aria-labelledby="forgot-title">
        <p className="inventory-eyebrow">TRACKFLOW · OPERATIONS</p>
        <h1 id="forgot-title">Forgot your password?</h1>
        <p className="login-subtitle">Enter the email of your account and we will send you a link to choose a new password.</p>
        {submitted && <p className="inventory-success auth-notice" role="status">{CONFIRMATION}</p>}
        <form className="incident-form" onSubmit={submit} noValidate aria-busy={busy}>
          <div className="incident-field">
            <label htmlFor="forgot-email">Email</label>
            <input
              id="forgot-email"
              type="email"
              autoComplete="email"
              value={email}
              disabled={busy || submitted}
              aria-invalid={fieldError ? true : undefined}
              aria-describedby={fieldError ? "forgot-email-error" : undefined}
              onChange={(event) => { setEmail(event.target.value); setFieldError(""); }}
            />
            {fieldError && <p className="incident-field-error" id="forgot-email-error">{fieldError}</p>}
          </div>
          {error && <p className="inventory-alert" role="alert">{error}</p>}
          <button className="inventory-primary-button" type="submit" disabled={busy || submitted}>
            {busy ? "Sending…" : submitted ? "Link requested" : "Send reset link"}
          </button>
        </form>
        <p className="auth-links"><Link href="/login">Back to sign in</Link></p>
      </section>
    </main>
  );
}
