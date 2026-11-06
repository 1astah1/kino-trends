"use client";

import { parseAsInteger, parseAsStringLiteral, useQueryState } from "nuqs";

import { SectionError } from "@/components/common/section-error";
import { Badge, DataTable, type DataTableColumn, InfoTooltip, Pagination, Select } from "@/components/ui";
import { formatNumber, formatUsd } from "@/lib/format";
import {
  type AnalyticsFilters,
  analyticsExportHref,
  type BreakdownRow,
  useAnalyticsBreakdown,
} from "@/lib/queries/analytics";

import { DIMENSION_LABELS, formatRatio } from "./labels";
import { BasisFallbackNote, CsvLink, TabToolbar } from "./shared";

const DIMENSIONS = ["channel", "niche", "product", "campaign", "variant", "track", "platform"] as const;
const LIMIT = 50;
const DEFAULT_SORT = "-views";

const SORT_FIELDS: Record<string, string> = {
  label: "label",
  n_videos: "n_videos",
  views: "views",
  views_median: "views_median",
  views_72h_median: "views_72h_median",
  ctr: "ctr_per_1k_views",
  visitors: "visitors",
  spend: "spend_usd",
};

function parseSort(sort: string): { field: string; direction: "asc" | "desc" } {
  return sort.startsWith("-") ? { field: sort.slice(1), direction: "desc" } : { field: sort, direction: "asc" };
}

function Num({ value, digits = 0 }: { value: number | null | undefined; digits?: number }) {
  return (
    <span className="tabular font-mono text-xs">
      {value === null || value === undefined ? "—" : formatNumber(value, digits)}
    </span>
  );
}

/** Разрезы: канал, ниша, продукт, кампания, вариант баннера, трек, площадка — медианы, доли, p-value. */
export function BreakdownTab({ filters }: { filters: AnalyticsFilters }) {
  const [dimension, setDimension] = useQueryState(
    "dimension",
    parseAsStringLiteral(DIMENSIONS).withDefault("channel").withOptions({ history: "replace" }),
  );
  const [sort, setSort] = useQueryState("sort", { history: "replace" });
  const [offset, setOffset] = useQueryState(
    "offset",
    parseAsInteger.withDefault(0).withOptions({ history: "replace" }),
  );
  const effectiveSort = sort || DEFAULT_SORT;
  const { field, direction } = parseSort(effectiveSort);

  const breakdown = useAnalyticsBreakdown(filters, { dimension, sort: effectiveSort, limit: LIMIT, offset });
  const data = breakdown.data;

  const onSort = (key: string) => {
    const target = SORT_FIELDS[key];
    if (!target) return;
    const next =
      target === field ? (direction === "desc" ? target : `-${target}`) : target === "label" ? target : `-${target}`;
    void setSort(next === DEFAULT_SORT ? null : next);
    void setOffset(null);
  };
  const sortColumnKey = Object.keys(SORT_FIELDS).find((key) => SORT_FIELDS[key] === field);

  const columns: Array<DataTableColumn<BreakdownRow>> = [
    {
      key: "label",
      header: DIMENSION_LABELS[dimension],
      sortable: true,
      className: "max-w-72",
      render: (row) => (
        <span className="flex min-w-0 flex-col">
          <span className="truncate font-medium" title={row.label}>
            {row.label || row.key}
          </span>
          {row.label !== row.key ? <span className="truncate font-mono text-2xs text-fg-muted">{row.key}</span> : null}
        </span>
      ),
    },
    {
      key: "n_videos",
      header: "Роликов",
      sortable: true,
      className: "text-right",
      render: (row) => (
        <span className="flex flex-col items-end">
          <Num value={row.n_videos} />
          <span className="text-2xs text-fg-muted">зрелых {formatNumber(row.n_mature)}</span>
        </span>
      ),
    },
    {
      key: "views",
      header: "Просмотры",
      sortable: true,
      className: "text-right",
      render: (row) => (
        <span className="flex flex-col items-end">
          <Num value={row.metrics.views} />
          <span className="text-2xs text-fg-muted">{formatRatio(row.share_views, 1)}</span>
        </span>
      ),
    },
    {
      key: "views_median",
      header: "Медиана",
      sortable: true,
      className: "text-right",
      render: (row) => (
        <span className="flex flex-col items-end">
          <Num value={row.views_median} />
          {row.views_p25 !== null && row.views_p75 !== null ? (
            <span className="tabular font-mono text-2xs text-fg-muted">
              {formatNumber(row.views_p25)}–{formatNumber(row.views_p75)}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "views_72h_median",
      header: "Медиана 72 ч",
      sortable: true,
      className: "text-right",
      render: (row) => <Num value={row.views_72h_median} />,
    },
    {
      key: "ctr",
      header: "CTR/1k",
      sortable: true,
      className: "text-right",
      render: (row) => <Num value={row.metrics.ctr_per_1k_views} digits={2} />,
    },
    {
      key: "visitors",
      header: "Посетители",
      sortable: true,
      className: "text-right",
      render: (row) => <Num value={row.metrics.visitors} />,
    },
    {
      key: "bot_starts",
      header: "Старты",
      className: "text-right",
      render: (row) => <Num value={row.metrics.bot_starts} />,
    },
    {
      key: "spend",
      header: "Расход",
      sortable: true,
      className: "text-right",
      render: (row) => (
        <span className="flex flex-col items-end">
          <span className="tabular font-mono text-xs">{formatUsd(row.metrics.spend_usd, 2)}</span>
          <span className="text-2xs text-fg-muted">{formatRatio(row.share_spend, 1)}</span>
        </span>
      ),
    },
    {
      key: "p_value",
      header: (
        <span className="inline-flex items-center gap-1">
          p-value
          <InfoTooltip text="Манн–Уитни по ln(просмотров за 72 ч): группа против остальных. Меньше 0,05 — разница не случайна." />
        </span>
      ),
      className: "text-right",
      render: (row) =>
        row.needs_more_n ? (
          <Badge variant="neutral" outline title={`Нужно хотя бы ${data?.min_videos_for_stats ?? "—"} зрелых роликов`}>
            мало данных
          </Badge>
        ) : (
          <span
            className={
              row.p_value !== null && row.p_value < 0.05
                ? "tabular font-mono text-xs text-positive"
                : "tabular font-mono text-xs"
            }
          >
            {row.p_value === null ? "—" : formatNumber(row.p_value, 3)}
          </span>
        ),
    },
  ];

  return (
    <div className="flex flex-col gap-3" data-testid="analytics-breakdown">
      <TabToolbar
        right={<CsvLink href={analyticsExportHref(filters, { report: "breakdown", dimension, sort: effectiveSort })} />}
      >
        <Select
          aria-label="Разрез"
          className="h-7 w-48 text-xs"
          value={dimension}
          onChange={(event) => {
            void setDimension(event.target.value as (typeof DIMENSIONS)[number]);
            void setOffset(null);
          }}
        >
          {DIMENSIONS.map((item) => (
            <option key={item} value={item}>
              {DIMENSION_LABELS[item]}
            </option>
          ))}
        </Select>
        {data ? (
          <span className="text-xs text-fg-muted">
            {formatNumber(data.total_rows)} групп · всего {formatNumber(data.totals.views)} просмотров ·{" "}
            {formatUsd(data.totals.spend_usd, 2)} · медиана и p75 вместо среднего
          </span>
        ) : null}
      </TabToolbar>

      <BasisFallbackNote show={data?.basis_fallback} />

      {breakdown.isError ? (
        <SectionError error={breakdown.error} onRetry={() => void breakdown.refetch()} title="Разрез не загрузился" />
      ) : (
        <>
          <DataTable
            caption={`Разрез: ${DIMENSION_LABELS[dimension]}`}
            columns={columns}
            rows={data?.rows ?? []}
            rowKey={(row) => row.key}
            sortKey={sortColumnKey}
            sortDirection={direction}
            onSort={onSort}
            isLoading={breakdown.isPending}
            emptyTitle="В этом разрезе пока пусто"
            emptyDescription="Группы появятся, когда за период будут опубликованные ролики с метриками."
            minWidth={1100}
          />
          {data && data.total_rows > LIMIT ? (
            <Pagination
              total={data.total_rows}
              limit={LIMIT}
              offset={offset}
              onOffsetChange={(next) => void setOffset(next || null)}
            />
          ) : null}
        </>
      )}
    </div>
  );
}
