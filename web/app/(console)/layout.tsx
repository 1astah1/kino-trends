import type { ReactNode } from "react";

import { AuthGate } from "@/components/auth/auth-gate";
import { PageSuspense } from "@/components/common/page-suspense";
import { AppShell } from "@/components/layout/app-shell";
import { ShellSkeleton } from "@/components/layout/shell-skeleton";

/** ConsoleLayout → AuthGate → AppShell (§7.5). AuthGate читает `useSearchParams` → Suspense. */
export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <PageSuspense fallback={<ShellSkeleton />}>
      <AuthGate>
        <AppShell>{children}</AppShell>
      </AuthGate>
    </PageSuspense>
  );
}
