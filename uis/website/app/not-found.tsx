import Link from "next/link";

export default function WebsiteNotFound() {
  return (
    <div className="application-shell">
      <main className="application-main">
        <section className="application-panel" aria-labelledby="not-found-title">
          <h1 id="not-found-title">Page not found</h1>
          <p className="application-intro">The page you are looking for does not exist or has moved.</p>
          <div className="form-actions">
            <Link className="form-submit" href="/">Back to home</Link>
          </div>
        </section>
      </main>
    </div>
  );
}
