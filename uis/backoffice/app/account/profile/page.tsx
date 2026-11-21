"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { ApiError } from "../../../lib/api-client";
import { authErrorMessage, fetchAccount, saveUser, toSessionUser, updateMyProfile, type Account } from "../../../lib/auth";

type LoadState = "loading" | "error" | "ready";
type FieldName = "name" | "phone" | "address";
type FieldErrors = Partial<Record<FieldName, string>>;

const FIELDS: FieldName[] = ["name", "phone", "address"];

function validate(values: Record<FieldName, string>): FieldErrors {
  const problems: FieldErrors = {};
  if (!values.name.trim()) problems.name = "Enter your name.";
  else if (values.name.length > 120) problems.name = "Use at most 120 characters.";
  if (values.phone.length > 40) problems.phone = "Use at most 40 characters.";
  if (values.address.length > 300) problems.address = "Use at most 300 characters.";
  return problems;
}

export default function ProfilePage() {
  const [state, setState] = useState<LoadState>("loading");
  const [loadError, setLoadError] = useState("");
  const [account, setAccount] = useState<Account | null>(null);
  const [values, setValues] = useState({ name: "", phone: "", address: "" });
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [banner, setBanner] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setState("loading");
    setLoadError("");
    try {
      const loaded = await fetchAccount();
      setAccount(loaded);
      setValues({ name: loaded.profile.name, phone: loaded.profile.phone, address: loaded.profile.address });
      saveUser(toSessionUser(loaded));
      setState("ready");
    } catch (caught) {
      setLoadError(`Your account could not be loaded. ${authErrorMessage(caught)}`);
      setState("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function setValue(field: FieldName, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
    setFieldErrors((current) => ({ ...current, [field]: undefined }));
    setNotice("");
  }

  function showFieldErrors(errors: FieldErrors) {
    setFieldErrors(errors);
    const first = FIELDS.find((field) => errors[field]);
    if (first) document.getElementById(`profile-${first}`)?.focus();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || !account) return;
    setBanner("");
    setNotice("");

    const problems = validate(values);
    if (Object.keys(problems).length > 0) {
      showFieldErrors(problems);
      return;
    }
    setFieldErrors({});

    setSaving(true);
    try {
      const profile = await updateMyProfile({ name: values.name.trim(), phone: values.phone.trim(), address: values.address.trim() });
      const updated = { ...account, profile };
      setAccount(updated);
      setValues({ name: profile.name, phone: profile.phone, address: profile.address });
      saveUser(toSessionUser(updated));
      setNotice("Your profile was updated.");
    } catch (caught) {
      const fromServer = caught instanceof ApiError ? caught.fieldErrors : {};
      const known = Object.fromEntries(FIELDS.filter((field) => fromServer[field]).map((field) => [field, fromServer[field]])) as FieldErrors;
      if (Object.keys(known).length > 0) showFieldErrors(known);
      else setBanner(`Your profile was not saved. ${authErrorMessage(caught)} Your changes are still here.`);
    } finally {
      setSaving(false);
    }
  }

  const common = (name: FieldName) => ({
    id: `profile-${name}`,
    value: values[name],
    "aria-invalid": fieldErrors[name] ? true : undefined,
    "aria-describedby": fieldErrors[name] ? `profile-${name}-error` : undefined,
    disabled: saving,
    onChange: (event: { target: { value: string } }) => setValue(name, event.target.value),
  });
  const error = (name: FieldName) => fieldErrors[name] && <p className="incident-field-error" id={`profile-${name}-error`}>{fieldErrors[name]}</p>;

  return (
    <>
      <header className="inventory-page-header">
        <div>
          <p className="inventory-eyebrow">ACCOUNT / PROFILE</p>
          <h1>My account</h1>
          <p>Your sign-in email comes from your account. Name and contact details come from your profile.</p>
        </div>
      </header>

      {state === "loading" && <p className="incident-state" role="status"><span className="incident-spinner incident-spinner-dark" aria-hidden="true" /> Loading your account…</p>}
      {state === "error" && (
        <p className="incident-state incident-state-error" role="alert">
          <span>{loadError}</span>
          <button className="inventory-secondary-button" type="button" onClick={() => void load()}>Try again</button>
        </p>
      )}

      {state === "ready" && account && (
        <section className="inventory-form-section" aria-labelledby="profile-heading">
          <h2 id="profile-heading" className="visually-hidden">Profile details</h2>
          <dl className="profile-summary">
            <dt>Email</dt>
            <dd>{account.email}</dd>
            <dt>Role</dt>
            <dd>{account.role}</dd>
          </dl>
          {notice && <p className="inventory-success auth-notice" role="status">{notice}</p>}
          {banner && <p className="inventory-alert auth-notice" role="alert">{banner}</p>}
          <form className="incident-form" onSubmit={submit} noValidate aria-busy={saving}>
            <div className="incident-field">
              <label htmlFor="profile-name">Name</label>
              <input {...common("name")} type="text" autoComplete="name" />
              {error("name")}
            </div>
            <div className="incident-field">
              <label htmlFor="profile-phone">Phone</label>
              <input {...common("phone")} type="tel" autoComplete="tel" />
              {error("phone")}
            </div>
            <div className="incident-field">
              <label htmlFor="profile-address">Address</label>
              <input {...common("address")} type="text" autoComplete="street-address" />
              {error("address")}
            </div>
            <div>
              <button className="inventory-primary-button" type="submit" disabled={saving}>{saving ? "Saving…" : "Save changes"}</button>
            </div>
          </form>
        </section>
      )}
    </>
  );
}
