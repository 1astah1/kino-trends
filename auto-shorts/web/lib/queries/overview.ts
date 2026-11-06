"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";
import { useLiveRefetchInterval } from "@/lib/hooks/use-live-refetch";

/**
 * Обзор — «Пульт» (docs/ARCHITECTURE.md §7.3 `/`): один запрос `GET /overview` на весь экран —
 * KPI с дельтами к прошлому периоду той же длины, дневные ряды для спарклайнов и малых
 * мультипликаторов, мини-воронка двух веток, «Требует тебя», топ-10 роликов, открытые алерты,
 * бюджет дня, свежесть данных и онбординг пустой базы.
 */
export type Overview = Schemas["Overview"];
export type OverviewKpis = Schemas["OverviewKpis"];
export type OverviewMetric = keyof OverviewKpis;
export type KpiValue = Schemas["KpiValue"];
export type OverviewTodo = Schemas["OverviewTodo"];
export type OverviewOnboarding = Schemas["OverviewOnboarding"];
export type MetricSeries = Schemas["MetricSeries"];
export type ChartAnnotation = Schemas["ChartAnnotation"];
export type FunnelBranches = Schemas["FunnelBranches"];
export type FunnelStep = Schemas["FunnelStep"];
export type FunnelStepCode = Schemas["FunnelStepCode"];
export type BreakdownRow = Schemas["BreakdownRow"];
export type BreakdownVideo = Schemas["BreakdownVideo"];
export type DataFreshness = Schemas["DataFreshness"];
export type AnalyticsBasis = Schemas["AnalyticsBasis"];

export interface OverviewFilters {
  /** `YYYY-MM-DD` бизнес-дня включительно; пусто — период сервера по умолчанию. */
  dateFrom?: string | null;
  dateTo?: string | null;
  channelId?: string | null;
  basis?: AnalyticsBasis | null;
}

export const overviewKeys = {
  all: ["overview"] as const,
  summary: (filters: OverviewFilters) => ["overview", "summary", filters] as const,
};

/** Сводка экрана. Пока SSE живой — опрос раз в минуту, иначе общий интервал 10 с. */
export function useOverview(filters: OverviewFilters, { enabled = true } = {}) {
  const refetchInterval = useLiveRefetchInterval(60_000);
  return useQuery({
    queryKey: overviewKeys.summary(filters),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/overview", {
          params: {
            query: {
              date_from: filters.dateFrom ?? undefined,
              date_to: filters.dateTo ?? undefined,
              channel_id: filters.channelId ? [filters.channelId] : undefined,
              basis: filters.basis ?? undefined,
            },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval,
    enabled,
  });
}
