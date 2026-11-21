"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { ApiError } from "../../../lib/api-client";
import { authErrorMessage, changePassword } from "../../../lib/auth";

type FieldName = "current" | "password" | "confirm";
type FieldErrors = Partial<Record<FieldName, string>>;

const IDS: Record<FieldName, string> = { current: "change-current", password: "change-password", confirm: "change-confirm" };

/** Mismas reglas que la API: 8 caracteres como mínimo y 72 bytes como máximo (límite de bcrypt). */
function passwordProblem(password: string): string | undefined {
  if (password.length < 8) return "Use at least 8 characters.";
  if (new TextEncoder().encode(password).length > 72) return "Use at most 72 characters.";
  return undefined;
}

export default function ChangePasswordPage() {
  const [values, setValues] = useState({ current: "", password: "", confirm: "" });
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [banner, setBanner] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  function setValue(field: FieldName, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
    setFieldErrors((current) => ({ ...current, [field]: undefined }));
    setNotice("");
  }

  function showFieldErrors(errors: FieldErrors) {
    setFieldErrors(errors);
    const first = (["current", "password", "confirm"] as FieldName[]).find((field) => errors[field]);
    if (first) document.getElementById(IDS[first])?.focus();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBanner("");
    setNotice("");

    const problems: FieldErrors = {};
    if (!values.current) problems.current = "Enter your current password.";
    const passwordError = passwordProblem(values.password);
    if (passwordError) problems.password = passwordError;
    else if (values.password === values.current) problems.password = "Choose a password different from the current one.";
    if (values.confirm !== values.password) problems.confirm = "The two passwords do not match.";
    if (Object.keys(problems).length > 0) {
      showFieldErrors(problems);
      return;
    }
    setFieldErrors({});

    setBusy(true);
    try {
      await changePassword(values.current, values.password);
      setValues({ current: "", password: "", confirm: "" });
      setNotice("Your password was changed. Use the new one the next time you sign in.");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 400) {
        // 400: la contraseña actual no es correcta, o la nueva es igual a la actual.
        showFieldErrors(caught.message.toLowerCase().includes("current password is incorrect") ? { current: caught.message } : { password: caught.message });
      } else if (caught instanceof ApiError && caught.fieldErrors.new_password) {
        showFieldErrors({ password: caught.fieldErrors.new_password });
      } else {
        setBanner(`Your password was not changed. ${authErrorMessage(caught)} Your answers are still here.`);
      }
    } finally {
      setBusy(false);
    }
  }

  const field = (name: FieldName, label: string, autoComplete: string) => (
    <div className="incident-field">
      <label htmlFor={IDS[name]}>{label}</label>
      <input
        id={IDS[name]}
        type="password"
        autoComplete={autoComplete}
        value={values[name]}
        disabled={busy}
        aria-invalid={fieldErrors[name] ? true : undefined}
        aria-describedby={fieldErrors[name] ? `${IDS[name]}-error` : undefined}
        onChange={(event) => setValue(name, event.target.value)}
      />
      {fieldErrors[name] && <p className="incident-field-error" id={`${IDS[name]}-error`}>{fieldErrors[name]}</p>}
    </div>
  );

  return (
    <>
      <header className="inventory-page-header">
        <div>
          <p className="inventory-eyebrow">ACCOUNT / SECURITY</p>
          <h1>Change password</h1>
          <p>Confirm your current password, then choose a new one.</p>
        </div>
        <Link className="inventory-secondary-button incident-link-button" href="/account/profile">Back to my account</Link>
      </header>

      <section className="inventory-form-section" aria-labelledby="change-heading">
        <h2 id="change-heading" className="visually-hidden">Change password form</h2>
        {notice && <p className="inventory-success auth-notice" role="status">{notice}</p>}
        {banner && <p className="inventory-alert auth-notice" role="alert">{banner}</p>}
        <form className="incident-form" onSubmit={submit} noValidate aria-busy={busy}>
          {field("current", "Current password", "current-password")}
          {field("password", "New password", "new-password")}
          {field("confirm", "Confirm new password", "new-password")}
          <div>
            <button className="inventory-primary-button" type="submit" disabled={busy}>{busy ? "Saving…" : "Change password"}</button>
          </div>
        </form>
      </section>
    </>
  );
}
