// Источник: retentionos/packages/ui/components/callout.tsx (скопировано 26.09.2026).
import { Info, OctagonAlert, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export interface CalloutProps {
  variant?: "warning" | "info" | "negative";
  title?: string;
  children?: ReactNode;
  className?: string;
  action?: ReactNode;
}

const VARIANT_STYLES = {
  warning: { box: "border-warning/40 bg-warning/10", icon: "text-warning", Icon: TriangleAlert },
  info: { box: "border-accent/40 bg-accent/10", icon: "text-accent", Icon: Info },
  negative: { box: "border-negative/50 bg-negative/10", icon: "text-negative", Icon: OctagonAlert },
} as const;

export function Callout({ variant = "info", title, children, className, action }: CalloutProps) {
  const styles = VARIANT_STYLES[variant];
  const Icon = styles.Icon;
  return (
    <div
      role={variant === "info" ? "note" : "alert"}
      className={cn("flex items-start gap-2 rounded-md border p-3", styles.box, className)}
    >
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", styles.icon)} aria-hidden="true" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5 text-sm">
        {title ? <p className="font-medium text-fg">{title}</p> : null}
        {children ? <div className="text-fg-muted">{children}</div> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}
