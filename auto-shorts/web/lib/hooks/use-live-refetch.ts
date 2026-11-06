"use client";

import { POLLING_INTERVAL_MS } from "@/lib/api/sse";
import { useLiveStore } from "@/lib/store/live";

/**
 * `refetchInterval` для видимых экранов (§7.4): пока SSE живой — `whenLive` (по умолчанию без опроса),
 * иначе опрос раз в 10 с. Скрытая вкладка не опрашивает (TanStack Query по умолчанию).
 */
export function useLiveRefetchInterval(whenLive: number | false = false): number | false {
  const status = useLiveStore((state) => state.status);
  return status === "live" ? whenLive : POLLING_INTERVAL_MS;
}
