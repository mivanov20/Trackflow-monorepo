"use client";

import { ChangeEvent, DragEvent, KeyboardEvent, useRef, useState } from "react";
import { getAccessToken } from "../../../../lib/auth";
import {
  CATEGORY_NAMES,
  COUNTRY_NAMES,
  EXPORT_FILENAME,
  STATUS_NAMES,
  analysisApi,
  analysisErrorMessage,
  downloadTextFile,
  fileProblem,
  formatFileSize,
  type AnalysisSummary,
  type Breakdown,
  type SatisfactionGroup,
} from "../../../../lib/incident-analysis";

type Phase = "idle" | "uploading" | "done" | "error";

const LINES_SHOWN = 20;

function BreakdownTable({ title, rows, names }: { title: string; rows: Breakdown[]; names: Record<string, string> }) {
  return (
    <section className="inventory-table-section analysis-panel" aria-label={title}>
      <h3>{title}</h3>
      <table className="inventory-table">
        <thead><tr><th scope="col">Value</th><th scope="col">Records</th><th scope="col">Share</th><th scope="col"><span className="visually-hidden">Bar</span></th></tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.value}>
              <td><strong>{names[row.value] ?? row.value}</strong> <span className="inventory-code">{row.value}</span></td>
              <td className="inventory-number">{row.count}</td>
              <td className="inventory-number">{row.percent.toFixed(1)}%</td>
              <td className="analysis-bar-cell"><span className="analysis-bar" aria-hidden="true"><span style={{ width: `${Math.min(100, row.percent)}%` }} /></span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function SatisfactionTable({ title, rows, names }: { title: string; rows: SatisfactionGroup[]; names: Record<string, string> }) {
  return (
    <table className="inventory-table analysis-satisfaction-table" aria-label={title}>
      <thead><tr><th scope="col">{title}</th><th scope="col">Average</th><th scope="col">Scored</th></tr></thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.value}>
            <td><strong>{names[row.value] ?? row.value}</strong> <span className="inventory-code">{row.value}</span></td>
            <td className="inventory-number">{row.average === null ? "n/a" : `${row.average.toFixed(2)} / 5`}</td>
            <td className="inventory-number">{row.scored}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Results({ summary, onDownload, downloading, downloadError }: { summary: AnalysisSummary; onDownload: () => void; downloading: boolean; downloadError: string }) {
  const { satisfaction } = summary;
  const flagged = summary.invalid_breakdown.filter((rule) => rule.count > 0);
  const ruleTotal = flagged.reduce((sum, rule) => sum + rule.count, 0);
  const shownLines = summary.invalid_examples.slice(0, LINES_SHOWN);
  const maxScore = Math.max(1, ...satisfaction.distribution.map((item) => item.count));

  return (
    <div className="analysis-results" aria-live="polite">
      <div className="incident-section-title">
        <h2>Results for {summary.source_file || "your file"}</h2>
        <button className="inventory-primary-button" type="button" onClick={onDownload} disabled={downloading}>
          {downloading ? "Preparing…" : "Download results (CSV)"}
        </button>
      </div>
      {downloadError && <p className="inventory-alert" role="alert">{downloadError}</p>}

      <div className="analysis-cards">
        <article className="analysis-card"><span>Total records</span><strong>{summary.total_records}</strong></article>
        <article className="analysis-card"><span>Valid records</span><strong>{summary.valid_records}</strong></article>
        <article className={`analysis-card${summary.invalid_records > 0 ? " analysis-card-warn" : ""}`}><span>Invalid / incomplete</span><strong>{summary.invalid_records}</strong></article>
        <article className="analysis-card"><span>Average satisfaction</span><strong>{satisfaction.average === null ? "n/a" : `${satisfaction.average.toFixed(2)} / 5`}</strong></article>
      </div>

      {summary.invalid_records === 0 ? (
        <p className="inventory-success" role="status">Every record in the file is valid, so all of them are in the analysis below.</p>
      ) : (
        <section className="inventory-warning analysis-invalid" aria-labelledby="invalid-heading">
          <h3 id="invalid-heading">{summary.invalid_records} of {summary.total_records} records were left out of the analysis</h3>
          <p>They are missing information or have a value that is not allowed. They are counted here but not in the breakdowns below, and no customer details are shown.</p>
          <table className="inventory-table">
            <thead><tr><th scope="col">Problem</th><th scope="col">Records</th><th scope="col">What is wrong</th></tr></thead>
            <tbody>
              {flagged.map((rule) => (
                <tr key={rule.rule}><td><strong>{rule.label}</strong></td><td className="inventory-number">{rule.count}</td><td>{rule.description}</td></tr>
              ))}
            </tbody>
          </table>
          {ruleTotal > summary.invalid_records && <p className="incident-muted">A record can have several problems, so the counts above add up to more than {summary.invalid_records}.</p>}
          <p className="incident-muted">
            Lines to review in the file: {shownLines.map((item) => item.line).join(", ")}
            {summary.invalid_records > shownLines.length ? ` and ${summary.invalid_records - shownLines.length} more` : ""}.
          </p>
        </section>
      )}

      {summary.warnings.map((warning) => <p key={warning} className="inventory-warning" role="status">{warning}</p>)}

      <div className="analysis-grid">
        <BreakdownTable title="By category (valid records)" rows={summary.by_category} names={CATEGORY_NAMES} />
        <BreakdownTable title="By status (valid records)" rows={summary.by_status} names={STATUS_NAMES} />
        <BreakdownTable title="By country (valid records)" rows={summary.by_country} names={COUNTRY_NAMES} />
      </div>

      <section className="inventory-table-section analysis-panel" aria-labelledby="satisfaction-heading">
        <h3 id="satisfaction-heading">Satisfaction index (closed incidents)</h3>
        <p className="incident-muted">Scored incidents: {satisfaction.scored} of {satisfaction.closed_valid}.</p>
        <ul className="analysis-scores">
          {satisfaction.distribution.map((item) => (
            <li key={item.score}>
              <span>Score {item.score} <small>({item.label})</small></span>
              <span className="analysis-bar" aria-hidden="true"><span style={{ width: `${(item.count / maxScore) * 100}%` }} /></span>
              <strong>{item.count}</strong>
            </li>
          ))}
        </ul>
        <div className="analysis-grid">
          <SatisfactionTable title="By country" rows={satisfaction.by_country} names={COUNTRY_NAMES} />
          <SatisfactionTable title="By category" rows={satisfaction.by_category} names={CATEGORY_NAMES} />
        </div>
      </section>
    </div>
  );
}

export default function AnalyzeIncidentsPage() {
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");
  const [summary, setSummary] = useState<AnalysisSummary | null>(null);
  const [dragging, setDragging] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  function choose(candidate: File | undefined) {
    if (!candidate) return;
    const problem = fileProblem(candidate);
    setSummary(null);
    setDownloadError("");
    if (problem) {
      setFile(null);
      setError(problem);
      setPhase("error");
      return;
    }
    setFile(candidate);
    setError("");
    setPhase("idle");
  }

  function onInputChange(event: ChangeEvent<HTMLInputElement>) {
    choose(event.target.files?.[0]);
    event.target.value = ""; // permite volver a elegir el mismo archivo
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (phase === "uploading") return;
    choose(event.dataTransfer.files?.[0]);
  }

  function onZoneKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      inputRef.current?.click();
    }
  }

  async function analyze() {
    const token = getAccessToken();
    if (!file || !token || phase === "uploading") return;
    setPhase("uploading");
    setError("");
    setSummary(null);
    setDownloadError("");
    try {
      setSummary(await analysisApi.analyze(token, file));
      setPhase("done");
    } catch (caught) {
      setError(analysisErrorMessage(caught));
      setPhase("error");
    }
  }

  async function download() {
    const token = getAccessToken();
    if (!token || downloading) return;
    setDownloading(true);
    setDownloadError("");
    try {
      downloadTextFile(EXPORT_FILENAME, await analysisApi.exportResults(token));
    } catch (caught) {
      setDownloadError(`The results could not be downloaded. ${analysisErrorMessage(caught)}`);
    } finally {
      setDownloading(false);
    }
  }

  const busy = phase === "uploading";

  return (
    <>
      <header className="inventory-page-header">
        <div>
          <p className="inventory-eyebrow">OPERATIONS / INCIDENTS</p>
          <h1>Analyze incident file</h1>
          <p>Upload the incidents CSV to see volume, quality and satisfaction. The file is analysed on the TrackFlow server and is not kept; customer emails are never shown or exported.</p>
        </div>
      </header>

      <section className="inventory-table-section" aria-labelledby="upload-heading">
        <h2 id="upload-heading" className="visually-hidden">Upload a file</h2>
        <div
          className={`analysis-dropzone${dragging ? " is-dragging" : ""}${busy ? " is-busy" : ""}`}
          role="button"
          tabIndex={0}
          aria-label="Choose an incidents CSV file, or drop one here"
          aria-disabled={busy}
          onClick={() => !busy && inputRef.current?.click()}
          onKeyDown={onZoneKey}
          onDragOver={(event) => { event.preventDefault(); if (!busy) setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <p><strong>Drag a CSV file here</strong> or <span className="analysis-link">choose a file</span></p>
          <p className="incident-muted">UTF-8, comma separated, one incident per row (for example incidents.csv)</p>
          <input ref={inputRef} className="visually-hidden" type="file" accept=".csv,text/csv" tabIndex={-1} onChange={onInputChange} disabled={busy} data-testid="file-input" />
        </div>

        {file && (
          <p className="analysis-selected">
            <strong>{file.name}</strong> <span className="incident-muted">{formatFileSize(file.size)}</span>
          </p>
        )}

        <div className="analysis-actions">
          <button className="inventory-primary-button" type="button" onClick={() => void analyze()} disabled={!file || busy}>
            {busy ? "Analysing…" : "Analyze file"}
          </button>
          {file && !busy && <button className="inventory-secondary-button" type="button" onClick={() => { setFile(null); setSummary(null); setPhase("idle"); setError(""); }}>Remove file</button>}
        </div>

        {busy && <p className="incident-state" role="status"><span className="incident-spinner incident-spinner-dark" aria-hidden="true" /> Analysing {file?.name}… large files can take a little while.</p>}
        {phase === "error" && (
          <p className="inventory-alert" role="alert">
            {error} {file && <button className="inventory-secondary-button" type="button" onClick={() => void analyze()}>Try again</button>}
          </p>
        )}
      </section>

      {summary && phase === "done" && <Results summary={summary} onDownload={() => void download()} downloading={downloading} downloadError={downloadError} />}
    </>
  );
}
