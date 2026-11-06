import { Clock, Wallet } from "lucide-react";

import { Badge } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatAgo, formatPct, formatUsd } from "@/lib/format";
import { type Budget, budgetUsage } from "@/lib/queries/budget";
import type { DataFreshness } from "@/lib/queries/overview";

const LEVEL_TEXT = {
  ok: "text-fg",
  warning: "text-warning",
  over: "text-negative",
  paused: "text-negative",
} as const;
const LEVEL_BAR = {
  ok: "bg-accent",
  warning: "bg-warning",
  over: "bg-negative",
  paused: "bg-negative",
} as const;

/** Бюджет дня и свежесть данных одной строкой мелким текстом; предупреждения свежести — списком. */
export function HealthLine({ budget, freshness, now }: { budget: Budget; freshness: DataFreshness; now: number }) {
  const usage = budgetUsage(budget);
  const width = Math.max(0, Math.min(100, usage.pct));
  return (
    <div className="flex flex-col gap-1.5 text-xs text-fg-muted" data-testid="overview-health">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="inline-flex items-center gap-1.5">
          <Wallet className="h-3.5 w-3.5" aria-hidden="true" />
          Бюджет дня:
          <span className={cn("tabular font-mono", LEVEL_TEXT[usage.level])}>
            {formatUsd(usage.used)} / {formatUsd(budget.daily_limit_usd)} · {formatPct(usage.pct)}
          </span>
          <span className="relative h-1.5 w-16 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
            <span className={cn("absolute inset-y-0 left-0 rounded-full", LEVEL_BAR[usage.level])} style={{ width: `${width}%` }} />
          </span>
          {budget.paused ? (
            <Badge variant="negative">пауза{budget.pause_reason ? `: ${budget.pause_reason}` : ""}</Badge>
          ) : null}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Clock className="h-3.5 w-3.5" aria-hidden="true" />
          Метрики опрошены {formatAgo(freshness.metrics_polled_at, now)}
        </span>
        <span>Ролл-ап {formatAgo(freshness.rollup_at, now)}</span>
        <span>Переходы {formatAgo(freshness.clicks_last_at, now)}</span>
        <span>Конверсии {formatAgo(freshness.conversions_last_at, now)}</span>
        {freshness.publications_stale > 0 ? <span>Устаревших публикаций: {freshness.publications_stale}</span> : null}
        {freshness.stale ? (
          <Badge variant="warning" outline>
            данные неполные
          </Badge>
        ) : null}
      </div>
      {freshness.warnings.length > 0 ? (
        <ul className="flex flex-col gap-0.5 text-2xs">
          {freshness.warnings.map((warning) => (
            <li key={warning.code}>
              <span className="font-mono">{warning.code}</span> — {warning.message}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
