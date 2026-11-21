"use client";

import Link from "next/link";

export default function CandidateDetailError({ reset }: { error: Error; reset: () => void }) {
  return (
    <main className="page-shell detail-shell">
      <section className="page-header">
        <p className="eyebrow">Candidate detail</p>
        <h1>Candidate could not be loaded</h1>
        <p className="lede">We couldn&apos;t load this candidate right now.</p>
      </section>
      <section className="list-panel detail-panel">
        <p className="error-state">Try loading the candidate again, or go back to the candidate list.</p>
        <div className="management-bar">
          <button type="button" onClick={reset}>
            Retry
          </button>
          <Link className="button-link" href="/">
            Back to candidates
          </Link>
        </div>
      </section>
    </main>
  );
}