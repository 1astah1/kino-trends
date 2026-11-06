// Источник: retentionos/packages/ui/components/empty-state.tsx (скопировано 26.09.2026).
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
  className?: string;
}

export function EmptyState({ icon: Icon, title, description, action, compact, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border text-center",
        compact ? "py-8" : "py-14",
        className,
      )}
    >
      {Icon ? (
        <div className="flex h-9 w-9 items-center justify-center rounded-md border border-border bg-surface-2">
          <Icon className="h-4 w-4 text-fg-muted" aria-hidden="true" />
        </div>
      ) : null}
      <div className="flex flex-col gap-1">
        <p className="text-sm font-medium text-fg">{title}</p>
        {description ? <div className="max-w-md text-sm text-fg-muted">{description}</div> : null}
      </div>
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}
