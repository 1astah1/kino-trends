// Источник: retentionos/packages/ui/components/key-value.tsx (скопировано 26.09.2026).
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export function KeyValue({ label, value, className }: { label: string; value: ReactNode; className?: string }) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-0.5", className)}>
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="truncate text-sm text-fg">{value}</dd>
    </div>
  );
}

export function DetailGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <dl className={cn("grid grid-cols-2 gap-3 sm:grid-cols-4", className)}>{children}</dl>;
}
