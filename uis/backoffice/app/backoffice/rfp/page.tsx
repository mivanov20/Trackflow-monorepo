"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { GENERIC_ERROR_MESSAGE, messageFor } from "../../../lib/api-client";
import { getAccessToken } from "../../../lib/auth";
import {
  APPROVAL_STATUS_LABELS, COUNTRY_LABELS, DRAFT_STATUS_LABELS, MAX_UPLOAD_MB, POLL_INTERVAL_MS, SERVICE_LABELS, canStartApproval, canStartProposal, checkDecision, checkPdf, figureLines, hasPending, inProgress, rfpApi, scoreText, statusLabel,
  type DecisionInput, type DecisionKind, type DepartmentSection, type FinalDocument, type TicketDetail, type TicketSummary, type TraceEvent,
} from "../../../lib/rfp";

function when(value: string | null): string {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "";
}

function StatusBadge({ ticket }: { ticket: Pick<TicketSummary, "status" | "proposal_state"> & { approval_state?: string } }) {
  const ready = ticket.status === "under_evaluation" && ticket.proposal_state === "ready_for_part_3";
  return <span className={`incident-status-select rfp-status rfp-status-${ready ? "ready" : ticket.status}`}>{statusLabel(ticket)}</span>;
}

function Checks({ section }: { section: DepartmentSection }) {
  const result = section.evaluation_result;
  if (!result) return <p className="incident-muted">Not evaluated yet.</p>;
  const mark = (ok: boolean) => (ok ? "passed" : "failed");
  return (
    <ul className="rfp-list" aria-label={`Evaluation of the ${section.department} section`}>
      <li>Readability: <strong>{mark(result.readability.pass)}</strong>{typeof result.readability.score === "number" ? ` (grade ${result.readability.score.toFixed(1)})` : ""}</li>
      <li>Relevance: <strong>{mark(result.relevance.pass)}</strong>{result.relevance.missing_aspects.length > 0 ? ` (missing: ${result.relevance.missing_aspects.join("; ")})` : ""}</li>
      <li>Compliance: <strong>{mark(result.compliance.pass)}</strong>{result.compliance.rule_ids.length > 0 ? ` (${result.compliance.rule_ids.join(", ")})` : ""}</li>
    </ul>
  );
}

/** The owner's decision on a section: approve, request changes (optionally with an edited draft) or reject. It is the human approval point: only the owner of the department decides. */
function ApprovalForm({ section, onDecide }: { section: DepartmentSection; onDecide: (departmentId: string, body: DecisionInput) => Promise<string> }) {
  const [decision, setDecision] = useState<DecisionKind>("approve");
  const [comment, setComment] = useState("");
  const [capacity, setCapacity] = useState("");
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState(section.draft_content ?? "");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const id = `decision-${section.department_id}`;
  const canCommit = section.department_id === "warehouse";

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = checkDecision({ decision, comment, capacity });
    if (found) {
      setProblem(found);
      return;
    }
    setBusy(true);
    setProblem("");
    const body: DecisionInput = { decision, approver: section.responsible, comment: comment.trim() };
    if (decision === "request_changes" && edit && draft.trim() && draft.trim() !== (section.draft_content ?? "").trim()) body.edited_content = draft.trim();
    if (canCommit && capacity.trim()) body.commitments = { capacity_orders_per_month: Number(capacity.trim()) };
    const failure = await onDecide(section.department_id, body);
    setBusy(false);
    if (failure) setProblem(failure);
    else {
      setComment("");
      setEdit(false);
    }
  }

  return (
    <form className="rfp-approval" onSubmit={submit} noValidate aria-label={`Decision of ${section.responsible}`}>
      <p><strong>Decision of {section.responsible}</strong> <span className="incident-muted">(round {section.approval_round} · registered by you)</span></p>
      <div className="analysis-period" role="radiogroup" aria-label="Decision">
        {([["approve", "Approve"], ["request_changes", "Request changes"], ["reject", "Reject"]] as [DecisionKind, string][]).map(([value, label]) => (
          <label key={value} className="rfp-choice"><input type="radio" name={id} value={value} checked={decision === value} disabled={busy} onChange={() => setDecision(value)} /> {label}</label>
        ))}
      </div>
      <label htmlFor={`${id}-comment`}>Comment{decision === "approve" ? " (optional)" : " (what has to change)"}</label>
      <textarea id={`${id}-comment`} rows={2} maxLength={2000} value={comment} disabled={busy} onChange={(event) => setComment(event.target.value)} />
      {canCommit && (<><label htmlFor={`${id}-capacity`}>Committed capacity, orders per month (optional)</label><input id={`${id}-capacity`} inputMode="numeric" value={capacity} disabled={busy} onChange={(event) => setCapacity(event.target.value)} /></>)}
      {decision === "request_changes" && (
        <>
          <label className="rfp-choice"><input type="checkbox" checked={edit} disabled={busy} onChange={(event) => setEdit(event.target.checked)} /> Send my own edited draft (it is evaluated again)</label>
          {edit && <textarea aria-label="Edited draft" rows={8} value={draft} disabled={busy} onChange={(event) => setDraft(event.target.value)} />}
        </>
      )}
      {problem && <p className="inventory-alert" role="alert">{problem}</p>}
      <div className="analysis-period"><button className="inventory-primary-button" type="submit" disabled={busy}>{busy ? "Sending…" : "Send decision"}</button></div>
    </form>
  );
}

/** One department's draft of the proposal with its evaluation and its approval; a section that used up its attempts is flagged for a person, with the last problems. */
function DraftPanel({ section, onDecide }: { section: DepartmentSection; onDecide: (departmentId: string, body: DecisionInput) => Promise<string> }) {
  const result = section.evaluation_result;
  const review = section.draft_status === "needs_human_review";
  return (
    <article className={`rfp-section rfp-draft-section${review ? " rfp-needs-review" : ""}`} aria-label={`${section.department} draft`}>
      <h4>{section.department} <span className={`incident-status-select rfp-status rfp-draft-${section.draft_status}`}>{DRAFT_STATUS_LABELS[section.draft_status] ?? section.draft_status}</span></h4>
      <p className="incident-muted">Owner: <strong>{section.owner}</strong> · attempts: {section.iterations}{section.draft_engine ? ` · written by ${section.draft_engine === "llm" ? "the model" : "rules"}` : ""}</p>
      {review && result && <p className="inventory-warning" role="status"><strong>This section did not pass after {section.iterations} attempts.</strong> {result.feedback_for_generator}</p>}
      <Checks section={section} />
      <p className="incident-muted">
        Approval: <strong>{APPROVAL_STATUS_LABELS[section.approval_status] ?? section.approval_status}</strong>
        {section.approver ? ` by ${section.approver}${section.approved_at ? ` on ${when(section.approved_at)}` : ""}` : ""}
        {section.approval_history.length > 0 ? ` · ${section.approval_history.map((entry) => `round ${entry.round}: ${entry.decision.replace(/_/g, " ")} (${entry.approver})`).join("; ")}` : ""}
      </p>
      {section.awaiting_approval && <ApprovalForm section={section} onDecide={onDecide} />}
      {section.draft_content ? <pre className="rfp-draft">{section.draft_content}</pre> : <p className="incident-muted">No draft yet.</p>}
    </article>
  );
}

function List({ items }: { items: string[] }) {
  return items.length === 0 ? <p className="incident-muted">Nothing to report.</p> : <ul className="rfp-list">{items.map((item, index) => <li key={index}>{item}</li>)}</ul>;
}

/** The final document, once every department approved: read it here or download it as Markdown. */
function FinalPanel({ ticket }: { ticket: TicketDetail }) {
  const [document, setDocument] = useState<FinalDocument | null>(null);
  const [problem, setProblem] = useState("");
  useEffect(() => {
    const token = getAccessToken();
    if (!token || !ticket.document_ready) return;
    rfpApi.document(token, ticket.ticket_id).then(setDocument).catch((caught) => setProblem(`The document could not be loaded. ${messageFor(caught, GENERIC_ERROR_MESSAGE)}`));
  }, [ticket.ticket_id, ticket.document_ready]);
  function download() {
    if (!document) return;
    const url = URL.createObjectURL(new Blob([document.content_markdown], { type: "text/markdown" }));
    const link = window.document.createElement("a");
    link.href = url;
    link.download = `${document.rfp_id}-proposal.md`;
    link.click();
    URL.revokeObjectURL(url);
  }
  return (
    <section className="rfp-final" aria-label="Final document">
      <h3>Final document</h3>
      {problem && <p className="inventory-alert" role="alert">{problem}</p>}
      {document && (
        <>
          <p className="incident-muted">{document.rfp_id} · {document.currency} · generated {when(document.generated_at)}</p>
          <div className="analysis-period"><button className="inventory-secondary-button" type="button" onClick={download}>Download Markdown</button></div>
          <pre className="rfp-draft">{document.content_markdown}</pre>
        </>
      )}
    </section>
  );
}

/** Which agent did what, in what order, across the three parts. */
function TracePanel({ ticket }: { ticket: TicketDetail }) {
  const [events, setEvents] = useState<TraceEvent[] | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const token = getAccessToken();
    if (!token || !open) return;
    rfpApi.trace(token, ticket.ticket_id).then((answer) => setEvents(answer.events)).catch(() => setEvents([]));
  }, [open, ticket.ticket_id, ticket.updated_at]);
  return (
    <details className="rfp-trace" onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}>
      <summary>Trace: which agent did what, in order</summary>
      {events && events.length > 0 && (
        <div className="inventory-table-scroll">
          <table className="inventory-table">
            <thead><tr><th scope="col">#</th><th scope="col">Part</th><th scope="col">Time</th><th scope="col">Agent</th><th scope="col">Step</th><th scope="col">Department</th><th scope="col">Result</th></tr></thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.seq}><td>{event.seq}</td><td>{event.part}</td><td>{event.at.slice(11, 19)}</td><td>{event.agent}</td><td>{event.node}</td><td>{event.department_id ?? ""}</td><td className="rfp-trace-output">{JSON.stringify(event.output).slice(0, 140)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}

function TicketPanel({ ticket, onDecide }: { ticket: TicketDetail; onDecide: (departmentId: string, body: DecisionInput) => Promise<string> }) {
  const { metadata, readability, classification } = ticket;
  const contact = metadata.client_contact;
  return (
    <section className="inventory-table-section analysis-panel" aria-label={`Ticket ${ticket.rfp_id}`} aria-live="polite">
      <h2>{ticket.rfp_id} <StatusBadge ticket={ticket} /></h2>
      <p className="incident-muted">{ticket.original_filename} · uploaded {when(ticket.created_at)} · last update {when(ticket.updated_at)}</p>

      {inProgress(ticket) && (
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

      {["intake_complete", "drafting", "under_evaluation", "needs_human_review", "waiting_for_approval", "done"].includes(ticket.status) && (
        <>
          {ticket.status === "done" && <FinalPanel ticket={ticket} />}
          {ticket.status === "waiting_for_approval" && <p className="inventory-warning" role="status">{ticket.current_step}. Each owner decides on their own section; the others keep moving.</p>}
          {ticket.error_message && ["intake_complete", "needs_human_review"].includes(ticket.status) && <p className="inventory-alert" role="alert">{ticket.error_message}</p>}
          {ticket.sections.some((section) => section.draft_content) && (
            <>
              <h3>Proposal draft by department</h3>
              <div className="rfp-sections">{ticket.sections.map((section) => <DraftPanel key={section.department_id} section={section} onDecide={onDecide} />)}</div>
            </>
          )}
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

          <TracePanel ticket={ticket} />
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

  const pending = hasPending(tickets) || (detail !== null && inProgress(detail));
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

  async function startProposal(ticketId: string) {
    const token = getAccessToken();
    if (!token) return;
    setError("");
    try {
      await rfpApi.startProposal(token, ticketId);
      await refresh();
    } catch (caught) {
      setError(`The proposal could not be started. ${messageFor(caught, GENERIC_ERROR_MESSAGE)}`);
    }
  }

  /** Sends the owner's decision. Returns the message to show when it fails, or an empty string when it was registered. */
  async function decide(departmentId: string, body: DecisionInput): Promise<string> {
    const token = getAccessToken();
    if (!token || !selectedRef.current) return "Sign in again to continue.";
    try {
      await rfpApi.decide(token, selectedRef.current, departmentId, body);
      await refresh();
      return "";
    } catch (caught) {
      return messageFor(caught, GENERIC_ERROR_MESSAGE);
    }
  }

  async function startApproval(ticketId: string) {
    const token = getAccessToken();
    if (!token) return;
    setError("");
    try {
      await rfpApi.startApproval(token, ticketId);
      await refresh();
    } catch (caught) {
      setError(`The approvals could not be started. ${messageFor(caught, GENERIC_ERROR_MESSAGE)}`);
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
                    <td><StatusBadge ticket={ticket} /></td>
                    <td>{inProgress(ticket) ? ticket.current_step : ""}</td>
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

      {detail && canStartProposal(detail) && (
        <div className="analysis-period">
          <button className="inventory-primary-button" type="button" onClick={() => void startProposal(detail.ticket_id)}>Draft the proposal</button>
        </div>
      )}
      {detail && canStartApproval(detail) && (
        <div className="analysis-period">
          <button className="inventory-primary-button" type="button" onClick={() => void startApproval(detail.ticket_id)}>Send the sections for approval</button>
        </div>
      )}
      {detail && <TicketPanel ticket={detail} onDecide={decide} />}
    </>
  );
}
