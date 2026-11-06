// Источник: retentionos/packages/ui/components/stat-tile.tsx (скопировано 26.09.2026).
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

import { Card, CardContent } from "./card";

export interface StatTileProps {
  label: string;
  value: ReactNode;
  icon?: LucideIcon;
  badge?: ReactNode;
  hint?: ReactNode;
  size?: "sm" | "lg";
  className?: string;
}

export function StatTile({ label, value, icon: Icon, badge, hint, size = "sm", className }: StatTileProps) {
  return (
    <Card className={className}>
      <CardContent className="flex flex-col gap-1">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-medium text-fg-muted">{label}</span>
          {Icon ? <Icon className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" /> : null}
        </div>
        <div className="flex items-end justify-between gap-2">
          <span className={cn("tabular font-semibold text-fg", size === "lg" ? "text-2xl" : "text-lg")}>{value}</span>
          {badge}
        </div>
        {hint ? <span className="text-xs text-fg-muted">{hint}</span> : null}
      </CardContent>
    </Card>
  );
}

export function StatTileGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("grid grid-cols-2 gap-3 sm:grid-cols-4", className)}>{children}</div>;
}
