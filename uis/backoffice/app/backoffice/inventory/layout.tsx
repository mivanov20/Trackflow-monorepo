import type { ReactNode } from "react";
import { InventoryGuard } from "./InventoryGuard";
import { InventoryShell } from "./InventoryShell";

export default function InventoryLayout({ children }: { children: ReactNode }) {
  return <InventoryGuard><InventoryShell>{children}</InventoryShell></InventoryGuard>;
}