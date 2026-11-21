import type { ReactNode } from "react";
import { AuthGuard } from "../../components/AuthGuard";
import { InventoryShell } from "./InventoryShell";

export default function InventoryLayout({ children }: { children: ReactNode }) {
  return <AuthGuard><InventoryShell>{children}</InventoryShell></AuthGuard>;
}
