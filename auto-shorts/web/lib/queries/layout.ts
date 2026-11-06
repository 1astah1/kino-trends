"use client";

/**
 * Раскладка ролика для редактора приёмки (§6.3.7): `GET/PUT /jobs/{id}/layout`.
 *
 * Ключ — `['job', id, 'layout']`: SSE `job_status` этого ролика (инвалидация `['job', id]`) освежит
 * и его. После PUT ответ кладётся в кеш сразу, контекст приёмки и список роликов перечитываются
 * (с `rerender=true` ролик уходит из очереди в работу).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";

import { queryKeys } from "./keys";
import { reviewKeys } from "./review";

export type JobLayout = Schemas["JobLayout"];
export type LayoutOverride = Schemas["LayoutOverride"];
export type LayoutBannerShift = Schemas["LayoutBannerShift"];
export type LayoutMode = Schemas["LayoutMode"];
export type LayoutRect = Schemas["LayoutRect"];
export type LayoutRectKind = Schemas["LayoutRectKind"];
export type LayoutConflict = Schemas["LayoutConflict"];
export type LayoutSnapshot = Schemas["LayoutSnapshotOut"];
export type LayoutPut = Schemas["LayoutPut"];
export type Rect = Schemas["Rect"];

export const layoutKeys = {
  job: (jobId: number) => [...queryKeys.job(jobId), "layout"] as const,
};

/** Канва, safe zone, авто-раскладка и раскладка с текущим переопределением. */
export function useJobLayout(jobId: number | null | undefined, { enabled = true } = {}) {
  return useQuery({
    queryKey: layoutKeys.job(jobId ?? 0),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/jobs/{job_id}/layout", { params: { path: { job_id: jobId as number } }, signal })),
    enabled: enabled && typeof jobId === "number" && jobId > 0,
  });
}

/**
 * `PUT /jobs/{id}/layout`. Ошибки показывает редактор сам (meta.silentError): 409 `job_state_conflict`
 * у одобренного ролика или пока идёт рендер, 422 — значения вне канвы.
 */
export function useSaveJobLayout(jobId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: LayoutPut) =>
      unwrap(api.PUT("/api/v1/jobs/{job_id}/layout", { params: { path: { job_id: jobId } }, body })),
    meta: { silentError: true },
    onSuccess: (layout) => {
      queryClient.setQueryData(layoutKeys.job(jobId), layout);
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(jobId), exact: true });
      void queryClient.invalidateQueries({ queryKey: reviewKeys.context(jobId) });
      if (layout.rerender_started) {
        void queryClient.invalidateQueries({ queryKey: reviewKeys.queueAll });
        void queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
      }
    },
  });
}

export type JobCaptions = Schemas["JobCaptions"];
export type JobCaptionsPut = Schemas["JobCaptionsPut"];

/** Субтитры ролика одной галочкой: `GET /jobs/{id}/captions`. */
export function useJobCaptions(jobId: number | null | undefined, { enabled = true } = {}) {
  return useQuery({
    queryKey: [...queryKeys.job(jobId ?? 0), "captions"] as const,
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/jobs/{job_id}/captions", { params: { path: { job_id: jobId as number } }, signal })),
    enabled: enabled && typeof jobId === "number" && jobId > 0,
  });
}

/**
 * `PUT /jobs/{id}/captions` — включить/выключить субтитры и сразу перерендерить со стадии render (с приёмки
 * ролик уходит в работу и вернётся в очередь). 409 у одобренного ролика и пока идёт рендер.
 */
export function useSetJobCaptions(jobId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: JobCaptionsPut) =>
      unwrap(api.PUT("/api/v1/jobs/{job_id}/captions", { params: { path: { job_id: jobId } }, body })),
    meta: { silentError: true },
    onSuccess: (captions) => {
      queryClient.setQueryData([...queryKeys.job(jobId), "captions"], captions);
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(jobId), exact: true });
      void queryClient.invalidateQueries({ queryKey: reviewKeys.context(jobId) });
      if (captions.rerender_started) {
        void queryClient.invalidateQueries({ queryKey: reviewKeys.queueAll });
        void queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
      }
    },
  });
}

export type JobChannel = Schemas["JobChannel"];

/** Канал ролика в приёмке и можно ли его сменить: `GET /jobs/{id}/channel`. */
export function useJobChannel(jobId: number | null | undefined, { enabled = true } = {}) {
  return useQuery({
    queryKey: [...queryKeys.job(jobId ?? 0), "channel"] as const,
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/jobs/{job_id}/channel", { params: { path: { job_id: jobId as number } }, signal })),
    enabled: enabled && typeof jobId === "number" && jobId > 0,
  });
}

/** `PUT /jobs/{id}/channel` — ролик уйдёт на другой канал: его баннер и оформление, перерендер со стадии render. */
export function useSetJobChannel(jobId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["JobChannelPut"]) =>
      unwrap(api.PUT("/api/v1/jobs/{job_id}/channel", { params: { path: { job_id: jobId } }, body })),
    meta: { silentError: true },
    onSuccess: (view) => {
      queryClient.setQueryData([...queryKeys.job(jobId), "channel"], view);
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(jobId), exact: true });
      void queryClient.invalidateQueries({ queryKey: reviewKeys.context(jobId) });
      void queryClient.invalidateQueries({ queryKey: reviewKeys.queueAll });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
    },
  });
}
