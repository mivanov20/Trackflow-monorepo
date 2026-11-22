import type { ReactNode } from "react";
import { AuthGuard } from "../../components/AuthGuard";
import { InventoryShell } from "../inventory/InventoryShell";

export default function RfpLayout({ children }: { children: ReactNode }) {
  return <AuthGuard><InventoryShell>{children}</InventoryShell></AuthGuard>;
}
