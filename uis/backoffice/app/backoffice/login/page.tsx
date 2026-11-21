"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Dirección antigua del login: reenvía a /login conservando ?next= para no romper enlaces guardados. */
export default function LegacyLoginRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace(`/login${window.location.search}`);
  }, [router]);

  return <main className="inventory-loading" role="status">Redirecting to sign in…</main>;
}
