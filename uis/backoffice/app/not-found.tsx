import Link from "next/link";

export default function BackofficeNotFound() {
  return (
    <main className="login-page">
      <section className="login-panel" aria-labelledby="not-found-title">
        <p className="inventory-eyebrow">TRACKFLOW · OPERATIONS</p>
        <h1 id="not-found-title">Page not found</h1>
        <p className="login-subtitle">The page you are looking for does not exist or has moved.</p>
        <Link className="inventory-primary-button incident-link-button" href="/">Back to the operations desk</Link>
      </section>
    </main>
  );
}
