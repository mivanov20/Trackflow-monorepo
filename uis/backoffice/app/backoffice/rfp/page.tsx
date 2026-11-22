"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { GENERIC_ERROR_MESSAGE, messageFor } from "../../../lib/api-client";
import { getAccessToken } from "../../../lib/auth";
import {
  COUNTRY_LABELS, MAX_UPLOAD_MB, POLL_INTERVAL_MS, SERVICE_LABELS, STATUS_LABELS, checkPdf, figureLines, hasPending, rfpApi, scoreText,
  type TicketDetail, type TicketSummary,
} from "../../../lib/rfp";

function when(value: string | null): string {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "";
}

function StatusBadge({ status }: { status: TicketSummary["status"] }) {
  return <span className={`incident-status-select rfp-status rfp-status-${status}`}>{STATUS_LABELS[status]}</span>;
}

function List({ items }: { items: string[] }) {
  return items.length === 0 ? <p className="incident-muted">Nothing to report.</p> : <ul className="rfp-list">{items.map((item, index) => <li key={index}>{item}</li>)}</ul>;
}

function TicketPanel({ ticket }: { ticket: TicketDetail }) {
  const { metadata, readability, classification } = ticket;
  const contact = metadata.client_contact;
  return (
    <section className="inventory-table-section analysis-panel" aria-label={`Ticket ${ticket.rfp_id}`} aria-live="polite">
      <h2>{ticket.rfp_id} <StatusBadge status={ticket.status} /></h2>
      <p className="incident-muted">{ticket.original_filename} · uploaded {when(ticket.created_at)} · last update {when(ticket.updated_at)}</p>

      {ticket.status === "analyzing" && (
        <>
          <p className="inventory-hint" role="status">{ticket.current_step}…</p>
          <ol className="rfp-steps" aria-label="Progress">
            {ticket.progress.map((entry, index) => <li key={index}><strong>{entry.step}</strong> {entry.detail}</li>)}
          </ol>
        </>
      )}

      {ticket.status === "failed" && <p className="inventory-alert" role="alert">{ticket.error_message ?? GENERIC_ERROR_MESSAGE}</p>}

      {ticket.status === "discarded" && (
        <div className="inventory-warning" role="status">
          <strong>This document is not a client RFP, so no department was involved.</strong>
          <p>{classification.reason}</p>
          <p className="incident-muted">Classified as {classification.document_type?.replace(/_/g, " ")}{classification.confidence !== null ? ` (confidence ${Math.round(classification.confidence * 100)}%)` : ""}.</p>
        </div>
      )}

      {ticket.status === "intake_complete" && (
        <>
          <h3>Summary for Sales</h3>
          <div className="rfp-summary">{(ticket.summary ?? "").split("\n").map((line, index) => (line.trim() ? <p key={index}>{line}</p> : null))}</div>

          <h3>Who to ask, and what</h3>
          {ticket.sections.length === 0 && <p className="inventory-warning">No department was identified from the request. Ask the client which services it wants.</p>}
          <div className="rfp-sections">
            {ticket.sections.map((section) => (
              <article key={section.department_id} className="rfp-section" aria-label={section.department}>
                <h4>{section.department}</h4>
                <p className="incident-muted">Ask: <strong>{section.owner}</strong> · contributes: {section.key_aspects.contributes}</p>
                <p>{section.key_aspects.summary}</p>
                <p><strong>What the owner must provide</strong></p>
                <List items={section.key_aspects.asks_of_owner} />
                {Object.keys(section.key_aspects.stated_figures).length > 0 && (<><p><strong>Figures stated in the RFP</strong></p><List items={figureLines(section.key_aspects.stated_figures)} /></>)}
                <p><strong>The RFP does not say</strong></p>
                <List items={section.key_aspects.open_questions} />
                <p><strong>Constraints</strong></p>
                <List items={section.key_aspects.constraints} />
              </article>
            ))}
          </div>

          <h3>Client and request</h3>
          <table className="inventory-table rfp-meta">
            <tbody>
              <tr><th scope="row">Client</th><td>{metadata.client_name ?? "Not stated"}</td></tr>
              <tr><th scope="row">Country · currency</th><td>{metadata.client_country ? `${COUNTRY_LABELS[metadata.client_country] ?? metadata.client_country} · ${metadata.currency ?? "?"}` : "Not clear"}</td></tr>
              <tr><th scope="row">Services requested</th><td>{metadata.services_requested.map((service) => SERVICE_LABELS[service] ?? service).join(", ") || "Not clear"}</td></tr>
              <tr><th scope="row">Monthly volume</th><td>{metadata.monthly_volume !== null ? `${metadata.monthly_volume.toLocaleString("en-US")} orders` : "Not stated"}</td></tr>
              <tr><th scope="row">Deadline</th><td>{metadata.deadline ?? "Not stated"}</td></tr>
              <tr><th scope="row">Budget</th><td>{metadata.budget_range ?? "Not stated"}</td></tr>
              <tr><th scope="row">Contact at the client</th><td>{[contact.name, contact.title, contact.email].filter(Boolean).join(" · ") || "Not stated"}</td></tr>
            </tbody>
          </table>

          <h3>Processing cost</h3>
          <p>
            Band <strong>{readability.cost_band ?? "unknown"}</strong>: about {readability.estimated_tokens?.toLocaleString("en-US") ?? "?"} tokens, {readability.word_count?.toLocaleString("en-US") ?? "?"} words.
            {" "}Flesch-Kincaid grade {scoreText(readability.flesch_kincaid_grade)} · Gunning Fog {scoreText(readability.gunning_fog)} · reading ease {scoreText(readability.flesch_reading_ease)} · ARI {scoreText(readability.ari)}.
          </p>
          {readability.note && <p className="incident-muted">{readability.note}</p>}

          <p className="incident-muted">
            Classified as {classification.document_type?.replace(/_/g, " ")} · decided by {ticket.engine === "llm" ? "the model" : ticket.engine === "mixed" ? "rules and the model" : "rules"} · handoff for the proposal stage: {ticket.handoff_state === "ready_for_part_2" ? "ready" : "not ready"}.
          </p>
        </>
      )}
    </section>
  );
}

/** Upload RFP PDFs (one ticket each) and follow them: the page asks the API every few seconds while any ticket is still being analysed. */
export default function RfpIntakePage() {
  const [tickets, setTickets] = useState<TicketSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<TicketDetail | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loaded, setLoaded] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const selectedRef = useRef<string | null>(null);
  selectedRef.current = selected;

  const refresh = useCallback(async () => {
    const token = getAccessToken();
    if (!token) return;
    try {
      const list = await rfpApi.list(token);
      setTickets(list);
      const current = selectedRef.current;
      if (current) setDetail(await rfpApi.get(token, current));
      setError("");
    } catch (caught) {
      setError(`The tickets could not be loaded. ${messageFor(caught, GENERIC_ERROR_MESSAGE)}`);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const pending = hasPending(tickets) || (detail !== null && detail.status === "analyzing");
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [pending, refresh]);

  async function open(ticketId: string) {
    const token = getAccessToken();
    if (!token) return;
    setSelected(ticketId);
    selectedRef.current = ticketId;
    try {
      setDetail(await rfpApi.get(token, ticketId));
    } catch (caught) {
      setError(`The ticket could not be loaded. ${messageFor(caught, GENERIC_ERROR_MESSAGE)}`);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setNotice("");
    if (!file) {
      setError("Choose a PDF first.");
      return;
    }
    const problem = checkPdf(file);
    if (problem) {
      setError(problem);
      return;
    }
    const token = getAccessToken();
    if (!token) return;
    setUploading(true);
    setError("");
    try {
      const ticket = await rfpApi.upload(token, file);
      setNotice(`${ticket.rfp_id} received. It is being analysed: this page updates by itself.`);
      setFile(null);
      if (input.current) input.current.value = "";
      await open(ticket.ticket_id);
      await refresh();
    } catch (caught) {
      setError(`The upload failed. ${messageFor(caught, GENERIC_ERROR_MESSAGE)}`);
    } finally {
      setUploading(false);
    }
  }

  return (
    <>
      <header className="inventory-page-header">
        <div>
          <p className="inventory-eyebrow">SALES · RFP INTAKE</p>
          <h1>RFP intake</h1>
          <p>Upload a client&apos;s RFP as a PDF. TrackFlow checks that it is a real request, reads its data and tells you which departments to involve and what to ask each owner.</p>
        </div>
      </header>

      <section className="inventory-form-section">
        <form className="incident-form" onSubmit={submit} noValidate>
          <label htmlFor="rfp-file"><strong>RFP document (PDF, up to {MAX_UPLOAD_MB} MB)</strong></label>
          <input id="rfp-file" ref={input} type="file" accept="application/pdf,.pdf" disabled={uploading} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setError(""); }} />
          <div className="analysis-period">
            <button className="inventory-primary-button" type="submit" disabled={uploading || !file}>{uploading ? "Uploading…" : "Upload and analyze"}</button>
          </div>
        </form>
      </section>

      {notice && <p className="inventory-success" role="status">{notice}</p>}
      {error && <p className="inventory-alert" role="alert">{error}</p>}

      <section className="inventory-table-section" aria-label="Tickets">
        <h2>Tickets</h2>
        {loaded && tickets.length === 0 && <p className="incident-muted">No RFP has been uploaded yet.</p>}
        {tickets.length > 0 && (
          <div className="inventory-table-scroll">
            <table className="inventory-table">
              <thead><tr><th scope="col">RFP</th><th scope="col">File</th><th scope="col">Status</th><th scope="col">Now</th><th scope="col">Client</th><th scope="col">Departments</th><th scope="col"><span className="sr-only">Open</span></th></tr></thead>
              <tbody>
                {tickets.map((ticket) => (
                  <tr key={ticket.ticket_id} aria-selected={ticket.ticket_id === selected}>
                    <td>{ticket.rfp_id}</td>
                    <td>{ticket.original_filename}</td>
                    <td><StatusBadge status={ticket.status} /></td>
                    <td>{ticket.status === "analyzing" ? ticket.current_step : ""}</td>
                    <td>{ticket.client_name ?? ""}{ticket.client_country ? ` (${ticket.client_country})` : ""}</td>
                    <td>{ticket.departments_needed.join(", ")}</td>
                    <td><button className="inventory-secondary-button" type="button" onClick={() => void open(ticket.ticket_id)}>Open</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {detail && <TicketPanel ticket={detail} />}
    </>
  );
}
