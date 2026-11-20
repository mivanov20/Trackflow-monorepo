"use client";

import { useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { getAccessToken } from "../../../lib/auth";

export function InventoryGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [authorized, setAuthorized] = useState(false);

  useEffect(() => {
    if (!getAccessToken()) {
      router.replace(`/backoffice/login?next=${encodeURIComponent(pathname)}`);
      return;
    }
    setAuthorized(true);
  }, [pathname, router]);

  if (!authorized) return <main className="inventory-loading" role="status">Checking your session…</main>;
  return children;
}