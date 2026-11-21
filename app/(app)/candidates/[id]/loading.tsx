export default function CandidateDetailLoading() {
  return (
    <main className="page-shell detail-shell">
      <p className="back-link">Back to candidates</p>
      <section className="page-header">
        <p className="eyebrow">Candidate detail</p>
        <h1>Loading candidate...</h1>
        <p className="lede">Fetching the latest candidate data.</p>
      </section>
      <section className="list-panel detail-panel">
        <p className="empty-state">Loading candidate details...</p>
      </section>
    </main>
  );
}