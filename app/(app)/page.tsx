import { Suspense } from "react";
import CandidateList from "../components/CandidateList";

export default function CandidateListPage() {
  return (
    <main className="page-shell">
      <section className="page-header">
        <p className="eyebrow">Talent pipeline</p>
        <h1>Candidates</h1>
        <p className="lede">Review everyone currently registered in the recruiting pipeline.</p>
      </section>

      <Suspense fallback={<p className="empty-state">Loading candidates...</p>}>
        <CandidateList />
      </Suspense>
    </main>
  );
}