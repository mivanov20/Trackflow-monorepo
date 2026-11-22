import { apiRequest } from "./api-client";

export type Row = Record<string, string | number | boolean | null>;

/** Answer of `GET /telemetry/report`: the period it covers and one list of rows per operational metric. */
export interface TelemetryReport {
  period: { from: string; to: string };
  metrics: {
    events_per_day: Row[];
    error_rate_by_day: Row[];
    error_rate_by_type: Row[];
    page_load_latency_by_day: Row[];
    network_failures_by_route: Row[];
    frontend_errors_by_route: Row[];
    auth_failure_rate: Row[];
  };
}

export interface ReportPeriod {
  /** ISO 8601, inclusive. Omit both to get the last 7 days. */
  startDate?: string;
  /** ISO 8601, exclusive. */
  endDate?: string;
}

export const telemetryReportApi = {
  get(token: string, period: ReportPeriod = {}): Promise<TelemetryReport> {
    const query = new URLSearchParams();
    if (period.startDate) query.set("start_date", period.startDate);
    if (period.endDate) query.set("end_date", period.endDate);
    const text = query.toString();
    return apiRequest<TelemetryReport>(`/telemetry/report${text ? `?${text}` : ""}`, { token, timeoutMs: 30_000, serviceName: "telemetry" });
  },
};

/** The report period for two calendar days picked in the page: from the start of the first to the end of the last (UTC). */
export function periodFromDays(fromDay: string, toDay: string): ReportPeriod {
  const end = new Date(`${toDay}T00:00:00Z`);
  end.setUTCDate(end.getUTCDate() + 1);
  return { startDate: `${fromDay}T00:00:00Z`, endDate: end.toISOString() };
}
