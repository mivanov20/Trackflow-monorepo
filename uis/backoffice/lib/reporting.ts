import { apiRequest } from "./api-client";

/** One warehouse and client for one week: the contract of `GET /reporting/weekly-warehouse-client-performance`. */
export interface WeeklyEntry {
  warehouse: string;
  client_id: string;
  inbound_units_count: number;
  outbound_orders_count: number;
  stockout_events_count: number;
  discrepancy_events_count: number;
  discrepancy_rate: number;
}

export interface WeeklyPerformance {
  week_start: string;
  entries: WeeklyEntry[];
}

/** What the page needs of `GET /reporting/pipeline-runs/latest`: when the numbers were refreshed and whether it went well. */
export interface LatestRun {
  status: "running" | "completed" | "failed";
  finished_at: string | null;
  optional_steps_failed: number;
}

/** The four KPIs of the company CONTEXT ("KPIs to Measure"), with the same names and meanings. */
export const KPIS = [
  {
    key: "inbound_units_count",
    name: "Inbound Volume",
    meaning: "How many units of a client's goods a warehouse received during the week.",
    useFor: "Shows the incoming workload for each warehouse and client, the basis for capacity planning.",
    unit: "units received",
  },
  {
    key: "outbound_orders_count",
    name: "Outbound Throughput",
    meaning: "How many orders a warehouse picked and dispatched for a client during the week.",
    useFor: "Shows how much a warehouse is actually able to move, not just receive.",
    unit: "orders dispatched",
  },
  {
    key: "stockout_events_count",
    name: "Stockout Frequency",
    meaning: "How many times during the week a client's product at a warehouse fell below its minimum stock.",
    useFor: "An early warning before a client runs out of stock.",
    unit: "times below minimum",
  },
  {
    key: "discrepancy_rate",
    name: "Discrepancy Rate",
    meaning: "The share of the week's dispatched orders associated with a detected stock count difference.",
    useFor: "Shows which warehouse and client combinations need a stock audit.",
    unit: "of dispatched orders",
  },
] as const;

export const reportingApi = {
  /** The KPI rows of one week; with no week, the most recent computed one. A 404 means nothing has been computed yet. */
  weekly(token: string, weekStart?: string): Promise<WeeklyPerformance> {
    const query = weekStart ? `?week_start=${encodeURIComponent(weekStart)}` : "";
    return apiRequest<WeeklyPerformance>(`/reporting/weekly-warehouse-client-performance${query}`, { token, timeoutMs: 20_000, serviceName: "reporting" });
  },
  latestRun(token: string): Promise<LatestRun> {
    return apiRequest<LatestRun>("/reporting/pipeline-runs/latest", { token, timeoutMs: 20_000, serviceName: "reporting" });
  },
};

const DAY_MS = 24 * 60 * 60 * 1000;

function utcDate(day: string): Date {
  return new Date(`${day}T00:00:00Z`);
}

function iso(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** The Monday `weeks` weeks after (or before, if negative) the given Monday, as YYYY-MM-DD. */
export function shiftWeek(weekStart: string, weeks: number): string {
  return iso(new Date(utcDate(weekStart).getTime() + weeks * 7 * DAY_MS));
}

function longDate(date: Date): string {
  return date.toLocaleDateString("en-US", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

/** "Monday, July 13, 2026 to Sunday, July 19, 2026": the period a week covers, in plain words. */
export function weekLabel(weekStart: string): string {
  const monday = utcDate(weekStart);
  const sunday = new Date(monday.getTime() + 6 * DAY_MS);
  return `${longDate(monday)} to ${longDate(sunday)}`;
}

/** `fashion-co` becomes `Fashion Co`: the brand as a person reads it. */
export function clientName(clientId: string): string {
  return clientId.split("-").filter(Boolean).map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

export function warehouseName(warehouse: string): string {
  return warehouse === "los_angeles" ? "Los Angeles" : warehouse === "zaragoza" ? "Zaragoza" : warehouse;
}

/** 0.25 becomes "25%"; a small rate keeps a decimal ("0.2%") so it does not read as zero. */
export function percent(rate: number): string {
  const value = rate * 100;
  return `${value >= 10 || Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)}%`;
}

/** The sum of one column per warehouse (a warehouse total across its clients, shown apart from the client rows). */
export function warehouseTotals(entries: WeeklyEntry[], column: "inbound_units_count" | "outbound_orders_count" | "stockout_events_count"): { warehouse: string; total: number }[] {
  const totals = new Map<string, number>();
  for (const entry of entries) totals.set(entry.warehouse, (totals.get(entry.warehouse) ?? 0) + entry[column]);
  return [...totals].map(([warehouse, total]) => ({ warehouse, total })).sort((a, b) => a.warehouse.localeCompare(b.warehouse));
}
