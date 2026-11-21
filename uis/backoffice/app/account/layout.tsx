import type { ReactNode } from "react";
import { AuthGuard } from "../components/AuthGuard";
import { InventoryShell } from "../backoffice/inventory/InventoryShell";

export default function AccountLayout({ children }: { children: ReactNode }) {
  return <AuthGuard><InventoryShell>{children}</InventoryShell></AuthGuard>;
}
