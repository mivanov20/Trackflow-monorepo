"use client";

import Link from "next/link";

/** Límite de error de la app: nunca muestra el error técnico, solo una salida clara. */
export default function BackofficeError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="login-page">
      <section className="login-panel" aria-labelledby="error-title" role="alert">
        <p className="inventory-eyebrow">TRACKFLOW · OPERATIONS</p>
        <h1 id="error-title">This page could not be displayed</h1>
        <p className="login-subtitle">Something unexpected happened. Your data has not been changed. Try again, or go back to the operations desk.</p>
        <button className="inventory-primary-button" type="button" onClick={reset}>Try again</button>
        <p className="login-subtitle"><Link href="/">Back to the operations desk</Link> · If the problem continues, contact your TrackFlow administrator.</p>
      </section>
    </main>
  );
}
