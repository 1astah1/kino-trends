// Источник: retentionos/packages/ui/components/section-card.tsx (скопировано 26.09.2026).
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

import { Card } from "./card";

export function SectionCard({
  title,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <Card className={cn("flex flex-col", className)}>
      <div className="flex h-9 items-center justify-between gap-2 border-b border-border px-3">
        <h2 className="text-xs font-semibold tracking-wide text-fg-muted uppercase">{title}</h2>
        {actions}
      </div>
      <div className={cn("flex flex-col gap-3 p-3", bodyClassName)}>{children}</div>
    </Card>
  );
}
