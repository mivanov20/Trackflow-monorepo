"use client";

import Link from "next/link";

/** Límite de error del sitio: mensaje legible, reintento y vuelta al inicio. */
export default function WebsiteError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="application-shell">
      <main className="application-main">
        <section className="application-panel" role="alert" aria-labelledby="error-title">
          <h1 id="error-title">This page could not be displayed</h1>
          <p className="application-intro">Something unexpected happened on our side. Please try again, or return to the home page.</p>
          <div className="form-actions">
            <button className="form-submit" type="button" onClick={reset}>Try again</button>
            <Link className="form-clear" href="/">Back to home</Link>
          </div>
        </section>
      </main>
    </div>
  );
}
