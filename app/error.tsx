"use client";

import Link from "next/link";

/** Límite de error general: muestra una salida clara sin exponer el error técnico. */
export default function AppError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="page-shell">
      <section className="page-header">
        <p className="eyebrow">Something went wrong</p>
        <h1>This page could not be displayed</h1>
        <p className="lede">An unexpected problem occurred. Your data has not been changed.</p>
      </section>
      <section className="list-panel" role="alert">
        <p className="error-state">Try again, or go back to the candidate list. If the problem continues, contact the recruiting team.</p>
        <div className="management-bar">
          <button type="button" onClick={reset}>
            Try again
          </button>
          <Link className="button-link" href="/">
            Back to candidates
          </Link>
        </div>
      </section>
    </main>
  );
}
