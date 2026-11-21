import type { ReactNode } from "react";
import { InventoryGuard } from "../inventory/InventoryGuard";
import { InventoryShell } from "../inventory/InventoryShell";

export default function IncidentsLayout({ children }: { children: ReactNode }) {
  return <InventoryGuard><InventoryShell>{children}</InventoryShell></InventoryGuard>;
}
