"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { clearSession, getCurrentUser, type AuthUser } from "../../../lib/auth";
import { SESSION_UPDATED_EVENT } from "../../../lib/session";

export function InventoryShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);

  // El nombre mostrado se actualiza cuando el perfil cambia (evento de sesión) sin recargar la página.
  useEffect(() => {
    const refresh = () => setUser(getCurrentUser());
    refresh();
    window.addEventListener(SESSION_UPDATED_EVENT, refresh);
    return () => window.removeEventListener(SESSION_UPDATED_EVENT, refresh);
  }, []);

  function signOut() {
    clearSession();
    router.replace("/login");
  }

  return (
    <div className="inventory-workspace">
      <aside className="inventory-sidebar" aria-label="Inventory navigation">
        <Link className="inventory-brand" href="/">TrackFlow <small>WAREHOUSE OPERATIONS</small></Link>
        <nav aria-label="Inventory">
          <Link href="/backoffice/inventory/products">Products</Link>
          <Link href="/backoffice/inventory/orders/inbound">Inbound order</Link>
          <Link href="/backoffice/inventory/orders/outbound">Outbound order</Link>
          <Link href="/backoffice/inventory/stock-count">Stock count</Link>
          <Link href="/backoffice/inventory/orders">Orders history</Link>
        </nav>
        <nav aria-label="Incidents" className="incident-nav">
          <Link href="/backoffice/incidents">Incidents</Link>
          <Link href="/backoffice/incidents/new">Report incident</Link>
          <Link href="/backoffice/incidents/analyze">Analyze file</Link>
        </nav>
        <nav aria-label="Account" className="incident-nav">
          <Link href="/account/profile">My account</Link>
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
