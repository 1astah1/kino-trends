"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { api, type Schemas, unwrap } from "@/lib/api/client";
import { useLiveRefetchInterval } from "@/lib/hooks/use-live-refetch";

import { queryKeys } from "./keys";

/**
 * Трендоскаут (`/trends/*`) — мост к отдельному сервису Kino-trends. Бэкенд auto-shorts только
 * проксирует его API и типизирует ответы; состояние (подборка, очередь конвейера) живёт в Kino-trends,
 * поэтому после мутаций мы перечитываем очередь и статус, а не правим кеш вручную.
 */

export type TrendsStatus = Schemas["TrendsStatus"];
export type TrendsToday = Schemas["TrendsToday"];
export type TrendsFilmSlot = Schemas["TrendsFilmSlot"];
export type TrendsPickVideo = Schemas["TrendsPickVideo"];
export type TrendsRights = Schemas["TrendsRights"];
export type TrendsVideo = Schemas["TrendsVideo"];
export type TrendsVideoList = Schemas["TrendsVideoList"];
export type TrendsVideoDetail = Schemas["TrendsVideoDetail"];
export type TrendsVideoAnalysis = Schemas["TrendsVideoAnalysis"];
export type TrendsVideoFrames = Schemas["TrendsVideoFrames"];
export type TrendsVideoFile = Schemas["TrendsVideoFile"];
export type TrendsVideoSeriesPoint = Schemas["TrendsVideoSeriesPoint"];
export type TrendsVideoLevelRequest = Schemas["TrendsVideoLevelRequest"];
export type TrendsFilmRow = Schemas["TrendsFilmRow"];
export type TrendsFilmList = Schemas["TrendsFilmList"];
export type TrendsFilmDetail = Schemas["TrendsFilmDetail"];
export type TrendsFilmTitle = Schemas["TrendsFilmTitle"];
export type TrendsFilmStats = Schemas["TrendsFilmStats"];
export type TrendsFilmSource = Schemas["TrendsFilmSource"];
export type TrendsFilmSources = Schemas["TrendsFilmSources"];
export type TrendsFilmRights = Schemas["TrendsFilmRights"];
export type TrendsFilmCutter = Schemas["TrendsFilmCutter"];
export type TrendsFilmPlan = Schemas["TrendsFilmPlan"];
export type TrendsFilmReference = Schemas["TrendsFilmReference"];
export type TrendsFilmVideo = Schemas["TrendsFilmVideo"];
export type TrendsFilmRightsManualRequest = Schemas["TrendsFilmRightsManualRequest"];
export type TrendsPipeline = Schemas["TrendsPipeline"];
export type TrendsPipelineItem = Schemas["TrendsPipelineItem"];
export type TrendsPipelineBridge = Schemas["TrendsPipelineBridge"];
export type TrendsEnqueueRequest = Schemas["TrendsEnqueueRequest"];

export type TrendsVideoLevel = "clean" | "check" | "no" | "unknown";
export type TrendsVideoSort = "score" | "audience" | "growth" | "views" | "published" | "seen";

export interface TrendsVideosFilters {
  level?: TrendsVideoLevel | null;
  /** Минимальное совпадение с нашей аудиторией, 0–1 (в Kino-trends «под аудиторию» = 0.6). */
  fit?: number | null;
  /** 1 — только шортсы, 0 — только длинные. */
  short?: 0 | 1 | null;
  pick?: boolean;
  sort?: TrendsVideoSort;
  q?: string | null;
  lang?: string | null;
  limit: number;
  offset: number;
}

export interface TrendsPipelineFilters {
  status?: string | null;
  limit?: number;
}

export type TrendsFilmPeriod = "7" | "30" | "90" | "all";
export type TrendsFilmKind =
  "all" | "film" | "series" | "animated" | "animated_series" | "anime" | "tv_show" | "franchise";
export type TrendsFilmSort =
  "score" | "growth" | "shorts" | "views" | "median" | "vpd" | "fresh" | "outliers" | "channels";

export interface TrendsFilmsFilters {
  period?: TrendsFilmPeriod;
  lang?: string;
  sort?: TrendsFilmSort;
  kind?: TrendsFilmKind;
  genre?: string | null;
  q?: string | null;
  /** Только то, с чем можно работать сейчас: права ok/caution, исходник подтверждён, не в конвейере. */
  ready?: boolean;
  limit: number;
  offset: number;
}

export interface TrendsFilmDetailOptions {
  period?: TrendsFilmPeriod;
  lang?: string;
}

// Корень ключей нарочно не `["trends"]`: под ним лежит бейдж сайдбара `queryKeys.trendsAttention`,
// и инвалидация всего моста не должна его трогать.
export const trendsKeys = {
  all: ["trends-bridge"] as const,
  status: ["trends-bridge", "status"] as const,
  today: ["trends-bridge", "today"] as const,
  videosAll: ["trends-bridge", "videos"] as const,
  videos: (filters: TrendsVideosFilters) => ["trends-bridge", "videos", filters] as const,
  pipelineAll: ["trends-bridge", "pipeline"] as const,
  pipeline: (filters: TrendsPipelineFilters) => ["trends-bridge", "pipeline", filters] as const,
  videoAll: ["trends-bridge", "video"] as const,
  video: (videoId: string) => ["trends-bridge", "video", videoId] as const,
  filmsAll: ["trends-bridge", "films"] as const,
  films: (filters: TrendsFilmsFilters) => ["trends-bridge", "films", filters] as const,
  filmAll: ["trends-bridge", "film"] as const,
  film: (titleId: string, options: TrendsFilmDetailOptions) => ["trends-bridge", "film", titleId, options] as const,
  filmSources: (titleId: string) => ["trends-bridge", "film-sources", titleId] as const,
};

/** `GET /trends/status`: настроен ли мост, отвечает ли Kino-trends, счётчики дня и очереди. */
export function useTrendsStatus() {
  const refetchInterval = useLiveRefetchInterval(60_000);
  return useQuery({
    queryKey: trendsKeys.status,
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/trends/status", { signal })),
    refetchInterval,
    staleTime: 20_000,
  });
}

/**
 * Бейдж «Трендоскаут» в сайдбаре: число чистых роликов из статуса моста кладётся в
 * `queryKeys.trendsAttention` (nav-list.tsx читает только кеш). Вызывается внутри экрана.
 */
export function useTrendsAttentionBadge() {
  const queryClient = useQueryClient();
  const status = useTrendsStatus();
  const clean = status.data?.reachable ? (status.data.today?.clean ?? 0) : null;
  useEffect(() => {
    if (clean === null) return;
    queryClient.setQueryData(queryKeys.trendsAttention, clean);
  }, [clean, queryClient]);
  return status;
}

/** `GET /trends/today`: календарь кино, подборка чистых роликов, кандидаты. Без опроса — тяжёлый ответ. */
export function useTrendsToday({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: trendsKeys.today,
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/trends/today", { signal })),
    staleTime: 60_000,
    enabled,
  });
}

/** `GET /trends/videos`: чарты популярных видео СНГ с фильтрами уровня чистоты, формата и поиском. */
export function useTrendsVideos(filters: TrendsVideosFilters, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: trendsKeys.videos(filters),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/trends/videos", {
          params: {
            query: {
              level: filters.level ?? undefined,
              fit: filters.fit ?? undefined,
              short: filters.short ?? undefined,
              pick: filters.pick || undefined,
              sort: filters.sort ?? "score",
              q: filters.q || undefined,
              lang: filters.lang || undefined,
              limit: filters.limit,
              offset: filters.offset,
            },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    enabled,
  });
}

/** `GET /trends/pipeline`: очередь конвейера Kino-trends → auto-shorts. Опрос: 30 с при живом SSE. */
export function useTrendsPipeline(filters: TrendsPipelineFilters = {}, { enabled = true }: { enabled?: boolean } = {}) {
  const refetchInterval = useLiveRefetchInterval(30_000);
  return useQuery({
    queryKey: trendsKeys.pipeline(filters),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/trends/pipeline", {
          params: { query: { status: filters.status || undefined, limit: filters.limit ?? 200 } },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval,
    enabled,
  });
}

function useInvalidatePipeline() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: trendsKeys.pipelineAll });
    void queryClient.invalidateQueries({ queryKey: trendsKeys.status });
  };
}

/** «В конвейер»: кино (`kind=film`, `ref_id=title_id`) или ролик (`kind=video`, `ref_id=video_id`). Оператор. */
export function useEnqueueTrend() {
  const invalidate = useInvalidatePipeline();
  return useMutation({
    mutationFn: (body: TrendsEnqueueRequest) => unwrap(api.POST("/api/v1/trends/pipeline/enqueue", { body })),
    meta: { silentError: true },
    onSuccess: invalidate,
  });
}

export function useRetryTrendItem() {
  const invalidate = useInvalidatePipeline();
  return useMutation({
    mutationFn: (itemId: number) =>
      unwrap(api.POST("/api/v1/trends/pipeline/{item_id}/retry", { params: { path: { item_id: itemId } } })),
    meta: { silentError: true },
    onSuccess: invalidate,
  });
}

export function useCancelTrendItem() {
  const invalidate = useInvalidatePipeline();
  return useMutation({
    mutationFn: (itemId: number) =>
      unwrap(api.POST("/api/v1/trends/pipeline/{item_id}/cancel", { params: { path: { item_id: itemId } } })),
    meta: { silentError: true },
    onSuccess: invalidate,
  });
}

// ---------------------------------------------------------------- разбор ролика

/** `GET /trends/videos/{video_id}`: большой разбор ролика. Без id запрос не уходит. */
export function useTrendsVideo(videoId: string | null | undefined, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: trendsKeys.video(videoId ?? ""),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/trends/videos/{video_id}", { params: { path: { video_id: videoId ?? "" } }, signal })),
    staleTime: 30_000,
    enabled: enabled && !!videoId,
  });
}

/**
 * Решения оператора по ролику: подборка, уровень чистоты руками, разбор заново, заказ mp4. Каждое действие
 * возвращает обновлённую деталь — кладём её в кеш сразу, а список чартов, подборку дня и статус перечитываем.
 */
export function useTrendsVideoActions(videoId: string) {
  const queryClient = useQueryClient();
  const path = { params: { path: { video_id: videoId } } } as const;
  const onSuccess = (detail: TrendsVideoDetail) => {
    queryClient.setQueryData(trendsKeys.video(videoId), detail);
    void queryClient.invalidateQueries({ queryKey: trendsKeys.videosAll });
    void queryClient.invalidateQueries({ queryKey: trendsKeys.today });
    void queryClient.invalidateQueries({ queryKey: trendsKeys.status });
  };
  const pick = useMutation({
    mutationFn: (body: { pick: boolean; note?: string | null }) =>
      unwrap(api.POST("/api/v1/trends/videos/{video_id}/pick", { ...path, body })),
    meta: { silentError: true },
    onSuccess,
  });
  const level = useMutation({
    mutationFn: (body: TrendsVideoLevelRequest) =>
      unwrap(api.POST("/api/v1/trends/videos/{video_id}/level", { ...path, body })),
    meta: { silentError: true },
    onSuccess,
  });
  const analyze = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/trends/videos/{video_id}/analyze", path)),
    meta: { silentError: true },
    onSuccess,
  });
  const file = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/trends/videos/{video_id}/file", path)),
    meta: { silentError: true },
    onSuccess,
  });
  return { pick, level, analyze, file };
}

// ---------------------------------------------------------------- кино и сериалы

/** `GET /trends/films`: произведения, которые режут в шортсы — с правами, исходником и признаком «готово». */
export function useTrendsFilms(filters: TrendsFilmsFilters, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: trendsKeys.films(filters),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/trends/films", {
          params: {
            query: {
              period: filters.period ?? "30",
              lang: filters.lang ?? "ru",
              sort: filters.sort ?? "score",
              kind: filters.kind ?? "all",
              genre: filters.genre || undefined,
              q: filters.q || undefined,
              ready: filters.ready || undefined,
              limit: filters.limit,
              offset: filters.offset,
            },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    enabled,
  });
}

/** `GET /trends/films/{title_id}`: бриф и статистика одним ответом. Без id запрос не уходит. */
export function useTrendsFilm(
  titleId: string | null | undefined,
  options: TrendsFilmDetailOptions = {},
  { enabled = true }: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: trendsKeys.film(titleId ?? "", options),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/trends/films/{title_id}", {
          params: {
            path: { title_id: titleId ?? "" },
            query: { period: options.period ?? "30", lang: options.lang ?? "ru" },
          },
          signal,
        }),
      ),
    staleTime: 60_000,
    enabled: enabled && !!titleId,
  });
}

/**
 * Действия по произведению (оператор): перепроверить права, решение по правам руками, найти исходники заново.
 * Права и исходники возвращаются своими блоками — подмешиваем их во все закешированные карточки этого
 * произведения, а список кино и подборку дня перечитываем (там права и готовность).
 */
export function useTrendsFilmActions(titleId: string) {
  const queryClient = useQueryClient();
  const path = { params: { path: { title_id: titleId } } } as const;
  const invalidateLists = () => {
    void queryClient.invalidateQueries({ queryKey: trendsKeys.filmsAll });
    void queryClient.invalidateQueries({ queryKey: trendsKeys.today });
  };
  const patchDetail = (patch: Partial<TrendsFilmDetail>) => {
    queryClient.setQueriesData<TrendsFilmDetail>({ queryKey: [...trendsKeys.filmAll, titleId] }, (current) =>
      current ? { ...current, ...patch } : current,
    );
  };
  const onRights = (rights: TrendsFilmRights) => {
    patchDetail({ rights });
    invalidateLists();
  };
  const refreshRights = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/trends/films/{title_id}/rights/refresh", path)),
    meta: { silentError: true },
    onSuccess: onRights,
  });
  const setRights = useMutation({
    mutationFn: (body: TrendsFilmRightsManualRequest) =>
      unwrap(api.POST("/api/v1/trends/films/{title_id}/rights/manual", { ...path, body })),
    meta: { silentError: true },
    onSuccess: onRights,
  });
  const findSources = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/trends/films/{title_id}/sources", path)),
    meta: { silentError: true },
    onSuccess: (result: TrendsFilmSources) => {
      patchDetail({ sources: result.items });
      queryClient.setQueryData(trendsKeys.filmSources(titleId), result);
      invalidateLists();
    },
  });
  return { refreshRights, setRights, findSources };
}

/** `POST /trends/films/find-sources`: фоновый поиск исходников для топа без них; ход — в `useTrendsRuns().jobs.sources_top`. */
export function useFindTopSources() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (limit: number) =>
      unwrap(api.POST("/api/v1/trends/films/find-sources", { params: { query: { limit } } })),
    meta: { silentError: true },
    onSuccess: () => {
      // прогресс читается из /trends/runs (ключи другого модуля — под тем же корнем)
      void queryClient.invalidateQueries({ queryKey: ["trends-bridge", "runs"] });
    },
  });
}
