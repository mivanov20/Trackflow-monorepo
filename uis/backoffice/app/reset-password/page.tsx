"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError } from "../../lib/api-client";
import { authErrorMessage, resetPassword } from "../../lib/auth";

type FieldName = "password" | "confirm";
type FieldErrors = Partial<Record<FieldName, string>>;

const INVALID_LINK = "This reset link is invalid or has expired.";

/** Mismas reglas que la API: 8 caracteres como mínimo y 72 bytes como máximo (límite de bcrypt). */
function passwordProblem(password: string): string | undefined {
  if (password.length < 8) return "Use at least 8 characters.";
  if (new TextEncoder().encode(password).length > 72) return "Use at most 72 characters.";
  return undefined;
}

export default function ResetPasswordPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [banner, setBanner] = useState("");
  const [linkProblem, setLinkProblem] = useState("");
  const [busy, setBusy] = useState(false);

  // El token viaja en la query (?token=...). Se lee tras montar para no desajustar la hidratación.
  useEffect(() => {
    const value = new URLSearchParams(window.location.search).get("token");
    setToken(value && value.trim() ? value.trim() : null);
    setReady(true);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !token) return;
    setBanner("");

    const problems: FieldErrors = {};
    const passwordError = passwordProblem(password);
    if (passwordError) problems.password = passwordError;
    if (confirm !== password) problems.confirm = "The two passwords do not match.";
    setFieldErrors(problems);
    if (problems.password || problems.confirm) {
      document.getElementById(problems.password ? "reset-password" : "reset-confirm")?.focus();
      return;
    }

    setBusy(true);
    try {
      await resetPassword(token, password);
      router.replace("/login?reset=success");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 400) setLinkProblem(caught.message || INVALID_LINK);
      else if (caught instanceof ApiError && caught.fieldErrors.new_password) setFieldErrors({ password: caught.fieldErrors.new_password });
      else setBanner(authErrorMessage(caught, "We could not update your password. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  const showLinkProblem = ready && (!token || linkProblem);

  return (
    <main className="login-page">
      {/* El enlace lleva un secreto: que no viaje en la cabecera Referer a otros sitios. */}
      <meta name="referrer" content="no-referrer" />
      <section className="login-panel" aria-labelledby="reset-title">
        <p className="inventory-eyebrow">TRACKFLOW · OPERATIONS</p>
        <h1 id="reset-title">Choose a new password</h1>
        {!ready && <p className="login-subtitle" role="status">Checking your link…</p>}
        {showLinkProblem && (
          <>
            <p className="inventory-alert auth-notice" role="alert">{linkProblem || INVALID_LINK} Reset links work once and expire after a short time.</p>
            <Link className="inventory-primary-button incident-link-button" href="/forgot-password">Request a new link</Link>
            <p className="auth-links"><Link href="/login">Back to sign in</Link></p>
          </>
        )}
        {ready && token && !linkProblem && (
          <>
            <p className="login-subtitle">Pick a password of at least 8 characters. You will use it to sign in right after.</p>
            {banner && <p className="inventory-alert auth-notice" role="alert">{banner}</p>}
            <form className="incident-form" onSubmit={submit} noValidate aria-busy={busy}>
              <div className="incident-field">
                <label htmlFor="reset-password">New password</label>
                <input
                  id="reset-password"
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  disabled={busy}
                  aria-invalid={fieldErrors.password ? true : undefined}
                  aria-describedby={fieldErrors.password ? "reset-password-error" : undefined}
                  onChange={(event) => { setPassword(event.target.value); setFieldErrors((current) => ({ ...current, password: undefined })); }}
                />
                {fieldErrors.password && <p className="incident-field-error" id="reset-password-error">{fieldErrors.password}</p>}
              </div>
              <div className="incident-field">
                <label htmlFor="reset-confirm">Confirm new password</label>
                <input
                  id="reset-confirm"
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  disabled={busy}
                  aria-invalid={fieldErrors.confirm ? true : undefined}
                  aria-describedby={fieldErrors.confirm ? "reset-confirm-error" : undefined}
                  onChange={(event) => { setConfirm(event.target.value); setFieldErrors((current) => ({ ...current, confirm: undefined })); }}
                />
                {fieldErrors.confirm && <p className="incident-field-error" id="reset-confirm-error">{fieldErrors.confirm}</p>}
              </div>
              <button className="inventory-primary-button" type="submit" disabled={busy}>{busy ? "Updating…" : "Update password"}</button>
            </form>
            <p className="auth-links"><Link href="/login">Back to sign in</Link></p>
          </>
        )}
      </section>
    </main>
  );
}
