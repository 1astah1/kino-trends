"use client";

import { parseAsStringLiteral, useQueryState } from "nuqs";

import { SectionError } from "@/components/common/section-error";
import {
  DetailGrid,
  KeyValue,
  PillToggle,
  SectionCard,
  Select,
  Skeleton,
  SkeletonStatGrid,
  StatTile,
  StatTileGrid,
} from "@/components/ui";
import { formatDay, formatNumber } from "@/lib/format";
import {
  type AnalyticsFilters,
  type AnalyticsMetric,
  analyticsExportHref,
  useAnalyticsSummary,
  useAnalyticsTimeseries,
} from "@/lib/queries/analytics";

import { TimeseriesChart } from "./charts-lazy";
import {
  COHORT_AGE_LABELS,
  formatMetric,
  formatMetricAxis,
  GRANULARITY_LABELS,
  METRIC_GOOD_WHEN_DOWN,
  METRIC_GROUPS,
  METRIC_LABELS,
} from "./labels";
import { AnnotationList, AttributionTable, BasisFallbackNote, CsvLink, DeltaBadge, TabToolbar } from "./shared";

const METRICS = METRIC_GROUPS.flatMap((group) => group.metrics) as [AnalyticsMetric, ...AnalyticsMetric[]];
const GRANULARITIES = ["day", "week"] as const;

/** Плитки итогов: что показываем против прошлого периода той же длины. */
const KPI_TILES: AnalyticsMetric[] = [
  "views",
  "visitors",
  "bot_starts",
  "users",
  "first_payments",
  "revenue_minor",
  "spend_usd",
  "videos_published",
  "ctr_per_1k_views",
  "cost_per_visitor_usd",
  "cost_per_published_video_usd",
  "romi",
];

/** Итоги: плитки с дельтой, атрибуция, ряд выбранной метрики по дням/неделям, отметки. */
export function SummaryTab({ filters }: { filters: AnalyticsFilters }) {
  const [metric, setMetric] = useQueryState(
    "metric",
    parseAsStringLiteral(METRICS).withDefault("views").withOptions({ history: "replace" }),
  );
  const [granularity, setGranularity] = useQueryState(
    "granularity",
    parseAsStringLiteral(GRANULARITIES).withDefault("day").withOptions({ history: "replace" }),
  );
  const summary = useAnalyticsSummary(filters);
  const timeseries = useAnalyticsTimeseries(filters, { metric, granularity });
  const data = summary.data;

  return (
    <div className="flex flex-col gap-4" data-testid="analytics-summary">
      <TabToolbar right={<CsvLink href={analyticsExportHref(filters, { report: "summary" })} />}>
        {data ? (
          <span className="text-xs text-fg-muted">
            {formatDay(data.range.date_from)} — {formatDay(data.range.date_to)} · {data.range.days} дн · против{" "}
            {formatDay(data.previous_range.date_from)} — {formatDay(data.previous_range.date_to)}
          </span>
        ) : null}
      </TabToolbar>

      <BasisFallbackNote show={data?.basis_fallback} />

      {summary.isError ? (
        <SectionError error={summary.error} onRetry={() => void summary.refetch()} title="Итоги не загрузились" />
      ) : !data ? (
        <SkeletonStatGrid count={8} />
      ) : (
        <StatTileGrid className="lg:grid-cols-6">
          {KPI_TILES.map((key) => (
            <StatTile
              key={key}
              label={METRIC_LABELS[key]}
              value={formatMetric(key, data.totals[key])}
              badge={
                <DeltaBadge
                  value={data.totals[key]}
                  previous={data.previous[key]}
                  goodWhenDown={METRIC_GOOD_WHEN_DOWN.has(key)}
                />
              }
              hint={data.previous[key] !== null ? `было ${formatMetric(key, data.previous[key])}` : undefined}
            />
          ))}
        </StatTileGrid>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <SectionCard
          title="Динамика"
          actions={
            <div className="flex items-center gap-2">
              <Select
                aria-label="Метрика"
                className="h-7 w-56 text-xs"
                value={metric}
                onChange={(event) => void setMetric(event.target.value as AnalyticsMetric)}
              >
                {METRIC_GROUPS.map((group) => (
                  <optgroup key={group.label} label={group.label}>
                    {group.metrics.map((item) => (
                      <option key={item} value={item}>
                        {METRIC_LABELS[item]}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </Select>
              <PillToggle
                label="Шаг"
                options={GRANULARITIES.map((value) => ({ value, label: GRANULARITY_LABELS[value] }))}
                value={granularity}
                onChange={(value) => void setGranularity(value)}
              />
            </div>
          }
        >
          {timeseries.isError ? (
            <SectionError
              error={timeseries.error}
              onRetry={() => void timeseries.refetch()}
              title="Ряд не загрузился"
            />
          ) : !timeseries.data ? (
            <Skeleton className="h-64 w-full" />
          ) : (
            <>
              <TimeseriesChart
                series={timeseries.data.series}
                annotations={timeseries.data.annotations}
                formatValue={(value) => formatMetric(metric, value)}
                formatAxis={(value) => formatMetricAxis(metric, value)}
                emptyTitle="За период по этой метрике пусто"
                emptyDescription="Ряд наполнится с первыми снимками метрик и переходами по ссылкам роликов."
              />
              <AnnotationList annotations={timeseries.data.annotations} />
            </>
          )}
        </SectionCard>

        <div className="flex flex-col gap-4">
          <SectionCard title="Атрибуция">
            {data ? (
              <AttributionTable attribution={data.attribution} />
            ) : summary.isError ? null : (
              <Skeleton className="h-40 w-full" />
            )}
          </SectionCard>
          {data?.cohort ? (
            <SectionCard title="Когорта">
              <DetailGrid className="sm:grid-cols-3">
                <KeyValue label="Возраст" value={COHORT_AGE_LABELS[data.cohort.age]} />
                <KeyValue label="Опубликовано" value={formatNumber(data.cohort.videos)} />
                <KeyValue
                  label="Достигли возраста"
                  value={`${formatNumber(data.cohort.videos_mature)} · незрелых ${formatNumber(data.cohort.immature)}`}
                />
              </DetailGrid>
              <p className="text-xs text-fg-muted">
                В режиме когорты считаются только ролики, опубликованные в периоде, и только их показатели к выбранному
                возрасту. Незрелые в цифры не входят.
              </p>
            </SectionCard>
          ) : null}
        </div>
      </div>
    </div>
  );
}
