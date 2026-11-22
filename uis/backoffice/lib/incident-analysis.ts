import { ApiError, GENERIC_ERROR_MESSAGE, apiRequest } from "./api-client";
import { EVENT_SCHEMAS } from "./telemetry-schema.generated";
import { newRequestId, track, withRequestId } from "./telemetry";

/** Un valor del desglose (categoría, estado o país) con su recuento y su porcentaje sobre los registros válidos. */
export interface Breakdown {
  value: string;
  count: number;
  percent: number;
}

export interface InvalidRule {
  rule: string;
  label: string;
  description: string;
  count: number;
}

export interface SatisfactionGroup {
  value: string;
  scored: number;
  average: number | null;
}

/** Respuesta de `POST /api/incidents/analyze`: solo agregados y números de línea, nunca datos de clientes. */
export interface AnalysisSummary {
  source_file: string;
  analyzed_at: string;
  total_records: number;
  valid_records: number;
  invalid_records: number;
  invalid_breakdown: InvalidRule[];
  invalid_examples: { line: number; rules: string[] }[];
  invalid_examples_truncated: boolean;
  by_category: Breakdown[];
  by_status: Breakdown[];
  by_country: Breakdown[];
  satisfaction: {
    closed_valid: number;
    scored: number;
    average: number | null;
    distribution: { score: number; label: string; count: number }[];
    by_country: SatisfactionGroup[];
    by_category: SatisfactionGroup[];
  };
  warnings: string[];
}

// Nombres legibles de los códigos del archivo (el código sigue visible junto al nombre).
export const CATEGORY_NAMES: Record<string, string> = {
  LOST_PARCEL: "Lost parcel",
  DELAYED_DELIVERY: "Delayed delivery",
  WRONG_ADDRESS: "Wrong address",
  RETURN_REQUEST: "Return request",
  DAMAGE: "Damage",
};
export const STATUS_NAMES: Record<string, string> = { OPEN: "Open", CLOSED: "Closed", DISCARDED: "Discarded" };
export const COUNTRY_NAMES: Record<string, string> = { US: "United States", ES: "Spain" };

export const EXPORT_FILENAME = "results.csv";

const CSV_MIME = "text/csv;charset=utf-8";
const REQUEST_OPTIONS = { serviceName: "incident" } as const;

/** Estados HTTP cuyo mensaje escribe la API pensando en el usuario; el resto se resume en un texto genérico. */
const USER_FACING_STATUSES = new Set([400, 404, 413, 415, 422]);

/** Mensaje para el usuario ante cualquier fallo del análisis o de la descarga. */
export function analysisErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0 || USER_FACING_STATUSES.has(error.status)) return error.message;
  }
  return GENERIC_ERROR_MESSAGE;
}

/** Revisa en el navegador lo que se puede saber sin subir nada; devuelve el problema o null si el archivo vale. */
export function fileProblem(file: File): string | null {
  if (!file.name.toLowerCase().endsWith(".csv")) return "Only .csv files can be analysed. Choose the incidents CSV export.";
  if (file.size === 0) return "That file is empty. Choose the incidents CSV export.";
  return null;
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Rules the telemetry schema knows; a rule the browser has not heard of would invalidate the whole event. */
const KNOWN_RULES = new Set(Object.keys(EVENT_SCHEMAS.incident_file_analyzed.properties.invalid_by_rule.properties ?? {}));

/** Why a refused file was refused, from the status and the wording of the API's answer. */
function rejectionReason(error: ApiError): string {
  if (error.status === 413) return "too_large";
  if (error.status === 415) return /multipart/i.test(error.message) ? "not_multipart" : "not_csv";
  if (error.status === 400) return /empty/i.test(error.message) ? "empty_file" : "no_file";
  if (/utf-8/i.test(error.message)) return "invalid_encoding";
  if (/column/i.test(error.message)) return "missing_columns";
  if (/header|no data rows/i.test(error.message)) return "no_data_rows";
  return "malformed_csv";
}

export const analysisApi = {
  /** Sube el CSV como multipart/form-data (campo `file`) y devuelve el resumen. */
  async analyze(token: string, file: File): Promise<AnalysisSummary> {
    const body = new FormData();
    body.append("file", file, file.name);
    const requestId = newRequestId();
    const startedAt = performance.now();
    try {
      const summary = await apiRequest<AnalysisSummary>("/api/incidents/analyze", { method: "POST", token, body, requestId, ...REQUEST_OPTIONS });
      // Aggregates only: the file name, its rows and the customer emails never leave the analysis.
      const invalidByRule = Object.fromEntries(summary.invalid_breakdown.filter((rule) => KNOWN_RULES.has(rule.rule)).map((rule) => [rule.rule, rule.count]));
      withRequestId(requestId, () =>
        track("incident_file_analyzed", {
          total_records: summary.total_records,
          valid_records: summary.valid_records,
          invalid_records: summary.invalid_records,
          invalid_by_rule: invalidByRule,
          average_satisfaction: summary.satisfaction.average,
          duration_ms: Math.round(performance.now() - startedAt),
          file_size_bytes: file.size,
        }),
      );
      return summary;
    } catch (caught) {
      if (caught instanceof ApiError && caught.status >= 400 && caught.status < 500 && caught.status !== 401 && caught.status !== 403) {
        withRequestId(requestId, () => track("incident_file_rejected", { rejection_reason: rejectionReason(caught), http_status: caught.status, file_size_bytes: file.size }));
      }
      throw caught;
    }
  },
  /** Texto del CSV del último análisis (`metric,value`, una fila por métrica). */
  exportResults(token: string): Promise<string> {
    return apiRequest<string>("/api/incidents/results/export", { token, as: "text", ...REQUEST_OPTIONS });
  },
};

/** Guarda `text` como archivo en el navegador (descarga). */
export function downloadTextFile(filename: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: CSV_MIME }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
