"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ChangeEvent, useEffect, useState, useTransition } from "react";
import { candidateErrorMessage, candidateName, candidatePosition, createCandidate, getCandidates, humanize } from "../lib/candidates";
import { CANDIDATE_STAGES, CANDIDATE_STATUSES, Candidate, CandidatePayload } from "../types/candidates";
import CandidateForm from "./CandidateForm";

export default function CandidateList() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const status = searchParams.get("status") || "";
  const stage = searchParams.get("stage") || "";
  const search = searchParams.get("search") || "";
  const [searchInput, setSearchInput] = useState(search);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [fetchStatus, setFetchStatus] = useState<"loading" | "success" | "error">("loading");
  const [error, setError] = useState("");
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [formKey, setFormKey] = useState(0);
  const [formFeedback, setFormFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const [, startTransition] = useTransition();

  useEffect(() => {
    setSearchInput(search);
  }, [search]);

  useEffect(() => {
    const controller = new AbortController();

    async function loadCandidates() {
      setFetchStatus("loading");
      setError("");
      let outcome: "success" | "error" = "error";

      try {
        const records = await getCandidates({ status, stage, search });
        if (!controller.signal.aborted) setCandidates(records);
        outcome = "success";
      } catch (loadError) {
        if (!controller.signal.aborted) {
          setCandidates([]);
          setError(candidateErrorMessage(loadError, "We couldn't load candidates."));
        }
      } finally {
        // El estado de carga se cierra siempre aquí; una petición cancelada no pisa a la nueva.
        if (!controller.signal.aborted) setFetchStatus(outcome);
      }
    }

    loadCandidates();
    return () => controller.abort();
  }, [status, stage, search, refreshToken]);

  function setParam(name: string, value: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set(name, value);
    else params.delete(name);

    startTransition(() => {
      router.replace(`${pathname}${params.toString() ? `?${params.toString()}` : ""}`, { scroll: false });
    });
  }

  function handleSearchChange(event: ChangeEvent<HTMLInputElement>) {
    const value = event.target.value;
    setSearchInput(value);
    setParam("search", value);
  }

  async function registerCandidate(payload: CandidatePayload) {
    setFormFeedback(null);

    try {
      await createCandidate(payload);
      setFormFeedback({ type: "success", message: "Candidate registered successfully." });
      setShowCreateForm(false);
      setFormKey((current) => current + 1);
      setRefreshToken((current) => current + 1);
    } catch (submitError) {
      setFormFeedback({
        type: "error",
        message: candidateErrorMessage(submitError, "Candidate could not be registered."),
      });
    }
  }

  const isLoading = fetchStatus === "loading";
  const isSuccess = fetchStatus === "success";
  const isError = fetchStatus === "error";

  return (
    <section className="list-panel" aria-labelledby="candidate-list-heading">
      <div className="panel-heading">
        <h2 id="candidate-list-heading">Candidate list</h2>
        <span>{isLoading ? "Loading..." : isError ? "Unavailable" : `${candidates.length} total`}</span>
      </div>

      <div className="management-bar">
        <button type="button" onClick={() => setShowCreateForm((current) => !current)}>
          {showCreateForm ? "Close form" : "Register candidate"}
        </button>
        {formFeedback ? <p className={formFeedback.type === "success" ? "success-state" : "inline-error"}>{formFeedback.message}</p> : null}
      </div>

      {showCreateForm ? (
        <div className="form-panel">
          <CandidateForm key={formKey} submitLabel="Register candidate" busyLabel="Registering..." onSubmit={registerCandidate} />
        </div>
      ) : null}

      <div className="filters" aria-label="Candidate filters">
        <label>
          <span>Search by name or email</span>
          <input
            type="search"
            value={searchInput}
            onChange={handleSearchChange}
            placeholder="Name or email"
            aria-label="Search by candidate name or email"
          />
        </label>
        <label>
          <span>Status</span>
          <select value={status} onChange={(event) => setParam("status", event.target.value)}>
            <option value="">All statuses</option>
            {CANDIDATE_STATUSES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Stage</span>
          <select value={stage} onChange={(event) => setParam("stage", event.target.value)}>
            <option value="">All stages</option>
            {CANDIDATE_STAGES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {isLoading ? <p className="empty-state">Loading candidates...</p> : null}
      {isError ? (
        <div className="error-state" role="alert">
          <p>{error}</p>
          <button type="button" onClick={() => setRefreshToken((current) => current + 1)}>
            Try again
          </button>
        </div>
      ) : null}

      {isSuccess && candidates.length ? (
        <ul className="candidate-list">
          {candidates.map((candidate) => (
            <li key={candidate.id}>
              <Link className="candidate-card" href={`/candidates/${candidate.id}`}>
                <dl>
                  <div>
                    <dt>Full name</dt>
                    <dd>{candidateName(candidate)}</dd>
                  </div>
                  <div>
                    <dt>Position applied for</dt>
                    <dd>{candidatePosition(candidate)}</dd>
                  </div>
                  <div>
                    <dt>Current status</dt>
                    <dd>{humanize(candidate.status)}</dd>
                  </div>
                  <div>
                    <dt>Current stage</dt>
                    <dd>{humanize(candidate.stage)}</dd>
                  </div>
                </dl>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}

      {isSuccess && !candidates.length ? <p className="empty-state">No candidates match the current filters.</p> : null}
    </section>
  );
}