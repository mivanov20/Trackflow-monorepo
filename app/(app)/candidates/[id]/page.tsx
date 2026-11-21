import Link from "next/link";
import { notFound } from "next/navigation";
import CandidateDetail from "../../../components/CandidateDetail";
import {
  CandidateApiError,
  getCandidate,
} from "../../../lib/candidates";

interface CandidateDetailPageProps {
  params: Promise<{ id: string }>;
}

export default async function CandidateDetailPage({ params }: CandidateDetailPageProps) {
  const { id } = await params;
  let candidate;

  try {
    candidate = await getCandidate(id);
  } catch (error) {
    if (error instanceof CandidateApiError && error.status === 404) notFound();
    throw error;
  }

  return (
    <main className="page-shell detail-shell">
      <Link className="back-link" href="/">
        Back to candidates
      </Link>
      <CandidateDetail initialCandidate={candidate} />
    </main>
  );
}