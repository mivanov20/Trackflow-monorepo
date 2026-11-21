import type { ReactNode } from "react";
import AccountBar from "../../components/AccountBar";
import AuthGuard from "../../components/AuthGuard";

/** Todo lo que cuelga de este grupo exige una sesión; las páginas de acceso viven en el grupo (auth). */
export default function ProtectedLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGuard>
      <AccountBar />
      {children}
    </AuthGuard>
  );
}
