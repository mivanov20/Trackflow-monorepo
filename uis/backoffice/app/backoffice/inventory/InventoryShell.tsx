"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { clearSession, getCurrentUser } from "../../../lib/auth";

export function InventoryShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const user = getCurrentUser();

  function signOut() {
    clearSession();
    router.replace("/backoffice/login");
  }

  return (
    <div className="inventory-workspace">
      <aside className="inventory-sidebar" aria-label="Inventory navigation">
        <Link className="inventory-brand" href="/">TrackFlow <small>WAREHOUSE OPERATIONS</small></Link>
        <nav aria-label="Inventory">
          <Link href="/backoffice/inventory/products">Products</Link>
          <Link href="/backoffice/inventory/orders/inbound">Inbound order</Link>
          <Link href="/backoffice/inventory/orders/outbound">Outbound order</Link>
          <Link href="/backoffice/inventory/orders">Orders history</Link>
        </nav>
        <nav aria-label="Incidents" className="incident-nav">
          <Link href="/backoffice/incidents">Incidents</Link>
          <Link href="/backoffice/incidents/new">Report incident</Link>
        </nav>
        <div className="inventory-session">
          <span>{user?.name ?? "Signed-in operative"}</span>
          <button type="button" onClick={signOut}>Sign out</button>
        </div>
      </aside>
      <main className="inventory-main">{children}</main>
    </div>
  );
}