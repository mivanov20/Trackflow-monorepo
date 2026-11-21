"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { getAccessToken } from "../../../lib/auth";
import {
  BRANCHES,
  BRANCH_LABELS,
  CATEGORIES,
  CATEGORY_LABELS,
  ORIGINS,
  ORIGIN_LABELS,
  STATUSES,
  STATUS_LABELS,
  STATUS_TRANSITIONS,
  fieldErrorsOf,
  incidentsApi,
  userMessage,
  type Incident,
  type IncidentFilters,
  type IncidentStatus,
  type IncidentSummary,
} from "../../../lib/incidents";

type LoadState = "loading" | "error" | "ready";

const FILTER_FIELDS = [
  { name: "status", label: "Status", all: "All statuses", options: STATUSES.map((value) => [value, STATUS_LABELS[value]]) },
  { name: "origin", label: "Origin", all: "All origins", options: ORIGINS.map((value) => [value, ORIGIN_LABELS[value]]) },
  { name: "branch", label: "Branch", all: "All branches", options: BRANCHES.map((value) => [value, BRANCH_LABELS[value]]) },
  { name: "category", label: "Category", all: "All categories", options: CATEGORIES.map((value) => [value, CATEGORY_LABELS[value]]) },
] as const;

function SummaryGroup({ title, counts, labels }: { title: string; counts?: Record<string, number> | null; labels: Record<string, string> }) {
  // Si la API omite un grupo, se muestra vacío en lugar de romper el render.
  const entries = Object.entries(counts ?? {});
  return (
    <div className="incident-summary-group">
      <h3>{title}</h3>
      {entries.length === 0 && <p className="incident-muted">No data available.</p>}
      <dl>
        {entries.map(([key, count]) => (
          <div key={key} className={count === 0 ? "incident-zero" : undefined}>
            <dt>{labels[key] ?? key}</dt>
            <dd>{count}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** Fecha legible; un valor ausente o inválido no muestra "Invalid Date". */
function formatCreated(value: string | null | undefined): string {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleString() : "—";
}

export default function IncidentsPage() {
  const [filters, setFilters] = useState<IncidentFilters>({});
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [listState, setListState] = useState<LoadState>("loading");
  const [listError, setListError] = useState("");
  const [summary, setSummary] = useState<IncidentSummary | null>(null);
  const [summaryState, setSummaryState] = useState<LoadState>("loading");
  const [summaryError, setSummaryError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState<Record<number, IncidentStatus>>({});
  const listRequest = useRef(0);
  const summaryRequest = useRef(0);

  /** Loads the filtered list; a response that arrives after a newer request started is ignored. */
  const loadList = useCallback(async (current: IncidentFilters, silent = false) => {
    const token = getAccessToken();
    if (!token) return;
    const requestId = ++listRequest.current;
    if (!silent) setListState("loading");
    try {
      const result = await incidentsApi.list(token, current);
      if (requestId !== listRequest.current) return;
      setIncidents(result);
      setListState("ready");
    } catch (caught) {
      if (requestId !== listRequest.current) return;
      setListError(`The incidents could not be loaded. ${userMessage(caught)}`);
      setListState("error");
    }
  }, []);

  const loadSummary = useCallback(async (silent = false) => {
    const token = getAccessToken();
    if (!token) return;
    const requestId = ++summaryRequest.current;
    if (!silent) setSummaryState("loading");
    try {
      const result = await incidentsApi.summary(token);
      if (requestId !== summaryRequest.current) return;
      setSummary(result);
      setSummaryState("ready");
    } catch (caught) {
      if (requestId !== summaryRequest.current) return;
      setSummaryError(`The summary could not be loaded. ${userMessage(caught)}`);
      setSummaryState("error");
    }
  }, []);

  useEffect(() => {
    void loadList(filters);
  }, [filters, loadList]);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  /** Updates the status; on failure the select returns to the previous value and the user is told. */
  async function changeStatus(incident: Incident, wanted: IncidentStatus) {
    const token = getAccessToken();
    if (!token) return;
    setNotice("");
    setPending((current) => ({ ...current, [incident.id]: wanted }));
    try {
      await incidentsApi.updateStatus(token, incident.id, wanted);
      await Promise.all([loadList(filters, true), loadSummary(true)]);
    } catch (caught) {
      setNotice(`Incident #${incident.id} was not changed: ${fieldErrorsOf(caught).status ?? userMessage(caught)}`);
    } finally {
      setPending((current) => {
        const { [incident.id]: _done, ...rest } = current;
        return rest;
      });
    }
  }

  const hasFilters = Object.values(filters).some(Boolean);

  return (
    <>
      <header className="inventory-page-header">
        <div>
          <p className="inventory-eyebrow">OPERATIONS / INCIDENTS</p>
          <h1>Incident manager</h1>
          <p>Every incident, from every branch, in one place.</p>
        </div>
        <Link className="inventory-primary-button incident-link-button" href="/backoffice/incidents/new">+ Report incident</Link>
      </header>

      <section className="inventory-table-section incident-summary" aria-labelledby="incident-summary-heading" aria-busy={summaryState === "loading"}>
        <div className="incident-section-title">
          <h2 id="incident-summary-heading">Summary</h2>
          {summaryState === "ready" && summary && <span className="incident-muted">{summary.total ?? 0} incident{summary.total === 1 ? "" : "s"} in total</span>}
        </div>
        {summaryState === "loading" && <p className="incident-state" role="status"><span className="incident-spinner incident-spinner-dark" aria-hidden="true" /> Loading summary…</p>}
        {summaryState === "error" && (
          <p className="incident-state incident-state-error" role="alert">
            <span>{summaryError}</span>
            <button className="inventory-secondary-button" type="button" onClick={() => void loadSummary()}>Try again</button>
          </p>
        )}
        {summaryState === "ready" && summary && (
          <div className="incident-summary-groups">
            <SummaryGroup title="By status" counts={summary.by_status} labels={STATUS_LABELS} />
            <SummaryGroup title="By category" counts={summary.by_category} labels={CATEGORY_LABELS} />
            <SummaryGroup title="By origin" counts={summary.by_origin} labels={ORIGIN_LABELS} />
            <SummaryGroup title="By branch" counts={summary.by_branch} labels={BRANCH_LABELS} />
          </div>
        )}
      </section>

      <section className="inventory-table-section" aria-labelledby="incident-list-heading">
        <div className="incident-section-title"><h2 id="incident-list-heading">Incidents</h2></div>

        <div className="incident-filters">
          {FILTER_FIELDS.map(({ name, label, all, options }) => (
            <select
              key={name}
              aria-label={`Filter by ${label.toLowerCase()}`}
              value={filters[name] ?? ""}
              onChange={(event) => setFilters((current) => ({ ...current, [name]: event.target.value || undefined }))}
            >
              <option value="">{all}</option>
              {options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
            </select>
          ))}
        </div>

        {notice && <p className="inventory-alert incident-notice" role="alert">{notice}</p>}

        {listState === "loading" && <p className="incident-state" role="status"><span className="incident-spinner incident-spinner-dark" aria-hidden="true" /> Loading incidents…</p>}
        {listState === "error" && (
          <p className="incident-state incident-state-error" role="alert">
            <span>{listError}</span>
            <button className="inventory-secondary-button" type="button" onClick={() => void loadList(filters)}>Try again</button>
          </p>
        )}
        {listState === "ready" && incidents.length === 0 && (
          <p className="incident-state">
            <span>{hasFilters ? "No incidents match these filters." : "No incidents have been registered yet."}</span>
            {hasFilters
              ? <button className="inventory-secondary-button" type="button" onClick={() => setFilters({})}>Clear filters</button>
              : <Link className="inventory-secondary-button incident-link-button" href="/backoffice/incidents/new">Report an incident</Link>}
          </p>
        )}
        {listState === "ready" && incidents.length > 0 && (
          <div className="inventory-table-scroll">
            <table className="inventory-table">
              <thead><tr><th>INCIDENT</th><th>CATEGORY</th><th>ORIGIN</th><th>BRANCH</th><th>STATUS</th><th>CREATED</th></tr></thead>
              <tbody>
                {incidents.map((incident) => {
                  // Un estado desconocido se trata como final: sin transiciones, en lugar de fallar.
                  const next = STATUS_TRANSITIONS[incident.status] ?? [];
                  const shown = pending[incident.id] ?? incident.status;
                  return (
                    <tr key={incident.id}>
                      <td className="incident-title-cell" title={incident.description}>
                        <strong>{incident.title}</strong>
                        <span className="inventory-code">#{incident.id}</span>
                      </td>
                      <td>{CATEGORY_LABELS[incident.category] ?? incident.category ?? "—"}</td>
                      <td>{ORIGIN_LABELS[incident.origin] ?? incident.origin ?? "—"}</td>
                      <td>{BRANCH_LABELS[incident.branch] ?? incident.branch ?? "—"}</td>
                      <td>
                        <select
                          className={`incident-status-select incident-status-${shown}`}
                          aria-label={`Status of incident ${incident.id}`}
                          value={shown}
                          disabled={next.length === 0 || incident.id in pending}
                          title={next.length === 0 ? "Final status: it can no longer change." : undefined}
                          onChange={(event) => void changeStatus(incident, event.target.value as IncidentStatus)}
                        >
                          {[incident.status, ...next].map((value) => <option key={value} value={value}>{STATUS_LABELS[value] ?? value}</option>)}
                        </select>
                      </td>
                      <td>{formatCreated(incident.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {listState === "ready" && <p className="incident-muted incident-count" aria-live="polite">Showing {incidents.length} incident{incidents.length === 1 ? "" : "s"}</p>}
      </section>
    </>
  );
}
