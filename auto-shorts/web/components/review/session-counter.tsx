"use client";

import { Check, RotateCcw, Undo2, X } from "lucide-react";

import { useReviewPrefs } from "@/lib/store/review-prefs";

/** Счётчик сессии приёмки (§7.5 SessionCounter): сколько решено с момента открытия вкладки. */
export function SessionCounter({ remaining }: { remaining: number }) {
  const session = useReviewPrefs((state) => state.session);
  const decided = session.approved + session.rejected + session.rework;
  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs text-fg-muted tabular"
      data-testid="session-counter"
      data-decided={decided}
      aria-live="polite"
    >
      <span>
        в очереди <span className="text-human">{remaining}</span>
      </span>
      <span className="inline-flex items-center gap-1" title="Одобрено за сессию">
        <Check className="h-3 w-3 text-positive" aria-hidden="true" />
        <span className="sr-only">одобрено</span>
        {session.approved}
      </span>
      <span className="inline-flex items-center gap-1" title="Отклонено за сессию">
        <X className="h-3 w-3 text-negative" aria-hidden="true" />
        <span className="sr-only">отклонено</span>
        {session.rejected}
      </span>
      <span className="inline-flex items-center gap-1" title="На доработку за сессию">
        <RotateCcw className="h-3 w-3 text-warning" aria-hidden="true" />
        <span className="sr-only">на доработку</span>
        {session.rework}
      </span>
      {session.undone ? (
        <span className="inline-flex items-center gap-1" title="Отменено решений">
          <Undo2 className="h-3 w-3" aria-hidden="true" />
          <span className="sr-only">отменено</span>
          {session.undone}
        </span>
      ) : null}
    </div>
  );
}
