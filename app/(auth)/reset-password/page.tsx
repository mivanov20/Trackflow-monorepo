"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, authErrorMessage, resetPassword } from "../../../lib/auth";

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
    <main className="page-shell">
      {/* El enlace lleva un secreto: que no viaje en la cabecera Referer a otros sitios. */}
      <meta name="referrer" content="no-referrer" />
      <section className="page-header">
        <p className="eyebrow">Talent pipeline</p>
        <h1>Choose a new password</h1>
        {!ready ? <p className="lede" role="status">Checking your link...</p> : null}
      </section>
      <section className="list-panel auth-panel" aria-labelledby="reset-heading">
        <h2 id="reset-heading" className="visually-hidden">Reset password</h2>
        {showLinkProblem ? (
          <>
            <p className="error-state" role="alert">
              {linkProblem || INVALID_LINK} Reset links work once and expire after a short time.
            </p>
            <div className="management-bar">
              <Link className="button-link" href="/forgot-password">
                Request a new link
              </Link>
              <Link className="button-link" href="/login">
                Back to sign in
              </Link>
            </div>
          </>
        ) : null}
        {ready && token && !linkProblem ? (
          <>
            {banner ? <p className="error-state auth-notice" role="alert">{banner}</p> : null}
            <form className="candidate-form auth-form" onSubmit={submit} noValidate aria-busy={busy}>
              <label>
                <span>New password</span>
                <input
                  id="reset-password"
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  disabled={busy}
                  aria-invalid={fieldErrors.password ? true : undefined}
                  onChange={(event) => {
                    setPassword(event.target.value);
                    setFieldErrors((current) => ({ ...current, password: undefined }));
                  }}
                />
                {fieldErrors.password ? <strong>{fieldErrors.password}</strong> : null}
              </label>
              <label>
                <span>Confirm new password</span>
                <input
                  id="reset-confirm"
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  disabled={busy}
                  aria-invalid={fieldErrors.confirm ? true : undefined}
                  onChange={(event) => {
                    setConfirm(event.target.value);
                    setFieldErrors((current) => ({ ...current, confirm: undefined }));
                  }}
                />
                {fieldErrors.confirm ? <strong>{fieldErrors.confirm}</strong> : null}
              </label>
              <button type="submit" disabled={busy}>
                {busy ? "Updating..." : "Update password"}
              </button>
            </form>
            <p className="auth-links">
              <Link href="/login">Back to sign in</Link>
            </p>
          </>
        ) : null}
      </section>
    </main>
  );
}
