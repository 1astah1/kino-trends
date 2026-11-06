"use client";

import { parseAsStringLiteral, useQueryState } from "nuqs";

import { SectionError } from "@/components/common/section-error";
import {
  DataTable,
  type DataTableColumn,
  InfoTooltip,
  PillToggle,
  SectionCard,
  Select,
  Skeleton,
  SkeletonStatGrid,
  StatTile,
  StatTileGrid,
} from "@/components/ui";
import { formatNumber, formatUsd } from "@/lib/format";
import {
  type AnalyticsFilters,
  analyticsExportHref,
  type CostGroupBy,
  type CostGroupRow,
  useAnalyticsCosts,
} from "@/lib/queries/analytics";

import { TimeseriesChart } from "./charts-lazy";
import { COST_GROUP_LABELS, formatRatio, formatRub, GRANULARITY_LABELS } from "./labels";
import { AnnotationList, CsvLink, TabToolbar } from "./shared";

const GROUP_BYS = ["provider", "step", "track", "outcome", "channel", "niche", "product"] as const;
const GRANULARITIES = ["day", "week"] as const;
/** Цветов графика три: верхние группы + свёрнутый хвост «прочее». */
const CHART_TOP = 3;

const price = (value: number | null) => (value === null ? "—" : formatUsd(value, value < 1 ? 4 : 2));

/** Расходы: по провайдерам, шагам, трекам, исходам; честная цена опубликованного ролика. */
export function CostsTab({ filters }: { filters: AnalyticsFilters }) {
  const [groupBy, setGroupBy] = useQueryState(
    "group_by",
    parseAsStringLiteral(GROUP_BYS).withDefault("provider").withOptions({ history: "replace" }),
  );
  const [granularity, setGranularity] = useQueryState(
    "granularity",
    parseAsStringLiteral(GRANULARITIES).withDefault("day").withOptions({ history: "replace" }),
  );
  const costs = useAnalyticsCosts(filters, { groupBy, granularity, top: CHART_TOP });
  const data = costs.data;

  const columns: Array<DataTableColumn<CostGroupRow>> = [
    {
      key: "label",
      header: COST_GROUP_LABELS[groupBy],
      render: (row) => (
        <span className="flex min-w-0 flex-col">
          <span className="truncate font-medium">{row.label || row.key}</span>
          {row.label !== row.key ? <span className="font-mono text-2xs text-fg-muted">{row.key}</span> : null}
        </span>
      ),
    },
    {
      key: "spend",
      header: "Расход",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatUsd(row.spend_usd, 2)}</span>,
    },
    {
      key: "share",
      header: "Доля",
      className: "w-48",
      render: (row) => (
        <span className="flex items-center gap-2">
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
            <span
              className="block h-full rounded-full bg-chart-2"
              style={{ width: `${Math.min(100, (row.share ?? 0) * 100)}%` }}
            />
          </span>
          <span className="tabular w-12 text-right font-mono text-xs">{formatRatio(row.share, 1)}</span>
        </span>
      ),
    },
    {
      key: "calls",
      header: "Вызовов",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatNumber(row.calls)}</span>,
    },
    {
      key: "avg",
      header: "За вызов",
      className: "text-right",
      render: (row) => (
        <span className="tabular font-mono text-xs">{row.calls > 0 ? price(row.spend_usd / row.calls) : "—"}</span>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-4" data-testid="analytics-costs">
      <TabToolbar
        right={<CsvLink href={analyticsExportHref(filters, { report: "costs", costGroupBy: groupBy, granularity })} />}
      >
        <Select
          aria-label="Группировка расходов"
          className="h-7 w-44 text-xs"
          value={groupBy}
          onChange={(event) => void setGroupBy(event.target.value as CostGroupBy)}
        >
          {GROUP_BYS.map((item) => (
            <option key={item} value={item}>
              {COST_GROUP_LABELS[item]}
            </option>
          ))}
        </Select>
        <PillToggle
          label="Шаг"
          options={GRANULARITIES.map((value) => ({ value, label: GRANULARITY_LABELS[value] }))}
          value={granularity}
          onChange={(value) => void setGranularity(value)}
        />
        {data ? (
          <span className="text-xs text-fg-muted">
            курс {formatNumber(data.totals.fx_usd_rub, 2)} ₽/$ · накладные {formatUsd(data.totals.overhead_usd, 2)}
            {filters.includeOverhead ? " (в итоге)" : " (отдельно)"}
          </span>
        ) : null}
      </TabToolbar>

      {costs.isError ? (
        <SectionError error={costs.error} onRetry={() => void costs.refetch()} title="Расходы не загрузились" />
      ) : !data ? (
        <>
          <SkeletonStatGrid count={6} />
          <Skeleton className="h-64" />
        </>
      ) : (
        <>
          <StatTileGrid className="lg:grid-cols-6">
            <StatTile
              label="Расход за период"
              value={formatUsd(data.totals.spend_usd, 2)}
              hint={formatRub(data.totals.spend_rub)}
            />
            <StatTile
              label="Цена опубликованного ролика"
              value={price(data.totals.cost_per_published_video_usd)}
              hint={
                <span className="inline-flex items-center gap-1">
                  включая брак
                  <InfoTooltip text="Расход всех роликов периода, в том числе отклонённых и упавших, делённый на число опубликованных (ТЗ §8)." />
                </span>
              }
            />
            <StatTile
              label="Цена ролика"
              value={price(data.totals.cost_per_video_usd)}
              hint={`сделано ${formatNumber(data.totals.videos_produced)}`}
            />
            <StatTile
              label="На опубликованные"
              value={formatUsd(data.totals.published_spend_usd, 2)}
              hint={`${formatNumber(data.totals.videos_published)} роликов`}
            />
            <StatTile
              label="На брак"
              value={formatUsd(data.totals.rejected_spend_usd, 2)}
              hint={`отклонено ${formatNumber(data.totals.videos_rejected)} · упало ${formatNumber(data.totals.videos_failed)}`}
            />
            <StatTile
              label="В работе"
              value={formatUsd(data.totals.in_progress_spend_usd, 2)}
              hint="ролики ещё не дошли до приёмки"
            />
          </StatTileGrid>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_420px]">
            <SectionCard title={`Расход ${GRANULARITY_LABELS[granularity].toLowerCase()}`}>
              <TimeseriesChart
                series={data.series}
                annotations={data.annotations}
                stacked
                formatValue={(value) => formatUsd(value, 3)}
                formatAxis={(value) => formatUsd(value, value < 10 ? 2 : 0)}
                emptyTitle="Расходов за период нет"
                emptyDescription="Платные вызовы провайдеров (LLM, озвучка, картинки) появятся здесь с первым роликом."
              />
              <AnnotationList annotations={data.annotations} />
            </SectionCard>
            <SectionCard title={`По группам: ${COST_GROUP_LABELS[groupBy].toLowerCase()}`} bodyClassName="p-0">
              <DataTable
                caption="Расход по группам"
                columns={columns}
                rows={data.groups}
                rowKey={(row) => row.key}
                emptyTitle="Расходов за период нет"
                emptyDescription="Группы появятся с первыми платными вызовами."
                minWidth={380}
              />
            </SectionCard>
          </div>
        </>
      )}
    </div>
  );
}
