"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, authErrorMessage, registerUser, signIn } from "../../../lib/auth";
import { safeNextPath } from "../../../lib/session";

type FieldName = "name" | "email" | "password" | "phone" | "address";
type FieldErrors = Partial<Record<FieldName, string>>;

const FIELDS: FieldName[] = ["email", "password", "name", "phone", "address"];
const EMPTY = { name: "", email: "", password: "", phone: "", address: "" };

/** Revisa en el navegador lo mismo que valida la API, para dar el error junto al campo sin esperar la respuesta. */
function validate(values: Record<FieldName, string>): FieldErrors {
  const problems: FieldErrors = {};
  if (!values.email.trim()) problems.email = "Enter your email address.";
  else if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(values.email.trim())) problems.email = "Enter a valid email address.";
  if (values.password.length < 8) problems.password = "Use at least 8 characters.";
  else if (new TextEncoder().encode(values.password).length > 72) problems.password = "Use at most 72 characters.";
  if (values.name.length > 120) problems.name = "Use at most 120 characters.";
  if (values.phone.length > 40) problems.phone = "Use at most 40 characters.";
  if (values.address.length > 300) problems.address = "Use at most 300 characters.";
  return problems;
}

export default function RegisterPage() {
  const router = useRouter();
  const [values, setValues] = useState(EMPTY);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [banner, setBanner] = useState("");
  const [accountCreated, setAccountCreated] = useState(false);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);

  // Conserva ?next= al saltar entre /register y /login; se lee tras montar para no desajustar la hidratación.
  useEffect(() => {
    setSearch(window.location.search);
  }, []);

  function setValue(field: FieldName, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
    setFieldErrors((current) => ({ ...current, [field]: undefined }));
  }

  function showFieldErrors(errors: FieldErrors) {
    setFieldErrors(errors);
    const first = FIELDS.find((field) => errors[field]);
    if (first) document.getElementById(`register-${first}`)?.focus();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBanner("");
    setAccountCreated(false);

    const problems = validate(values);
    if (Object.keys(problems).length > 0) {
      showFieldErrors(problems);
      return;
    }
    setFieldErrors({});

    setBusy(true);
    let created = false;
    try {
      const email = values.email.trim();
      await registerUser({
        email,
        password: values.password,
        // Los campos de perfil son opcionales: los vacíos no se envían.
        ...(values.name.trim() && { name: values.name.trim() }),
        ...(values.phone.trim() && { phone: values.phone.trim() }),
        ...(values.address.trim() && { address: values.address.trim() }),
      });
      created = true;
      await signIn(email, values.password);
      router.replace(safeNextPath(new URLSearchParams(window.location.search).get("next")));
    } catch (caught) {
      if (created) {
        setAccountCreated(true);
        setBanner("Your account was created, but we could not sign you in automatically.");
      } else if (caught instanceof ApiError && caught.status === 409) {
        showFieldErrors({ email: caught.message });
      } else {
        const fromServer = caught instanceof ApiError ? caught.fieldErrors : {};
        const known = Object.fromEntries(FIELDS.filter((field) => fromServer[field]).map((field) => [field, fromServer[field]])) as FieldErrors;
        if (Object.keys(known).length > 0) showFieldErrors(known);
        else setBanner(authErrorMessage(caught, "We could not create your account. Please try again."));
      }
    } finally {
      setBusy(false);
    }
  }

  const common = (name: FieldName) => ({
    id: `register-${name}`,
    value: values[name],
    "aria-invalid": fieldErrors[name] ? true : undefined,
    onChange: (event: { target: { value: string } }) => setValue(name, event.target.value),
  });
  const error = (name: FieldName) => (fieldErrors[name] ? <strong>{fieldErrors[name]}</strong> : null);

  return (
    <main className="page-shell">
      <section className="page-header">
        <p className="eyebrow">Talent pipeline</p>
        <h1>Create an account</h1>
        <p className="lede">Your email and password are your sign-in. Name, phone and address are optional and can be changed later.</p>
      </section>
      <section className="list-panel auth-panel" aria-labelledby="register-heading">
        <h2 id="register-heading" className="visually-hidden">Registration form</h2>
        {banner ? (
          <p className="error-state auth-notice" role="alert">
            {banner} {accountCreated ? <Link href={`/login${search}`}>Go to sign in</Link> : null}
          </p>
        ) : null}
        <form className="candidate-form auth-form" onSubmit={submit} noValidate aria-busy={busy}>
          <label>
            <span>Email</span>
            <input {...common("email")} type="email" autoComplete="email" />
            {error("email")}
          </label>
          <label>
            <span>Password</span>
            <input {...common("password")} type="password" autoComplete="new-password" />
            {error("password")}
          </label>
          <label>
            <span>Name (optional)</span>
            <input {...common("name")} type="text" autoComplete="name" />
            {error("name")}
          </label>
          <label>
            <span>Phone (optional)</span>
            <input {...common("phone")} type="tel" autoComplete="tel" />
            {error("phone")}
          </label>
          <label>
            <span>Address (optional)</span>
            <input {...common("address")} type="text" autoComplete="street-address" />
            {error("address")}
          </label>
          <button type="submit" disabled={busy}>
            {busy ? "Creating account..." : "Create account"}
          </button>
        </form>
        <p className="auth-links">
          Already registered? <Link href={`/login${search}`}>Sign in</Link>
        </p>
      </section>
    </main>
  );
}
