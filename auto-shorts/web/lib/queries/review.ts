"use client";

/**
 * Приёмка (docs/ARCHITECTURE.md §5.2 `/review-queue`, `/jobs/{id}/review-context`, `/review`,
 * `/review/undo`; §5.4 ReviewContext; §7.3 «Приёмка»). Владелец — M2-D1.
 *
 * - Очередь живёт под `['review-queue', 'list', …]`. SSE `review_queue {count}` пишет число в
 *   `queryKeys.reviewQueueCount` (бейдж сайдбара); хук очереди замечает это обновление и перечитывает
 *   список, а свой ответ сам кладёт в тот же ключ — бейдж верен и без SSE.
 * - Контекст ролика — `['job', id, 'review-context']`: событие `job_status` этого ролика его освежает.
 * - Зоны для оверлея: основной источник — каталог `/banner-safe-zones` (профили площадок из
 *   config/safe_zones.json, тот же, что у рендера баннера); пока он недоступен (501 до M2-A или
 *   ошибка) — пресеты `/meta/safe-zones`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api, type Schemas, unwrap } from "@/lib/api/client";
import { useLiveRefetchInterval } from "@/lib/hooks/use-live-refetch";

import { queryKeys } from "./keys";

export type ReviewQueueItem = Schemas["ReviewQueueItem"];
export type ReviewContext = Schemas["ReviewContext"];
export type ReviewRequest = Schemas["ReviewRequest"];
export type ReviewFlag = Schemas["ReviewFlag"];
export type RejectReasonItem = Schemas["RejectReasonItem"];
export type RejectReason = Schemas["RejectReason"];
export type ReviewBanner = Schemas["ReviewBanner"];
export type BannerBox = Schemas["BannerBox"];
export type BannerWindow = Schemas["BannerWindow"];
export type BannerWarning = Schemas["BannerWarning"];
export type Rect = Schemas["Rect"];
export type Platform = Schemas["Platform"];

export const reviewKeys = {
  queueAll: ["review-queue", "list"] as const,
  queue: (channelId: string | null | undefined) => ["review-queue", "list", channelId ?? null] as const,
  context: (jobId: number) => [...queryKeys.job(jobId), "review-context"] as const,
  rejectReasons: ["meta", "reject-reasons"] as const,
  metaSafeZones: ["meta", "safe-zones"] as const,
  bannerSafeZones: ["review", "banner-safe-zones"] as const,
};

/** Очередь приёмки: pending_review | qc_failed, старые первыми (FIFO). */
export function useReviewQueue(channelId?: string | null) {
  const queryClient = useQueryClient();
  const refetchInterval = useLiveRefetchInterval(false);
  const selfSetAt = useRef(0);

  const query = useQuery({
    queryKey: reviewKeys.queue(channelId),
    queryFn: async ({ signal }) => {
      const data = await unwrap(
        api.GET("/api/v1/review-queue", { params: { query: { channel_id: channelId || undefined } }, signal }),
      );
      if (!channelId && queryClient.getQueryData(queryKeys.reviewQueueCount) !== data.items.length) {
        queryClient.setQueryData(queryKeys.reviewQueueCount, data.items.length);
        selfSetAt.current = queryClient.getQueryState(queryKeys.reviewQueueCount)?.dataUpdatedAt ?? 0;
      }
      return data;
    },
    select: (data) => data.items,
    refetchInterval,
  });

  // SSE `review_queue`: число в кеше обновилось не нами → перечитать список.
  const count = useQuery<number | null>({
    queryKey: queryKeys.reviewQueueCount,
    queryFn: () => Promise.resolve(null),
    enabled: false,
    staleTime: Infinity,
  });
  const countUpdatedAt = count.dataUpdatedAt;
  const seenAt = useRef(countUpdatedAt);
  useEffect(() => {
    if (!countUpdatedAt || countUpdatedAt === seenAt.current) return;
    seenAt.current = countUpdatedAt;
    if (countUpdatedAt === selfSetAt.current) return;
    void queryClient.invalidateQueries({ queryKey: reviewKeys.queueAll });
  }, [countUpdatedAt, queryClient]);

  return query;
}

/** Всё для экрана приёмки одним запросом (§5.4 ReviewContext). */
export function useReviewContext(jobId: number | null | undefined) {
  return useQuery({
    queryKey: reviewKeys.context(jobId ?? 0),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/jobs/{job_id}/review-context", { params: { path: { job_id: jobId as number } }, signal }),
      ),
    enabled: typeof jobId === "number" && jobId > 0,
  });
}

export function prefetchReviewContext(queryClient: ReturnType<typeof useQueryClient>, jobId: number) {
  return queryClient.prefetchQuery({
    queryKey: reviewKeys.context(jobId),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/jobs/{job_id}/review-context", { params: { path: { job_id: jobId } }, signal })),
    staleTime: 15_000,
  });
}

/** Причины отказа с клавишами (`X` + цифра). Справочник, меняется только с релизом. */
export function useRejectReasons() {
  return useQuery({
    queryKey: reviewKeys.rejectReasons,
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/meta/reject-reasons", { signal })),
    select: (data) => data.items,
    staleTime: Infinity,
  });
}

// ---------------------------------------------------------------- зоны для оверлея

export interface OverlayDeadZone {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface OverlayZones {
  source: "banner_catalog" | "meta_presets";
  /** id профиля (`youtube_shorts`, …) или площадки для пресетов. */
  profileId: string;
  label: string;
  canvas: { w: number; h: number };
  deadZones: OverlayDeadZone[];
  /** Безопасная рамка (title-safe) — циановые направляющие. */
  safe: Rect | null;
  /** Рекомендуемая зона креатива (только у каталога). */
  offerArea: Rect | null;
  note: string;
}

type BannerSafeZoneCatalog = Schemas["BannerSafeZoneCatalog"];
type SafeZoneList = Schemas["SafeZoneList"];

export function zonesFromCatalog(catalog: BannerSafeZoneCatalog, platform: Platform): OverlayZones | null {
  const profileId =
    catalog.platform_profiles.find((item) => item.platform === platform)?.profile_id ?? catalog.default_profile;
  const profile =
    catalog.items.find((item) => item.id === profileId) ??
    catalog.items.find((item) => item.id === catalog.default_profile) ??
    catalog.items[0];
  if (!profile) return null;
  return {
    source: "banner_catalog",
    profileId: profile.id,
    label: profile.label,
    canvas: profile.canvas,
    deadZones: profile.dead_zones,
    safe: profile.safe,
    offerArea: profile.offer_area,
    note: profile.notes,
  };
}

export function zonesFromPresets(list: SafeZoneList, platform: Platform): OverlayZones | null {
  const preset = list.items.find((item) => item.platform === platform) ?? list.items[0];
  if (!preset) return null;
  return {
    source: "meta_presets",
    profileId: preset.platform,
    label: preset.platform === "vk" ? "VK Клипы" : "YouTube Shorts",
    canvas: preset.canvas,
    deadZones: preset.dead_zones,
    safe: preset.conservative_safe_rect,
    offerArea: null,
    note: preset.source_note,
  };
}

/** Мёртвые зоны и безопасная рамка площадки для SVG-оверлея 1080×1920. */
export function useOverlayZones(platform: Platform) {
  const catalog = useQuery({
    queryKey: reviewKeys.bannerSafeZones,
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/banner-safe-zones", { signal })),
    staleTime: 10 * 60_000,
    retry: false,
  });
  const presets = useQuery({
    queryKey: reviewKeys.metaSafeZones,
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/meta/safe-zones", { signal })),
    enabled: catalog.isError,
    staleTime: 10 * 60_000,
  });
  const zones = catalog.data
    ? zonesFromCatalog(catalog.data, platform)
    : presets.data
      ? zonesFromPresets(presets.data, platform)
      : null;
  return { zones, isLoading: catalog.isPending || (catalog.isError && presets.isPending) };
}

// ---------------------------------------------------------------- решения

function useSettleReview() {
  const queryClient = useQueryClient();
  return (jobId: number, job?: Schemas["Job"]) => {
    if (job) queryClient.setQueryData(queryKeys.job(jobId), job);
    void queryClient.invalidateQueries({ queryKey: reviewKeys.queueAll });
    void queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
    void queryClient.invalidateQueries({ queryKey: queryKeys.job(jobId) });
  };
}

/**
 * `POST /jobs/{id}/review`. Ошибки экран разбирает сам (meta.silentError): 409 `job_state_conflict`
 * с `details.decided_by/decided_via`, 422 (approve из qc_failed), 403 (override_qc не у владельца).
 */
export function useDecideReview() {
  const settle = useSettleReview();
  return useMutation({
    mutationFn: ({ jobId, body }: { jobId: number; body: ReviewRequest }) =>
      unwrap(api.POST("/api/v1/jobs/{job_id}/review", { params: { path: { job_id: jobId } }, body })),
    meta: { silentError: true },
    onSuccess: (job, { jobId }) => settle(jobId, job),
    onError: (_error, { jobId }) => settle(jobId),
  });
}

/** `POST /jobs/{id}/review/undo` (≤ 10 мин). 409 несёт `details.reason`. */
export function useUndoReview() {
  const settle = useSettleReview();
  return useMutation({
    mutationFn: (jobId: number) =>
      unwrap(api.POST("/api/v1/jobs/{job_id}/review/undo", { params: { path: { job_id: jobId } } })),
    meta: { silentError: true },
    onSuccess: (job, jobId) => settle(jobId, job),
    onError: (_error, jobId) => settle(jobId),
  });
}
