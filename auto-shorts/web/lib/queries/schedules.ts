"use client";

import { useQuery } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";
import { useLiveRefetchInterval } from "@/lib/hooks/use-live-refetch";

export type Schedule = Schemas["Schedule"];

export const scheduleKeys = {
  list: ["schedules"] as const,
};

/** `GET /schedules`: периодические задачи auto-shorts (опрос метрик, уборка, автозалив…) и когда они в следующий раз. */
export function useSchedules({ enabled = true }: { enabled?: boolean } = {}) {
  const refetchInterval = useLiveRefetchInterval(60_000);
  return useQuery({
    queryKey: scheduleKeys.list,
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/schedules", { signal })),
    select: (data) => data.items,
    refetchInterval,
    staleTime: 30_000,
    enabled,
  });
}

/** Ближайший включённый запуск по расписанию (для пустого состояния «система ждёт следующего прогона в …»). */
export function nextScheduledRun(items: readonly Schedule[] | undefined): Schedule | null {
  if (!items) return null;
  let best: Schedule | null = null;
  for (const item of items) {
    if (!item.enabled) continue;
    const at = Date.parse(item.next_run_at);
    if (Number.isNaN(at)) continue;
    if (!best || at < Date.parse(best.next_run_at)) best = item;
  }
  return best;
}
