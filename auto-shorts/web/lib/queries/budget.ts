"use client";

import { useQuery } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";
import { useLiveRefetchInterval } from "@/lib/hooks/use-live-refetch";

import { queryKeys } from "./keys";

export type Budget = Schemas["Budget"];

/** Бюджет дня. Событие SSE `budget` обновляет кеш на месте (lib/hooks/use-live-events.ts). */
export function useBudget() {
  const refetchInterval = useLiveRefetchInterval(60_000);
  return useQuery({
    queryKey: queryKeys.budget,
    queryFn: () => unwrap(api.GET("/api/v1/budget")),
    refetchInterval,
  });
}

export function budgetUsage(budget: Budget | undefined): {
  used: number;
  pct: number;
  level: "ok" | "warning" | "paused" | "over";
} {
  if (!budget) return { used: 0, pct: 0, level: "ok" };
  const used = budget.spent_today_usd + budget.reserved_usd;
  const pct = budget.daily_limit_usd > 0 ? (used / budget.daily_limit_usd) * 100 : 0;
  if (budget.paused) return { used, pct, level: "paused" };
  if (pct >= 100) return { used, pct, level: "over" };
  if (pct >= budget.alert_threshold_pct) return { used, pct, level: "warning" };
  return { used, pct, level: "ok" };
}
