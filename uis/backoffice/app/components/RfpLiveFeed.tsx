"use client";

import Link from "next/link";
import { DEPARTMENT_LABELS, SERVICE_LABELS, STATUS_LABELS, COUNTRY_LABELS, type TicketStatus } from "../../lib/rfp";
import { statusText, type FeedTicket } from "../../lib/rfp-stream";
import { useRfpTicketFeed } from "./useRfpTicketFeed";

function time(value: string | null): string {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }) : "";
}

function statusLabel(status: string): string {
  return status === "analyzing" ? "Analyzing · needs processing" : (STATUS_LABELS[status as TicketStatus] ?? status);
}

function TicketCard({ ticket, onDismiss }: { ticket: FeedTicket; onDismiss: (ticketId: string) => void }) {
  const scope = ticket.services_requested.length > 0 ? ticket.services_requested.map((service) => SERVICE_LABELS[service] ?? service) : ticket.departments_needed.map((id) => DEPARTMENT_LABELS[id] ?? id);
  return (
    <li className={`rfp-live-card${ticket.fresh ? " is-fresh" : ""}`} data-ticket-id={ticket.ticket_id}>
      <div className="rfp-live-card-top">
        {ticket.fresh && <span className="rfp-live-new">{ticket.source === "recovered" ? "MISSED WHILE OFFLINE" : "NEW"}</span>}
        <strong>{ticket.rfp_id || ticket.ticket_id}</strong>
        <span className="rfp-live-time">{time(ticket.created_at)}</span>
      </div>
      <p className="rfp-live-client">{ticket.client_name ?? "Client not identified"}{ticket.client_country ? ` · ${COUNTRY_LABELS[ticket.client_country] ?? ticket.client_country}` : ""}</p>
      {scope.length > 0 && <p className="rfp-live-scope">{scope.join(" · ")}</p>}
      <div className="rfp-live-card-bottom">
        <span className={`rfp-live-status rfp-live-status-${ticket.status}`}>{statusLabel(ticket.status)}</span>
        <span className="rfp-live-id">ticket {ticket.ticket_id.slice(0, 8)}</span>
        <Link className="text-link" href={`/backoffice/rfp?ticket=${encodeURIComponent(ticket.ticket_id)}`}>Open <span aria-hidden="true">→</span></Link>
        <button className="rfp-live-dismiss" type="button" aria-label={`Hide ${ticket.rfp_id || ticket.ticket_id}`} onClick={() => onDismiss(ticket.ticket_id)}>×</button>
      </div>
    </li>
  );
}

/**
 * New RFP tickets, in real time: a card appears by itself the moment the intake accepts an RFP (event `rfp_ticket_created`), highlighted and distinct from the rest of the dashboard.
 * No reload and no refetch per event. If the connection drops it reconnects by itself and the pill says so; what was registered meanwhile is recovered.
 */
export function RfpLiveFeed({ limit = 6 }: { limit?: number }) {
  const { tickets, status, dismiss } = useRfpTicketFeed();
  return (
    <section className="rfp-live" aria-labelledby="rfp-live-title">
      <div className="section-title-row">
        <div><p className="eyebrow">REAL TIME · RFP INTAKE</p><h2 id="rfp-live-title">New RFP tickets</h2></div>
        <span className={`rfp-live-pill rfp-live-pill-${status.state}`} role="status" aria-live="polite"><span aria-hidden="true" />{statusText(status)}</span>
      </div>
      {tickets.length === 0 ? (
        <p className="empty-state rfp-live-empty">No RFP tickets yet. They appear here by themselves the moment one is registered: nobody has to refresh.</p>
      ) : (
        <ul className="rfp-live-list" aria-label="RFP tickets, newest first" aria-live="polite">
          {tickets.slice(0, limit).map((ticket) => <TicketCard key={ticket.ticket_id} ticket={ticket} onDismiss={dismiss} />)}
        </ul>
      )}
      <p className="rfp-live-note">Cards show the status each ticket had when it arrived. <Link className="text-link" href="/backoffice/rfp">RFP intake <span aria-hidden="true">→</span></Link></p>
    </section>
  );
}
