"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, GENERIC_ERROR_MESSAGE, messageFor } from "../../../lib/api-client";
import { getAccessToken } from "../../../lib/auth";
import {
  KPIS, clientName, percent, reportingApi, shiftWeek, warehouseName, warehouseTotals, weekLabel,
  type LatestRun, type WeeklyEntry, type WeeklyPerformance,
} from "../../../lib/reporting";

type Kpi = (typeof KPIS)[number];

function formatRefreshed(value: string | null): string {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleString("en-US", { dateStyle: "long", timeStyle: "short" }) : "";
}

function valueOf(entry: WeeklyEntry, kpi: Kpi): number {
  return entry[kpi.key];
}

function KpiSection({ kpi, entries }: { kpi: Kpi; entries: WeeklyEntry[] }) {
  const isRate = kpi.key === "discrepancy_rate";
  const largest = Math.max(0, ...entries.map((entry) => valueOf(entry, kpi)));
  const totals = isRate ? [] : warehouseTotals(entries, kpi.key);
  return (
    <section className="inventory-table-section analysis-panel" aria-label={kpi.name}>
      <h2>{kpi.name}</h2>
      <p>{kpi.meaning}</p>
      <p className="incident-muted">{kpi.useFor}</p>
      <div className="inventory-table-scroll">
        <table className="inventory-table">
          <thead>
            <tr><th scope="col">Warehouse</th><th scope="col">Client</th><th scope="col">{isRate ? "Discrepancy Rate" : kpi.name}</th><th scope="col">{isRate ? "Detail" : "Unit"}</th></tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={`${entry.warehouse}/${entry.client_id}`}>
                <td>{warehouseName(entry.warehouse)}</td>
                <td>{clientName(entry.client_id)}</td>
                <td className="inventory-number">
                  <strong>{isRate ? percent(entry.discrepancy_rate) : valueOf(entry, kpi).toLocaleString("en-US")}</strong>
                  {largest > 0 && <span className="analysis-bar" aria-hidden="true"><span style={{ width: `${(valueOf(entry, kpi) / largest) * 100}%` }} /></span>}
                </td>
                <td>
                  {isRate
                    ? entry.outbound_orders_count === 0
                      ? `${entry.discrepancy_events_count} stock count difference${entry.discrepancy_events_count === 1 ? "" : "s"}, no orders dispatched`
                      : `${entry.discrepancy_events_count} stock count difference${entry.discrepancy_events_count === 1 ? "" : "s"} in ${entry.outbound_orders_count.toLocaleString("en-US")} dispatched order${entry.outbound_orders_count === 1 ? "" : "s"}`
                    : kpi.unit}
                </td>
              </tr>
            ))}
          </tbody>
          {totals.length > 0 && entries.length > totals.length && (
            <tfoot>
              {totals.map(({ warehouse, total }) => (
                <tr key={warehouse}><th scope="row" colSpan={2}>{warehouseName(warehouse)}, all clients</th><td className="inventory-number"><strong>{total.toLocaleString("en-US")}</strong></td><td>{kpi.unit}</td></tr>
              ))}
            </tfoot>
          )}
        </table>
      </div>
    </section>
  );
}

export default function WeeklyPerformanceReportPage() {
  const [week, setWeek] = useState<string | undefined>(undefined);
  const [report, setReport] = useState<WeeklyPerformance | null>(null);
  const [run, setRun] = useState<LatestRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [notReady, setNotReady] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (requested?: string) => {
    const token = getAccessToken();
    if (!token) return;
    setLoading(true);
    setError("");
    setNotReady(false);
    try {
      setReport(await reportingApi.weekly(token, requested));
      setRun(await reportingApi.latestRun(token).catch(() => null));
    } catch (caught) {
      setReport(null);
      if (caught instanceof ApiError && caught.status === 404) setNotReady(true);
      else setError(`The report could not be loaded. ${messageFor(caught, GENERIC_ERROR_MESSAGE)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(week);
  }, [load, week]);

  const entries = report?.entries ?? [];
  const sorted = useMemo(() => [...entries].sort((a, b) => a.warehouse.localeCompare(b.warehouse) || a.client_id.localeCompare(b.client_id)), [entries]);
  const shownWeek = report?.week_start ?? week;
  const refreshed = run?.finished_at ? formatRefreshed(run.finished_at) : "";
  const unreliable = run !== null && (run.status === "failed" || run.optional_steps_failed > 0);

  return (
    <>
      <header className="inventory-page-header">
        <div>
          <p className="inventory-eyebrow">REPORTS</p>
          <h1>Weekly Warehouse &amp; Client Performance Report</h1>
          <p>How each warehouse performed for each client during the week: what came in, what went out, what ran low, and how accurate the stock is.</p>
        </div>
      </header>

      <section className="inventory-form-section">
        <div className="analysis-period">
          <button className="inventory-secondary-button" type="button" disabled={loading || !shownWeek} onClick={() => shownWeek && setWeek(shiftWeek(shownWeek, -1))}>← Previous week</button>
          <button className="inventory-secondary-button" type="button" disabled={loading || !shownWeek} onClick={() => shownWeek && setWeek(shiftWeek(shownWeek, 1))}>Next week →</button>
          <button className="inventory-primary-button" type="button" disabled={loading || week === undefined} onClick={() => setWeek(undefined)}>Most recent week</button>
        </div>
        {shownWeek && <p className="inventory-hint" role="status"><strong>Week shown:</strong> {weekLabel(shownWeek)} (UTC; the week runs Monday to Sunday).</p>}
        {refreshed && <p className="inventory-hint">Numbers last refreshed on {refreshed}. The report is recalculated every Monday morning.</p>}
        {unreliable && (
          <p className="inventory-warning" role="alert">
            The last refresh did not finish cleanly or the system recorded no activity, so these numbers may be out of date or incomplete. Please ask the engineering team to check.
          </p>
        )}
        {loading && <p className="inventory-hint" role="status">Loading the report…</p>}
        {notReady && <p className="inventory-hint" role="status">The first report has not been calculated yet. It is produced automatically every Monday morning.</p>}
        {error && <p className="inventory-alert" role="alert">{error}</p>}
      </section>

      {report && entries.length === 0 && <p className="inventory-hint">No warehouse activity was recorded for this week.</p>}
      {report && entries.length > 0 && (
        <>
          {KPIS.map((kpi) => <KpiSection key={kpi.key} kpi={kpi} entries={sorted} />)}
          <p className="incident-muted">
            Outbound Throughput counts orders dispatched to customers; stock written off as lost is not included. Every row belongs to one client in one warehouse. The figures come from activity recorded in the TrackFlow backoffice.
          </p>
        </>
      )}
    </>
  );
}
