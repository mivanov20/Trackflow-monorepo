"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";

type FieldName =
  | "client_id"
  | "company_name"
  | "tax_id"
  | "operating_country"
  | "preferred_warehouse"
  | "contact_name"
  | "contact_role"
  | "business_email"
  | "phone_number"
  | "monthly_orders"
  | "avg_weight_kg"
  | "go_live_date"
  | "integration_type"
  | "project_notes";

type FormValues = Record<FieldName, string> & {
  services_needed: string[];
  sla_priority: string;
};

type ValidationErrors = Partial<Record<FieldName | "services_needed" | "sla_priority", string>>;

interface FormFieldProps {
  id: string;
  label: string;
  error?: string;
  children: ReactNode;
}

const initialValues: FormValues = {
  client_id: "",
  company_name: "",
  tax_id: "",
  operating_country: "",
  preferred_warehouse: "",
  contact_name: "",
  contact_role: "",
  business_email: "",
  phone_number: "",
  monthly_orders: "",
  avg_weight_kg: "",
  go_live_date: "",
  integration_type: "",
  project_notes: "",
  services_needed: [],
  sla_priority: "",
};

function FormField({ id, label, error, children }: FormFieldProps) {
  return (
    <div className="form-field">
      <label htmlFor={id}>{label}</label>
      {children}
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>
  );
}

function validateField(name: FieldName, value: string): string {
  const trimmed = value.trim();
  const number = Number(value);
  switch (name) {
    case "client_id":
      if (!trimmed) return "Client ID is required.";
      return /^TF-CL-[0-9]{3,6}$/.test(trimmed) ? "" : "Client ID must match format TF-CL-### (3 to 6 digits).";
    case "company_name":
      if (!trimmed) return "Legal company name is required.";
      return trimmed.length >= 2 ? "" : "Legal company name must be at least 2 characters.";
    case "tax_id":
      if (!trimmed) return "Tax ID / VAT is required.";
      return /^[A-Za-z0-9-]{8,20}$/.test(trimmed) ? "" : "Tax ID / VAT must be 8 to 20 characters using letters, numbers, or dashes.";
    case "operating_country":
      return value ? "" : "Select an operational footprint option.";
    case "preferred_warehouse":
      return value ? "" : "Select a preferred warehouse option.";
    case "contact_name":
      if (!trimmed) return "Primary contact full name is required.";
      return trimmed.length >= 3 ? "" : "Full name must be at least 3 characters.";
    case "contact_role":
      if (!trimmed) return "Contact role is required.";
      return trimmed.length >= 2 ? "" : "Contact role must be at least 2 characters.";
    case "business_email":
      if (!trimmed) return "Business email is required.";
      return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(trimmed) ? "" : "Enter a valid business email address.";
    case "phone_number":
      if (!trimmed) return "Phone number is required.";
      return /^\+?[0-9\s()-]{8,20}$/.test(trimmed) ? "" : "Phone number must contain 8 to 20 valid characters.";
    case "monthly_orders":
      if (!value) return "Monthly order volume is required.";
      if (!Number.isFinite(number)) return "Monthly order volume must be a number.";
      return number >= 50 && number <= 500000 ? "" : "Monthly order volume must be between 50 and 500000.";
    case "avg_weight_kg":
      if (!value) return "Average parcel weight is required.";
      if (!Number.isFinite(number)) return "Average parcel weight must be a number.";
      return number >= 0.1 && number <= 50 ? "" : "Average parcel weight must be between 0.1 and 50 kg.";
    case "go_live_date": {
      if (!value) return "Target go-live date is required.";
      const date = new Date(`${value}T00:00:00`);
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      return date >= today ? "" : "Target go-live date must be today or later.";
    }
    case "integration_type":
      return value ? "" : "Select your current storefront/ERP option.";
    case "project_notes":
      if (!trimmed) return "Project notes are required.";
      return trimmed.length >= 20 ? "" : "Project notes must be at least 20 characters with relevant operational context.";
  }
}

export function ApplicationForm() {
  const [values, setValues] = useState<FormValues>(initialValues);
  const [errors, setErrors] = useState<ValidationErrors>({});
  const [success, setSuccess] = useState("");
  const [language, setLanguage] = useState<"en" | "es">("en");

  useEffect(() => {
    if (window.localStorage.getItem("trackflow-lang") === "es") {
      document.documentElement.lang = "es";
      setLanguage("es");
    }
  }, []);

  function updateField(name: FieldName, value: string) {
    setValues((current) => ({ ...current, [name]: value }));
    setErrors((current) => current[name] ? { ...current, [name]: validateField(name, value) } : current);
    setSuccess("");
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextErrors: ValidationErrors = {};
    (Object.keys(initialValues) as Array<keyof FormValues>).forEach((name) => {
      if (name === "services_needed" || name === "sla_priority") return;
      const error = validateField(name, values[name]);
      if (error) nextErrors[name] = error;
    });
    if (values.services_needed.length === 0) nextErrors.services_needed = "Select at least one required service.";
    if (!values.sla_priority) nextErrors.sla_priority = "Choose an SLA priority option.";
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      document.querySelector<HTMLElement>("[aria-invalid='true']")?.focus();
      return;
    }
    setSuccess("Application validated successfully. Submission simulated. Our onboarding team will contact you within one business day.");
    setValues(initialValues);
  }

  function toggleLanguage() {
    const nextLanguage = language === "en" ? "es" : "en";
    window.localStorage.setItem("trackflow-lang", nextLanguage);
    document.documentElement.lang = nextLanguage;
    setLanguage(nextLanguage);
  }

  const isSpanish = language === "es";
  const inputClass = "form-control";

  return (
    <div className="application-shell">
      <header className="application-header">
        <div className="application-header-inner">
          <Link className="application-brand" href="/" aria-label="Back to TrackFlow home">
            <img src="https://images.unsplash.com/photo-1586528116311-ad8dd3c8310d?auto=format&fit=crop&w=120&q=80" alt="" />
            <strong>TrackFlow</strong>
          </Link>
          <div className="application-header-actions">
            <button className="language-toggle" type="button" onClick={toggleLanguage} aria-label="Toggle website language">{isSpanish ? "EN" : "ES"}</button>
            <Link href="/">{isSpanish ? "Volver al sitio" : "Back to website"}</Link>
          </div>
        </div>
      </header>
      <main className="application-main">
        <section className="application-panel" aria-labelledby="form-title">
          <h1 id="form-title">{isSpanish ? "Solicitud de integracion de marca" : "Brand onboarding application"}</h1>
          <p className="application-intro">{isSpanish ? "Completa este formulario para que TrackFlow evalua tus necesidades de fulfillment y logistica de ultima milla." : "Complete this form so TrackFlow can evaluate your fulfillment and last-mile logistics needs."}</p>
          {success && <p className="form-success" role="status" aria-live="polite">{success}</p>}

          <form className="application-form" noValidate onSubmit={handleSubmit}>
            <p className="required-note">{isSpanish ? "Todos los campos marcados con * son obligatorios." : "All fields marked with * are required."}</p>
            <fieldset className="form-section">
              <legend>{isSpanish ? "Perfil de la empresa" : "Company profile"}</legend>
              <FormField id="client_id" label="Client ID *" error={errors.client_id}>
                <input className={inputClass} id="client_id" value={values.client_id} placeholder="Example: TF-CL-1098" aria-invalid={Boolean(errors.client_id)} onChange={(event) => updateField("client_id", event.target.value)} />
              </FormField>
              <div className="form-grid">
                <FormField id="company_name" label="Legal company name *" error={errors.company_name}>
                  <input className={inputClass} id="company_name" value={values.company_name} placeholder="Example: Atlas Commerce LLC" aria-invalid={Boolean(errors.company_name)} onChange={(event) => updateField("company_name", event.target.value)} />
                </FormField>
                <FormField id="tax_id" label="Tax ID / VAT *" error={errors.tax_id}>
                  <input className={inputClass} id="tax_id" value={values.tax_id} placeholder="Example: TF98A77B2" aria-invalid={Boolean(errors.tax_id)} onChange={(event) => updateField("tax_id", event.target.value)} />
                </FormField>
                <FormField id="operating_country" label="Operational footprint *" error={errors.operating_country}>
                  <select className={inputClass} id="operating_country" value={values.operating_country} aria-invalid={Boolean(errors.operating_country)} onChange={(event) => updateField("operating_country", event.target.value)}>
                    <option value="">Select service scope</option><option value="single-market">Single market</option><option value="multiple-markets">Multiple markets</option><option value="undetermined">Not sure yet</option>
                  </select>
                </FormField>
                <FormField id="preferred_warehouse" label="Preferred warehouse site *" error={errors.preferred_warehouse}>
                  <select className={inputClass} id="preferred_warehouse" value={values.preferred_warehouse} aria-invalid={Boolean(errors.preferred_warehouse)} onChange={(event) => updateField("preferred_warehouse", event.target.value)}>
                    <option value="">Choose warehouse</option><option value="Warehouse 1">Warehouse 1</option><option value="Warehouse 2">Warehouse 2</option><option value="Multiple">Multiple sites</option>
                  </select>
                </FormField>
              </div>
            </fieldset>

            <fieldset className="form-section">
              <legend>{isSpanish ? "Contacto principal" : "Primary contact"}</legend>
              <div className="form-grid">
                <FormField id="contact_name" label="Full name *" error={errors.contact_name}>
                  <input className={inputClass} id="contact_name" value={values.contact_name} placeholder="Example: Laura Nguyen" aria-invalid={Boolean(errors.contact_name)} onChange={(event) => updateField("contact_name", event.target.value)} />
                </FormField>
                <FormField id="contact_role" label="Role *" error={errors.contact_role}>
                  <input className={inputClass} id="contact_role" value={values.contact_role} placeholder="Example: Ecommerce Director" aria-invalid={Boolean(errors.contact_role)} onChange={(event) => updateField("contact_role", event.target.value)} />
                </FormField>
                <FormField id="business_email" label="Business email *" error={errors.business_email}>
                  <input className={inputClass} id="business_email" type="email" value={values.business_email} placeholder="name@company.com" aria-invalid={Boolean(errors.business_email)} onChange={(event) => updateField("business_email", event.target.value)} />
                </FormField>
                <FormField id="phone_number" label="Phone number *" error={errors.phone_number}>
                  <input className={inputClass} id="phone_number" type="tel" value={values.phone_number} placeholder="Enter your phone number" aria-invalid={Boolean(errors.phone_number)} onChange={(event) => updateField("phone_number", event.target.value)} />
                </FormField>
              </div>
            </fieldset>

            <fieldset className="form-section">
              <legend>{isSpanish ? "Requisitos operativos" : "Operational requirements"}</legend>
              <div className="form-grid">
                <FormField id="monthly_orders" label="Monthly order volume *" error={errors.monthly_orders}>
                  <input className={inputClass} id="monthly_orders" type="number" min="50" max="500000" value={values.monthly_orders} placeholder="Example: 22000" aria-invalid={Boolean(errors.monthly_orders)} onChange={(event) => updateField("monthly_orders", event.target.value)} />
                </FormField>
                <FormField id="avg_weight_kg" label="Average parcel weight (kg) *" error={errors.avg_weight_kg}>
                  <input className={inputClass} id="avg_weight_kg" type="number" min="0.1" max="50" step="0.1" value={values.avg_weight_kg} placeholder="Example: 1.8" aria-invalid={Boolean(errors.avg_weight_kg)} onChange={(event) => updateField("avg_weight_kg", event.target.value)} />
                </FormField>
                <FormField id="go_live_date" label="Target go-live date *" error={errors.go_live_date}>
                  <input className={inputClass} id="go_live_date" type="date" value={values.go_live_date} aria-invalid={Boolean(errors.go_live_date)} onChange={(event) => updateField("go_live_date", event.target.value)} />
                </FormField>
                <FormField id="integration_type" label="Current storefront/ERP *" error={errors.integration_type}>
                  <select className={inputClass} id="integration_type" value={values.integration_type} aria-invalid={Boolean(errors.integration_type)} onChange={(event) => updateField("integration_type", event.target.value)}>
                    <option value="">Select integration source</option><option value="Shopify">Shopify</option><option value="WooCommerce">WooCommerce</option><option value="Custom API">Custom API</option><option value="Legacy ERP CSV">Legacy ERP CSV</option>
                  </select>
                </FormField>
              </div>
              <fieldset className="choice-group" aria-describedby="services_needed_error">
                <legend>Services needed *</legend>
                <div className="choice-grid">
                  {[["inventory_storage", "Inventory storage"], ["pick_pack", "Pick and pack"], ["last_mile", "Last-mile delivery"], ["reverse_logistics", "Reverse logistics"]].map(([value, label]) => (
                    <label className="choice-label" key={value}><input type="checkbox" checked={values.services_needed.includes(value)} onChange={(event) => {
                      const selected = event.target.checked ? [...values.services_needed, value] : values.services_needed.filter((item) => item !== value);
                      setValues((current) => ({ ...current, services_needed: selected }));
                      setErrors((current) => ({ ...current, services_needed: selected.length ? "" : current.services_needed }));
                    }} />{label}</label>
                  ))}
                </div>
                {errors.services_needed && <p className="form-error" id="services_needed_error" role="alert">{errors.services_needed}</p>}
              </fieldset>
              <fieldset className="choice-group" aria-describedby="sla_priority_error">
                <legend>SLA priority *</legend>
                <div className="choice-row">
                  {["Standard", "Urgent rollout"].map((label) => <label className="choice-label" key={label}><input type="radio" name="sla_priority" value={label} checked={values.sla_priority === label} onChange={(event) => {
                    setValues((current) => ({ ...current, sla_priority: event.target.value }));
                    setErrors((current) => ({ ...current, sla_priority: "" }));
                  }} />{label}</label>)}
                </div>
                {errors.sla_priority && <p className="form-error" id="sla_priority_error" role="alert">{errors.sla_priority}</p>}
              </fieldset>
              <FormField id="project_notes" label="Project notes *" error={errors.project_notes}>
                <textarea className={inputClass} id="project_notes" rows={5} value={values.project_notes} placeholder="Share product types, return rate, regions, and launch constraints." aria-invalid={Boolean(errors.project_notes)} onChange={(event) => updateField("project_notes", event.target.value)} />
              </FormField>
            </fieldset>
            <div className="form-actions">
              <button className="form-submit" type="submit">{isSpanish ? "Enviar solicitud" : "Submit application"}</button>
              <button className="form-clear" type="button" onClick={() => { setValues(initialValues); setErrors({}); setSuccess(""); }}>{isSpanish ? "Limpiar formulario" : "Clear form"}</button>
            </div>
          </form>
        </section>
      </main>
      <footer className="application-footer">Contact: growth@trackflow.example</footer>
    </div>
  );
}