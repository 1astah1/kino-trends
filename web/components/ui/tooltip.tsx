"use client";

// Источник: retentionos/packages/ui/components/tooltip.tsx (скопировано 26.09.2026).
// Показ по наведению и по фокусу, связь через aria-describedby.
import { Info } from "lucide-react";
import { useId, useState } from "react";

import { cn } from "@/lib/cn";

export function InfoTooltip({
  text,
  className,
  srLabel = "Подробнее",
}: {
  text: string;
  className?: string;
  srLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span className={cn("relative inline-flex", className)}>
      <button
        type="button"
        aria-describedby={id}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className="inline-flex text-fg-muted hover:text-fg"
      >
        <Info className="h-3.5 w-3.5" aria-hidden="true" />
        <span className="sr-only">{srLabel}</span>
      </button>
      <span
        id={id}
        role="tooltip"
        className={cn(
          "absolute bottom-full left-1/2 z-20 mb-1.5 w-max max-w-64 -translate-x-1/2 rounded-md border border-border bg-surface px-2 py-1.5 text-xs text-fg",
          open ? "block" : "hidden",
        )}
      >
        {text}
      </span>
    </span>
  );
}
