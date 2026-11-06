"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";
import { useLiveRefetchInterval } from "@/lib/hooks/use-live-refetch";

import { trendsKeys } from "./trends";

/**
 * Фон Kino-trends для экрана «Очередь» (`/trends/runs`, `/trends/settings`, действия): прогоны сбора,
 * текущий прогон с фазой и хвостом лога, фоновые работы, тумблеры автоматики. Состояние живёт в
 * Kino-trends — после мутаций перечитываем, кеш руками не правим. Ключи — под корнем `trends-bridge`,
 * чтобы «Обновить» на экране трендов перечитывало и это.
 */

export type TrendsRuns = Schemas["TrendsRuns"];
export type TrendsRun = Schemas["TrendsRun"];
export type TrendsCurrentRun = Schemas["TrendsCurrentRun"];
export type TrendsBackgroundJob = Schemas["TrendsBackgroundJob"];
export type TrendsRunLog = Schemas["TrendsRunLog"];
export type TrendsRunLogLine = Schemas["TrendsRunLogLine"];
export type TrendsRunStarted = Schemas["TrendsRunStarted"];
export type TrendsPipelineTick = Schemas["TrendsPipelineTick"];
export type TrendsSettings = Schemas["TrendsSettings"];
export type TrendsSettingItem = Schemas["TrendsSettingItem"];
export type TrendsSettingsPatch = Schemas["TrendsSettingsPatch"];

export const trendsOpsKeys = {
  runsAll: ["trends-bridge", "runs"] as const,
  runs: (limit: number) => ["trends-bridge", "runs", limit] as const,
  runLog: (runId: number, limit: number) => ["trends-bridge", "run-log", runId, limit] as const,
  settings: ["trends-bridge", "settings"] as const,
};

/** `GET /trends/runs`: прогоны сбора, текущий прогон, фоновые работы. Опрос 10 с — экран живой. */
export function useTrendsRuns(
  { limit = 30 }: { limit?: number } = {},
  { enabled = true, whenLive = 10_000 }: { enabled?: boolean; whenLive?: number | false } = {},
) {
  const refetchInterval = useLiveRefetchInterval(whenLive);
  return useQuery({
    queryKey: trendsOpsKeys.runs(limit),
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/trends/runs", { params: { query: { limit } }, signal })),
    placeholderData: keepPreviousData,
    refetchInterval,
    enabled,
  });
}

/** `GET /trends/runs/{run_id}/log`: лог завершённого прогона (текущий читается из `current.log`). */
export function useTrendsRunLog(runId: number | null | undefined, limit = 300) {
  return useQuery({
    queryKey: trendsOpsKeys.runLog(runId ?? 0, limit),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/trends/runs/{run_id}/log", {
          params: { path: { run_id: runId as number }, query: { limit } },
          signal,
        }),
      ),
    enabled: typeof runId === "number" && runId > 0,
    staleTime: 30_000,
  });
}

function useInvalidateOps() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: trendsOpsKeys.runsAll });
    void queryClient.invalidateQueries({ queryKey: trendsKeys.pipelineAll });
    void queryClient.invalidateQueries({ queryKey: trendsKeys.status });
  };
}

/** «Собрать тренды сейчас» (`POST /trends/runs`). Оператор. 400 с текстом Kino-trends, если прогон уже идёт. */
export function useStartRun() {
  const invalidate = useInvalidateOps();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/trends/runs")),
    meta: { silentError: true },
    onSuccess: invalidate,
  });
}

/** «Остановить сбор» (`POST /trends/runs/cancel`). Оператор. */
export function useCancelRun() {
  const invalidate = useInvalidateOps();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/trends/runs/cancel")),
    meta: { silentError: true },
    onSuccess: invalidate,
  });
}

/** «Обновить чарты и разбор» (`POST /trends/videos-refresh`); `chartsOnly` — без нейросети и кадров. */
export function useRefreshVideos() {
  const invalidate = useInvalidateOps();
  return useMutation({
    mutationFn: ({ chartsOnly = false }: { chartsOnly?: boolean } = {}) =>
      unwrap(api.POST("/api/v1/trends/videos-refresh", { body: { charts_only: chartsOnly } })),
    meta: { silentError: true },
    onSuccess: invalidate,
  });
}

/** «Прогнать конвейер сейчас» (`POST /trends/pipeline/tick`) → итог шага. Оператор. */
export function usePipelineTick() {
  const invalidate = useInvalidateOps();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/trends/pipeline/tick")),
    meta: { silentError: true },
    onSuccess: invalidate,
  });
}

/** `GET /trends/settings`: тумблеры автоматики Kino-trends (белый список) с подписями и типами. */
export function useTrendsSettings({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: trendsOpsKeys.settings,
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/trends/settings", { signal })),
    select: (data) => data.items,
    staleTime: 30_000,
    enabled,
  });
}

/** `PUT /trends/settings {key: value}` — только владелец. Ответ кладём в кеш, статус конвейера перечитываем. */
export function useUpdateTrendsSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: TrendsSettingsPatch) => unwrap(api.PUT("/api/v1/trends/settings", { body })),
    meta: { silentError: true },
    onSuccess: (data) => {
      queryClient.setQueryData<TrendsSettings>(trendsOpsKeys.settings, data);
      void queryClient.invalidateQueries({ queryKey: trendsKeys.status });
      void queryClient.invalidateQueries({ queryKey: trendsKeys.pipelineAll });
      void queryClient.invalidateQueries({ queryKey: trendsOpsKeys.runsAll });
    },
  });
}
