"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { GENERIC_ERROR_MESSAGE, messageFor } from "../../../lib/api-client";
import { getAccessToken } from "../../../lib/auth";
import { periodFromDays, telemetryReportApi, type Row, type TelemetryReport } from "../../../lib/telemetry-report";

interface Column {
  key: string;
  label: string;
  /** Show the value as a horizontal bar scaled to the largest value of the column. */
  bar?: boolean;
  percent?: boolean;
}

function format(value: Row[string] | undefined, percent?: boolean): string {
  if (value === null || value === undefined) return "n/a";
  if (percent && typeof value === "number") return `${(value * 100).toFixed(1)}%`;
  return String(value);
}

/** One table per metric; a column can also be drawn as bars. No charting library: the data is small and operational. */
function MetricTable({ title, question, columns, rows }: { title: string; question: string; columns: Column[]; rows: Row[] }) {
  const maxima = useMemo(
    () => Object.fromEntries(columns.filter((column) => column.bar).map((column) => [column.key, Math.max(0, ...rows.map((row) => Number(row[column.key] ?? 0)))])),
    [columns, rows],
  );
  return (
    <section className="inventory-table-section analysis-panel" aria-label={title}>
      <h2>{title}</h2>
      <p className="incident-muted">{question}</p>
      {rows.length === 0 ? <p className="incident-muted">No events in this period.</p> : (
        <div className="inventory-table-scroll">
          <table className="inventory-table">
            <thead><tr>{columns.map((column) => <th key={column.key} scope="col">{column.label}</th>)}</tr></thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index}>
                  {columns.map((column) => (
                    <td key={column.key} className={typeof row[column.key] === "number" ? "inventory-number" : undefined}>
                      {format(row[column.key], column.percent)}
                      {column.bar && maxima[column.key] > 0 && (
                        <span className="analysis-bar" aria-hidden="true"><span style={{ width: `${(Number(row[column.key] ?? 0) / maxima[column.key]) * 100}%` }} /></span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function dayKey(offsetDays: number): string {
  const day = new Date();
  day.setUTCDate(day.getUTCDate() + offsetDays);
  return day.toISOString().slice(0, 10);
}

export default function TelemetryReportPage() {
  const [report, setReport] = useState<TelemetryReport | null>(null);
  const [fromDay, setFromDay] = useState(dayKey(-6));
  const [toDay, setToDay] = useState(dayKey(0));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (period?: { fromDay: string; toDay: string }) => {
    const token = getAccessToken();
    if (!token) return;
    setLoading(true);
    setError("");
    try {
      setReport(await telemetryReportApi.get(token, period ? periodFromDays(period.fromDay, period.toDay) : {}));
    } catch (caught) {
      setError(`The report could not be loaded. ${messageFor(caught, GENERIC_ERROR_MESSAGE)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const dailyTotals = useMemo<Row[]>(() => {
    const totals = new Map<string, number>();
    for (const row of report?.metrics.events_per_day ?? []) totals.set(String(row.date), (totals.get(String(row.date)) ?? 0) + Number(row.count));
    return [...totals].map(([date, events]) => ({ date, events }));
  }, [report]);

  const metrics = report?.metrics;
  return (
    <>
      <header className="inventory-page-header"><div><p className="inventory-eyebrow">ENGINEERING / TELEMETRY</p><h1>System health</h1><p>Operational metrics calculated from the stored telemetry events. Cached for 60 seconds.</p></div></header>
      <section className="inventory-form-section">
        <form className="analysis-period" onSubmit={(event) => { event.preventDefault(); void load({ fromDay, toDay }); }}>
          <label>From<input type="date" value={fromDay} max={toDay} onChange={(event) => setFromDay(event.target.value)} /></label>
          <label>To (included)<input type="date" value={toDay} min={fromDay} onChange={(event) => setToDay(event.target.value)} /></label>
          <button className="inventory-primary-button" type="submit" disabled={loading || !fromDay || !toDay}>Show period</button>
          <button className="inventory-secondary-button" type="button" disabled={loading} onClick={() => { setFromDay(dayKey(-6)); setToDay(dayKey(0)); void load(); }}>Last 7 days</button>
        </form>
        {report && <p className="inventory-hint" role="status">Showing {report.period.from} to {report.period.to} (UTC, end excluded).</p>}
        {loading && <p className="inventory-hint" role="status">Loading the report…</p>}
        {error && <p className="inventory-alert" role="alert">{error}</p>}
      </section>
      {metrics && (
        <>
          <MetricTable title="Events per day" question="How much telemetry does the system produce each day?" rows={dailyTotals}
            columns={[{ key: "date", label: "Day (UTC)" }, { key: "events", label: "Events", bar: true }]} />
          <MetricTable title="Events by day and type" question="Which kinds of events dominate?" rows={metrics.events_per_day}
            columns={[{ key: "date", label: "Day (UTC)" }, { key: "event_type", label: "Event type" }, { key: "count", label: "Events", bar: true }]} />
          <MetricTable title="Error rate by day" question="Is the system getting worse? Errors and warnings as a share of all events." rows={metrics.error_rate_by_day}
            columns={[{ key: "date", label: "Day (UTC)" }, { key: "events", label: "Events" }, { key: "errors", label: "Errors" }, { key: "error_rate", label: "Error rate", percent: true, bar: true }, { key: "warnings", label: "Warnings" }, { key: "warning_rate", label: "Warning rate", percent: true, bar: true }]} />
          <MetricTable title="Error rate by type" question="Which failures dominate?" rows={metrics.error_rate_by_type}
            columns={[{ key: "event_type", label: "Event type" }, { key: "level", label: "Level" }, { key: "events", label: "Events", bar: true }, { key: "share_of_all_events", label: "Share of all events", percent: true }, { key: "last_seen", label: "Last seen (UTC)" }]} />
          <MetricTable title="Page load latency by day" question="How fast do screens load? Time to first byte and largest paint, in ms." rows={metrics.page_load_latency_by_day}
            columns={[{ key: "date", label: "Day (UTC)" }, { key: "section", label: "Section" }, { key: "loads", label: "Loads" }, { key: "ttfb_avg_ms", label: "TTFB avg", bar: true }, { key: "ttfb_p95_ms", label: "TTFB p95" }, { key: "lcp_p75_ms", label: "LCP p75" }]} />
          <MetricTable title="Network failures by route" question="Where does the browser lose the API?" rows={metrics.network_failures_by_route}
            columns={[{ key: "date", label: "Day (UTC)" }, { key: "route_template", label: "API route" }, { key: "failure_kind", label: "Kind" }, { key: "failures", label: "Failures", bar: true }, { key: "avg_duration_ms", label: "Avg wait (ms)" }]} />
          <MetricTable title="Frontend errors by route" question="Which screens crash?" rows={metrics.frontend_errors_by_route}
            columns={[{ key: "date", label: "Day (UTC)" }, { key: "route_template", label: "Route" }, { key: "error_name", label: "Error" }, { key: "occurrences", label: "Occurrences", bar: true }, { key: "distinct_errors", label: "Distinct defects" }, { key: "boundary_hits", label: "Error screen shown" }]} />
          <MetricTable title="Sign-in failure rate" question="Can people sign in? Failed attempts over all attempts." rows={metrics.auth_failure_rate}
            columns={[{ key: "date", label: "Day (UTC)" }, { key: "attempts", label: "Attempts" }, { key: "failures", label: "Failures" }, { key: "failure_rate", label: "Failure rate", percent: true, bar: true }]} />
        </>
      )}
    </>
  );
}
