"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { getAccessToken } from "../../../../lib/auth";
import {
  BRANCHES,
  BRANCH_LABELS,
  CATEGORIES,
  CATEGORY_LABELS,
  ORIGINS,
  ORIGIN_LABELS,
  STATUSES,
  STATUS_LABELS,
  fieldErrorsOf,
  incidentsApi,
  userMessage,
  type IncidentInput,
} from "../../../../lib/incidents";

type FieldName = keyof IncidentInput;
const FIELDS: FieldName[] = ["title", "description", "category", "origin", "branch", "status"];

const EMPTY_FORM: IncidentInput = { title: "", description: "", category: "", origin: "", branch: "", status: "open" };

const REQUIRED_MESSAGES: Record<FieldName, string> = {
  title: "Enter a short title.",
  description: "Describe what happened.",
  category: "Choose a category.",
  origin: "Choose where the report comes from.",
  branch: "Choose the branch.",
  status: "Choose a status.",
};

type FieldErrors = Partial<Record<FieldName, string>>;

/** Checks required fields in the browser before anything is sent. */
function validateClient(values: IncidentInput): FieldErrors {
  const problems: FieldErrors = {};
  for (const field of FIELDS) {
    if (!values[field].trim()) problems[field] = REQUIRED_MESSAGES[field];
  }
  return problems;
}

export default function NewIncidentPage() {
  const [values, setValues] = useState<IncidentInput>(EMPTY_FORM);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [banner, setBanner] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);

  function setValue(field: FieldName, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
    setFieldErrors((current) => ({ ...current, [field]: undefined }));
  }

  async function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (busy) return;
    setBanner("");
    setConfirmation("");

    const problems = validateClient(values);
    setFieldErrors(problems);
    const firstProblem = FIELDS.find((field) => problems[field]);
    if (firstProblem) {
      document.getElementById(`incident-${firstProblem}`)?.focus();
      return;
    }

    const token = getAccessToken();
    if (!token) return;
    setBusy(true);
    try {
      const incident = await incidentsApi.create(token, values);
      setValues(EMPTY_FORM);
      setConfirmation(`Incident #${incident.id} was registered. Thank you for reporting it.`);
    } catch (caught) {
      const serverErrors = fieldErrorsOf(caught);
      const known = FIELDS.filter((field) => serverErrors[field]);
      if (known.length > 0) {
        setFieldErrors(Object.fromEntries(known.map((field) => [field, serverErrors[field]])) as FieldErrors);
        document.getElementById(`incident-${known[0]}`)?.focus();
      } else {
        setBanner(`${userMessage(caught)} Your answers are still here.`);
      }
    } finally {
      setBusy(false);
    }
  }

  function field(name: FieldName, label: string, control: React.ReactNode, extra?: React.ReactNode) {
    return (
      <div className={`incident-field${name === "branch" && values.origin === "branch" ? " incident-field-highlight" : ""}`}>
        <label htmlFor={`incident-${name}`}>{label}</label>
        {control}
        {extra}
        {fieldErrors[name] && <p className="incident-field-error" id={`incident-${name}-error`}>{fieldErrors[name]}</p>}
      </div>
    );
  }

  const common = (name: FieldName) => ({
    id: `incident-${name}`,
    name,
    value: values[name],
    "aria-invalid": fieldErrors[name] ? true : undefined,
    "aria-describedby": fieldErrors[name] ? `incident-${name}-error` : undefined,
  });

  return (
    <>
      <header className="inventory-page-header">
        <div>
          <p className="inventory-eyebrow">OPERATIONS / INCIDENTS</p>
          <h1>Report an incident</h1>
          <p>Log what happened so it can be tracked from one place.</p>
        </div>
        <Link className="inventory-secondary-button incident-link-button" href="/backoffice/incidents">View all incidents →</Link>
      </header>

      {confirmation && <p className="inventory-success incident-confirmation" role="status">{confirmation}</p>}

      <section className="inventory-form-section">
        <form className="incident-form" onSubmit={submit} noValidate aria-busy={busy}>
          {banner && (
            <div className="inventory-alert incident-banner" role="alert">
              <span>{banner}</span>
              <button className="inventory-secondary-button" type="button" onClick={() => submit()} disabled={busy}>Try again</button>
            </div>
          )}

          {field("title", "Title", <input {...common("title")} type="text" maxLength={120} autoComplete="off" onChange={(e) => setValue("title", e.target.value)} />)}
          {field("description", "Description", <textarea {...common("description")} rows={5} maxLength={5000} onChange={(e) => setValue("description", e.target.value)} />)}

          <div className="incident-field-row">
            {field("category", "Category", (
              <select {...common("category")} onChange={(e) => setValue("category", e.target.value)}>
                <option value="">Select a category</option>
                {CATEGORIES.map((value) => <option key={value} value={value}>{CATEGORY_LABELS[value]}</option>)}
              </select>
            ))}
            {field("origin", "Origin", (
              <select {...common("origin")} onChange={(e) => setValue("origin", e.target.value)}>
                <option value="">Select an origin</option>
                {ORIGINS.map((value) => <option key={value} value={value}>{ORIGIN_LABELS[value]}</option>)}
              </select>
            ))}
          </div>

          <div className="incident-field-row">
            {field("branch", "Branch", (
              <select {...common("branch")} onChange={(e) => setValue("branch", e.target.value)}>
                <option value="">Select a branch</option>
                {BRANCHES.map((value) => <option key={value} value={value}>{BRANCH_LABELS[value]}</option>)}
              </select>
            ), values.origin === "branch" && <p className="incident-field-hint">You are reporting from a specific location. Pick the exact branch.</p>)}
            {field("status", "Status", (
              <select {...common("status")} onChange={(e) => setValue("status", e.target.value)}>
                {STATUSES.map((value) => <option key={value} value={value}>{STATUS_LABELS[value]}</option>)}
              </select>
            ))}
          </div>

          <div>
            <button className="inventory-primary-button incident-submit" type="submit" disabled={busy}>
              {busy && <span className="incident-spinner" aria-hidden="true" />}
              {busy ? "Saving…" : "Register incident"}
            </button>
          </div>
        </form>
      </section>
    </>
  );
}
