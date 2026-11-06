"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, API_PREFIX, type Schemas, unwrap } from "@/lib/api/client";

import { queryKeys } from "./keys";

/**
 * Аналитика (docs/ARCHITECTURE.md §6.7, §7.3 «Аналитика» и «Карточка ролика»): итоги периода против
 * прошлого, ряды по дням и неделям, разрезы, воронка двух веток, когорты, расходы, карточка ролика,
 * удержание, внеочередной опрос метрик и ссылка на CSV. Списка `/analytics/videos` нет — лидерборд
 * роликов это `breakdown?dimension=video`.
 */
export type AnalyticsSummary = Schemas["AnalyticsSummary"];
export type AnalyticsTimeseries = Schemas["AnalyticsTimeseries"];
export type AnalyticsBreakdown = Schemas["AnalyticsBreakdown"];
export type AnalyticsFunnel = Schemas["AnalyticsFunnel"];
export type AnalyticsCohorts = Schemas["AnalyticsCohorts"];
export type AnalyticsCosts = Schemas["AnalyticsCosts"];
export type AnalyticsTotals = Schemas["AnalyticsTotals"];
export type AnalyticsMetric = Schemas["AnalyticsMetric"];
export type AnalyticsBasis = Schemas["AnalyticsBasis"];
export type AnalyticsMode = Schemas["AnalyticsMode"];
export type AnalyticsDimension = Schemas["AnalyticsDimension"];
export type AnalyticsGranularity = Schemas["AnalyticsGranularity"];
export type AnalyticsGroupBy = Schemas["AnalyticsGroupBy"];
export type AnalyticsReport = Schemas["AnalyticsReport"];
export type CsvDialect = Schemas["CsvDialect"];
export type CohortAge = Schemas["CohortAge"];
export type CohortMetric = Schemas["CohortMetric"];
export type CohortAggregate = Schemas["CohortAggregate"];
export type CohortRow = Schemas["CohortRow"];
export type CohortInfo = Schemas["CohortInfo"];
export type CostGroupBy = Schemas["CostGroupBy"];
export type CostGroupRow = Schemas["CostGroupRow"];
export type CostTotals = Schemas["CostTotals"];
export type CostItem = Schemas["CostItem"];
export type BreakdownRow = Schemas["BreakdownRow"];
export type BreakdownVideo = Schemas["BreakdownVideo"];
export type TimeseriesSeries = Schemas["TimeseriesSeries"];
export type ChartAnnotation = Schemas["ChartAnnotation"];
export type DataFreshness = Schemas["DataFreshness"];
export type FunnelStep = Schemas["FunnelStep"];
export type FunnelStepCode = Schemas["FunnelStepCode"];
export type FunnelBranches = Schemas["FunnelBranches"];
export type FunnelCosts = Schemas["FunnelCosts"];
export type AttributionSplit = Schemas["AttributionSplit"];
export type AttributionTotals = Schemas["AttributionTotals"];
export type AttributionLevel = Schemas["AttributionLevel"];
export type VideoAnalytics = Schemas["VideoAnalytics"];
export type VideoCosts = Schemas["VideoCosts"];
export type AgeViewsPoint = Schemas["AgeViewsPoint"];
export type AgePoint = Schemas["AgePoint"];
export type AgeVerdict = Schemas["AgeVerdict"];
export type LadderStep = Schemas["LadderStep"];
export type LadderStepStatus = Schemas["LadderStepStatus"];
export type VideoTrafficPoint = Schemas["VideoTrafficPoint"];
export type TrafficGranularity = Schemas["TrafficGranularity"];
export type ConversionCount = Schemas["ConversionCount"];
export type VideoConversionEvent = Schemas["VideoConversionEvent"];
export type MetricsSnapshot = Schemas["MetricsSnapshot"];
export type PublicationRetention = Schemas["PublicationRetention"];
export type RetentionPoint = Schemas["RetentionPoint"];
export type RetentionFailureClass = Schemas["RetentionFailureClass"];
export type Platform = Schemas["Platform"];

/** Общие фильтры всех отчётов (строка фильтров в URL экрана). Пустые даты — период по умолчанию сервера. */
export interface AnalyticsFilters {
  dateFrom: string | null;
  dateTo: string | null;
  platform?: Platform | null;
  channelId?: string | null;
  nicheId?: string | null;
  productId?: string | null;
  basis?: AnalyticsBasis;
  mode?: AnalyticsMode;
  /** Только для mode=cohort. */
  cohortAge?: CohortAge | null;
  includeOverhead?: boolean;
}

export interface TimeseriesParams {
  metric: AnalyticsMetric;
  granularity: AnalyticsGranularity;
  groupBy?: AnalyticsGroupBy;
  top?: number;
}

export interface BreakdownParams {
  dimension: AnalyticsDimension;
  /** Метрика AnalyticsMetric или n_videos, views_median, views_72h_median, label, published_at; `-` — по убыванию. */
  sort: string;
  limit: number;
  offset: number;
}

export interface CohortsParams {
  metric: CohortMetric;
  granularity: AnalyticsGranularity;
  aggregate: CohortAggregate;
}

export interface CostsParams {
  groupBy: CostGroupBy;
  granularity: AnalyticsGranularity;
  top?: number;
}

export interface VideoAnalyticsParams {
  basis?: AnalyticsBasis;
  trafficGranularity?: TrafficGranularity | null;
}

/** Общие query-параметры в форме openapi-fetch (массивы — повтором ключа). */
function toQuery(filters: AnalyticsFilters) {
  return {
    date_from: filters.dateFrom ?? undefined,
    date_to: filters.dateTo ?? undefined,
    platform: filters.platform ? [filters.platform] : undefined,
    channel_id: filters.channelId ? [filters.channelId] : undefined,
    niche_id: filters.nicheId ? [filters.nicheId] : undefined,
    product_id: filters.productId ? [filters.productId] : undefined,
    basis: filters.basis,
    mode: filters.mode,
    cohort_age: filters.mode === "cohort" && filters.cohortAge ? filters.cohortAge : undefined,
    include_overhead: filters.includeOverhead ? true : undefined,
  };
}

export const analyticsKeys = {
  all: ["analytics"] as const,
  summary: (filters: AnalyticsFilters) => ["analytics", "summary", filters] as const,
  timeseries: (filters: AnalyticsFilters, params: TimeseriesParams) =>
    ["analytics", "timeseries", filters, params] as const,
  breakdown: (filters: AnalyticsFilters, params: BreakdownParams) =>
    ["analytics", "breakdown", filters, params] as const,
  funnel: (filters: AnalyticsFilters) => ["analytics", "funnel", filters] as const,
  cohorts: (filters: AnalyticsFilters, params: CohortsParams) => ["analytics", "cohorts", filters, params] as const,
  costs: (filters: AnalyticsFilters, params: CostsParams) => ["analytics", "costs", filters, params] as const,
  freshness: ["analytics", "freshness"] as const,
  videoAll: (id: number) => ["analytics", "video", id] as const,
  video: (id: number, params: VideoAnalyticsParams) => ["analytics", "video", id, params] as const,
  retention: (id: number) => ["publication", id, "retention"] as const,
};

export function useAnalyticsSummary(filters: AnalyticsFilters, { enabled = true } = {}) {
  return useQuery({
    queryKey: analyticsKeys.summary(filters),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/analytics/summary", { params: { query: toQuery(filters) }, signal })),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useAnalyticsTimeseries(filters: AnalyticsFilters, params: TimeseriesParams, { enabled = true } = {}) {
  return useQuery({
    queryKey: analyticsKeys.timeseries(filters, params),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/analytics/timeseries", {
          params: {
            query: {
              ...toQuery(filters),
              metric: params.metric,
              granularity: params.granularity,
              group_by: params.groupBy,
              top: params.top,
            },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useAnalyticsBreakdown(filters: AnalyticsFilters, params: BreakdownParams, { enabled = true } = {}) {
  return useQuery({
    queryKey: analyticsKeys.breakdown(filters, params),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/analytics/breakdown", {
          params: {
            query: {
              ...toQuery(filters),
              dimension: params.dimension,
              sort: params.sort,
              limit: params.limit,
              offset: params.offset,
            },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useAnalyticsFunnel(filters: AnalyticsFilters, { enabled = true } = {}) {
  return useQuery({
    queryKey: analyticsKeys.funnel(filters),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/analytics/funnel", { params: { query: toQuery(filters) }, signal })),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useAnalyticsCohorts(filters: AnalyticsFilters, params: CohortsParams, { enabled = true } = {}) {
  return useQuery({
    queryKey: analyticsKeys.cohorts(filters, params),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/analytics/cohorts", {
          params: {
            query: {
              ...toQuery(filters),
              metric: params.metric,
              granularity: params.granularity,
              aggregate: params.aggregate,
            },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useAnalyticsCosts(filters: AnalyticsFilters, params: CostsParams, { enabled = true } = {}) {
  return useQuery({
    queryKey: analyticsKeys.costs(filters, params),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/analytics/costs", {
          params: {
            query: {
              ...toQuery(filters),
              group_by: params.groupBy,
              granularity: params.granularity,
              top: params.top,
            },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    enabled,
  });
}

/** Свежесть данных для плашки под любой вкладкой: опрос метрик, ролл-ап, коллекторы, квота. */
export function useAnalyticsFreshness({ enabled = true } = {}) {
  return useQuery({
    queryKey: analyticsKeys.freshness,
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/analytics/freshness", { signal })),
    staleTime: 30_000,
    enabled,
  });
}

/** Карточка ролика: лестница снимков, просмотры от возраста на фоне канала, трафик, конверсии, стоимость. */
export function useVideoAnalytics(id: number | null | undefined, params: VideoAnalyticsParams = {}) {
  return useQuery({
    queryKey: analyticsKeys.video(id ?? 0, params),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/analytics/videos/{publication_id}", {
          params: {
            path: { publication_id: id as number },
            query: { basis: params.basis, traffic_granularity: params.trafficGranularity ?? undefined },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    enabled: typeof id === "number" && id > 0,
  });
}

/** Кривая удержания: только при OAuth YouTube Analytics, иначе 404 — экран показывает подсказку, не ошибку. */
export function usePublicationRetention(id: number | null | undefined) {
  return useQuery({
    queryKey: analyticsKeys.retention(id ?? 0),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/publications/{publication_id}/retention", {
          params: { path: { publication_id: id as number } },
          signal,
        }),
      ),
    enabled: typeof id === "number" && id > 0,
    retry: false,
  });
}

/** Внеочередной опрос метрик (202 → задача коллектора). Тихая мутация: тосты показывает экран. */
export function useRefreshPublicationMetrics(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/publications/{publication_id}/refresh-metrics", {
          params: { path: { publication_id: id } },
        }),
      ),
    meta: { silentError: true },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: analyticsKeys.videoAll(id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.publication(id) });
    },
  });
}

export interface ExportOptions {
  report: AnalyticsReport;
  dialect?: CsvDialect;
  dimension?: AnalyticsDimension;
  metric?: AnalyticsMetric;
  granularity?: AnalyticsGranularity;
  groupBy?: AnalyticsGroupBy;
  costGroupBy?: CostGroupBy;
  cohortMetric?: CohortMetric;
  top?: number;
  sort?: string;
  publicationId?: number;
}

/**
 * Ссылка на CSV текущего отчёта для `<a download>`: тот же origin, cookie-сессия, сервер ставит
 * `Content-Disposition: attachment`. Диалект по умолчанию `excel_ru` (BOM, `;`, запятая в дробях).
 */
export function analyticsExportHref(filters: AnalyticsFilters, options: ExportOptions): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(toQuery(filters))) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item));
    } else {
      params.set(key, String(value));
    }
  }
  params.set("report", options.report);
  params.set("dialect", options.dialect ?? "excel_ru");
  const extra: Record<string, string | number | undefined> = {
    dimension: options.dimension,
    metric: options.metric,
    granularity: options.granularity,
    group_by: options.groupBy,
    cost_group_by: options.costGroupBy,
    cohort_metric: options.cohortMetric,
    top: options.top,
    sort: options.sort,
    publication_id: options.publicationId,
  };
  for (const [key, value] of Object.entries(extra)) {
    if (value !== undefined) params.set(key, String(value));
  }
  return `${API_PREFIX}/analytics/export?${params.toString()}`;
}
