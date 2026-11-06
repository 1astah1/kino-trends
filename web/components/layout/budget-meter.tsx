"use client";

import { Wallet } from "lucide-react";

import { cn } from "@/lib/cn";
import { formatDay, formatPct, formatUsd } from "@/lib/format";
import { budgetUsage, useBudget } from "@/lib/queries/budget";

import { HeaderPopover, headerButtonClass } from "./popover";

const LEVEL_STYLE = {
  ok: { bar: "bg-accent", text: "text-fg" },
  warning: { bar: "bg-warning", text: "text-warning" },
  over: { bar: "bg-negative", text: "text-negative" },
  paused: { bar: "bg-negative", text: "text-negative" },
} as const;

/** Бюджет дня с полоской (§7.3). SSE `budget` обновляет значение без запроса. */
export function BudgetMeter() {
  const budget = useBudget();
  const data = budget.data;
  const usage = budgetUsage(data);
  const style = LEVEL_STYLE[usage.level];
  const width = Math.max(0, Math.min(100, usage.pct));

  return (
    <HeaderPopover
      label="Бюджет дня"
      className="w-72"
      trigger={
        <button type="button" className={headerButtonClass} data-testid="budget-meter" data-level={usage.level}>
          <Wallet className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {data ? (
            <>
              <span className={cn("tabular hidden font-mono lg:inline", style.text)}>
                {formatUsd(usage.used)} / {formatUsd(data.daily_limit_usd)}
              </span>
              <span
                className="relative hidden h-1.5 w-14 overflow-hidden rounded-full bg-surface-2 sm:block"
                aria-hidden="true"
              >
                <span
                  className={cn("absolute inset-y-0 left-0 rounded-full", style.bar)}
                  style={{ width: `${width}%` }}
                />
              </span>
              {usage.level === "paused" ? (
                <span className="font-mono text-2xs text-negative uppercase">пауза</span>
              ) : null}
              <span className="sr-only">
                Бюджет: потрачено {formatUsd(usage.used)} из {formatUsd(data.daily_limit_usd)}
                {usage.level === "paused" ? ", платные шаги на паузе" : ""}
              </span>
            </>
          ) : (
            <span className="font-mono">{budget.isError ? "$ ?" : "$ …"}</span>
          )}
        </button>
      }
    >
      {data ? (
        <div className="flex flex-col gap-2">
          <div className="flex items-baseline justify-between">
            <p className="text-sm font-medium">Бюджет на {formatDay(data.day)}</p>
            <span className={cn("tabular font-mono text-xs", style.text)}>{formatPct(usage.pct)}</span>
          </div>
          <div className="relative h-2 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
            <span className={cn("absolute inset-y-0 left-0 rounded-full", style.bar)} style={{ width: `${width}%` }} />
            <span
              className="absolute inset-y-0 w-px bg-warning"
              style={{ left: `${Math.min(100, data.alert_threshold_pct)}%` }}
            />
          </div>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <dt className="text-fg-muted">Потрачено</dt>
            <dd className="tabular text-right font-mono">{formatUsd(data.spent_today_usd, 4)}</dd>
            <dt className="text-fg-muted">Зарезервировано</dt>
            <dd className="tabular text-right font-mono">{formatUsd(data.reserved_usd, 4)}</dd>
            <dt className="text-fg-muted">Лимит</dt>
            <dd className="tabular text-right font-mono">{formatUsd(data.daily_limit_usd)}</dd>
            <dt className="text-fg-muted">Предупреждение</dt>
            <dd className="tabular text-right font-mono">{formatPct(data.alert_threshold_pct)}</dd>
          </dl>
          {data.paused ? (
            <p className="rounded-md border border-negative/40 bg-negative/10 px-2 py-1.5 text-xs text-negative">
              Платные шаги на паузе{data.pause_reason ? `: ${data.pause_reason}` : ""}
            </p>
          ) : null}
          <p className="text-2xs text-fg-muted">
            Бизнес-день по Москве. Управление бюджетом — Настройки → Бюджет (M2).
          </p>
        </div>
      ) : (
        <p className="text-xs text-fg-muted">{budget.isError ? "Нет ответа /budget" : "Загрузка…"}</p>
      )}
    </HeaderPopover>
  );
}
