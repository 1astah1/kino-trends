"use client";

import { ChartLine } from "lucide-react";

import type { ChartRow } from "@/components/charts/chart-utils";
import { EmptyState } from "@/components/ui";
import { formatDay } from "@/lib/format";
import type { ChartAnnotation, MetricSeries } from "@/lib/queries/overview";

import { LazyCategoryBarChart, LazyTimeSeriesChart } from "./charts-lazy";
import { ANNOTATION_SHORT, TREND_CHARTS } from "./metric-meta";

const CHART_HEIGHT = 220;

/**
 * Динамика по дням малыми мультипликаторами: просмотры, переходы, старты (линии) и
 * опубликованные ролики (столбцы). У каждой метрики своя ось; аннотации — пунктиром на всех.
 */
export function TrendCharts({ series, annotations }: { series: MetricSeries[]; annotations: ChartAnnotation[] }) {
  const byMetric = new Map(series.map((item) => [item.metric, item.points]));
  const hasData = TREND_CHARTS.some((chart) =>
    (byMetric.get(chart.metric) ?? []).some((point) => point.v !== null && point.v !== 0),
  );
  if (!hasData) {
    return (
      <EmptyState
        compact
        icon={ChartLine}
        title="За период ещё нет движения"
        description="Графики появятся с первыми просмотрами, переходами и стартами бота. Нулевой период — нормальное состояние новой базы."
      />
    );
  }
  const chartAnnotations = annotations.map((item) => ({
    x: item.date,
    label: ANNOTATION_SHORT[item.code] ?? item.label,
  }));
  return (
    <div className="flex flex-col gap-3" data-testid="overview-trends">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {TREND_CHARTS.map((chart) => {
          const rows: ChartRow[] = (byMetric.get(chart.metric) ?? []).map((point) => ({
            x: point.t,
            [chart.metric]: point.v,
          }));
          const chartSeries = [{ key: chart.metric, label: chart.title, color: chart.color }];
          return (
            <div key={chart.metric} className="flex flex-col gap-1 rounded-lg border border-border bg-surface p-3">
              <h3 className="text-xs font-medium text-fg-muted">{chart.title}</h3>
              {chart.kind === "bar" ? (
                <LazyCategoryBarChart rows={rows} series={chartSeries} height={CHART_HEIGHT} title={chart.title} />
              ) : (
                <LazyTimeSeriesChart
                  rows={rows}
                  series={chartSeries}
                  annotations={chartAnnotations}
                  height={CHART_HEIGHT}
                  title={chart.title}
                />
              )}
            </div>
          );
        })}
      </div>
      {annotations.length > 0 ? (
        <p className="text-2xs text-fg-muted">
          Пунктир на графиках: {annotations.map((item) => `${formatDay(item.date)} — ${item.label}`).join("; ")}.
        </p>
      ) : null}
    </div>
  );
}
