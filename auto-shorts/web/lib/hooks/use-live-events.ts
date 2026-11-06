"use client";

import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import type { Schemas } from "@/lib/api/client";
import type { SseFrame } from "@/lib/api/sse-parse";
import { connectLiveEvents, type LiveStatus } from "@/lib/api/sse";
import { queryKeys } from "@/lib/queries/keys";
import { useLiveStore } from "@/lib/store/live";

/** Как событие SSE меняет кеш запросов (§7.4: `invalidateQueries` или `setQueryData`). */
export function applyLiveEvent(queryClient: QueryClient, frame: SseFrame): void {
  switch (frame.event) {
    case "budget": {
      const data = frame.data;
      const had = queryClient.getQueryData<Schemas["Budget"]>(queryKeys.budget);
      if (had) {
        queryClient.setQueryData<Schemas["Budget"]>(queryKeys.budget, {
          ...had,
          spent_today_usd: data.spent_usd,
          reserved_usd: data.reserved_usd,
          daily_limit_usd: data.limit_usd,
          paused: data.paused,
        });
      } else {
        void queryClient.invalidateQueries({ queryKey: queryKeys.budget });
      }
      return;
    }
    case "alert":
      void queryClient.invalidateQueries({ queryKey: queryKeys.alertsAll });
      return;
    case "worker":
      void queryClient.invalidateQueries({ queryKey: queryKeys.systemStatus });
      return;
    case "review_queue":
      queryClient.setQueryData(queryKeys.reviewQueueCount, frame.data.count);
      return;
    case "packages":
      queryClient.setQueryData(queryKeys.packagesCounts, frame.data);
      return;
    case "job_status":
    case "job_progress":
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(frame.data.job_id) });
      return;
    case "task_progress":
      void queryClient.invalidateQueries({ queryKey: queryKeys.tasks });
      void queryClient.invalidateQueries({ queryKey: queryKeys.task(frame.data.task_id) });
      return;
    case "source_status":
      void queryClient.invalidateQueries({ queryKey: queryKeys.sources });
      void queryClient.invalidateQueries({ queryKey: queryKeys.source(frame.data.source_id) });
      return;
    case "publication_metrics":
      void queryClient.invalidateQueries({ queryKey: queryKeys.publication(frame.data.publication_id) });
      return;
    case "ping":
      return;
  }
}

/**
 * Единственный EventSource на вкладку. Монтируется в AppShell (то есть только после входа).
 * После возврата из «опроса» в «live» перечитывает активные запросы: события за время
 * разрыва могли потеряться.
 */
export function useLiveEvents(enabled = true) {
  const queryClient = useQueryClient();
  const setStatus = useLiveStore((state) => state.setStatus);
  const touch = useLiveStore((state) => state.touch);

  useEffect(() => {
    if (!enabled) return;
    let previous: LiveStatus | null = null;
    const connection = connectLiveEvents({
      onEvent: (frame) => {
        touch(Date.now());
        applyLiveEvent(queryClient, frame);
      },
      onStatus: (status) => {
        if (previous === "polling" && status === "live") {
          void queryClient.invalidateQueries({ type: "active" });
        }
        previous = status;
        setStatus(status);
      },
    });
    return () => {
      connection.close();
      setStatus("connecting");
    };
  }, [enabled, queryClient, setStatus, touch]);
}
