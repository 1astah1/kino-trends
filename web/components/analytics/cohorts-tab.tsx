"use client";

import { Layers } from "lucide-react";
import { parseAsStringLiteral, useQueryState } from "nuqs";

import { SectionError } from "@/components/common/section-error";
import { EmptyState, Select, SkeletonTable, Table, TableBody, TableHead, TableRow, Td, Th } from "@/components/ui";
import { formatDay, formatNumber } from "@/lib/format";
import {
  type AnalyticsFilters,
  analyticsExportHref,
  type CohortAggregate,
  type CohortMetric,
  type CohortRow,
  useAnalyticsCohorts,
} from "@/lib/queries/analytics";

import {
  AGGREGATE_LABELS,
  COHORT_COLUMN_LABELS,
  COHORT_METRIC_LABELS,
  formatRubMinor,
  GRANULARITY_LABELS,
} from "./labels";
import { BasisFallbackNote, CsvLink, TabToolbar } from "./shared";

const COHORT_METRICS = [
  "views",
  "visitors",
  "bot_starts",
  "users",
  "first_payments",
  "revenue_minor",
  "ctr_per_1k_views",
] as const;
const GRANULARITIES = ["day", "week"] as const;
const AGGREGATES = ["median", "sum", "mean"] as const;
const AGES = ["h24", "h72", "d7", "d14", "d30"] as const;
type AgeKey = (typeof AGES)[number];

function formatCohortValue(metric: CohortMetric, value: number | null): string {
  if (value === null) return "—";
  if (metric === "revenue_minor") return formatRubMinor(value);
  if (metric === "ctr_per_1k_views") return formatNumber(value, 2);
  return formatNumber(value, Number.isInteger(value) ? 0 : 1);
}

/** Когорты по дате публикации × возраст ролика: тепловая таблица, незрелые клетки пустые. */
export function CohortsTab({ filters }: { filters: AnalyticsFilters }) {
  const [metric, setMetric] = useQueryState(
    "cohort_metric",
    parseAsStringLiteral(COHORT_METRICS).withDefault("views").withOptions({ history: "replace" }),
  );
  const [granularity, setGranularity] = useQueryState(
    "granularity",
    parseAsStringLiteral(GRANULARITIES).withDefault("week").withOptions({ history: "replace" }),
  );
  const [aggregate, setAggregate] = useQueryState(
    "aggregate",
    parseAsStringLiteral(AGGREGATES).withDefault("median").withOptions({ history: "replace" }),
  );
  const cohorts = useAnalyticsCohorts(filters, { metric, granularity, aggregate });
  const rows: CohortRow[] = cohorts.data?.rows ?? [];

  // Интенсивность клетки — доля от максимума своего столбца (разные возраста несравнимы между собой).
  const maxByAge: Record<AgeKey, number> = { h24: 0, h72: 0, d7: 0, d14: 0, d30: 0 };
  for (const row of rows) {
    for (const age of AGES) {
      const value = row.values[age];
      if (value !== null && value > maxByAge[age]) maxByAge[age] = value;
    }
  }

  return (
    <div className="flex flex-col gap-3" data-testid="analytics-cohorts">
      <TabToolbar
        right={
          <CsvLink href={analyticsExportHref(filters, { report: "cohorts", cohortMetric: metric, granularity })} />
        }
      >
        <Select
          aria-label="Метрика когорты"
          className="h-7 w-52 text-xs"
          value={metric}
          onChange={(event) => void setMetric(event.target.value as CohortMetric)}
        >
          {COHORT_METRICS.map((item) => (
            <option key={item} value={item}>
              {COHORT_METRIC_LABELS[item]}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Когорта"
          className="h-7 w-36 text-xs"
          value={granularity}
          onChange={(event) => void setGranularity(event.target.value as (typeof GRANULARITIES)[number])}
        >
          {GRANULARITIES.map((item) => (
            <option key={item} value={item}>
              {GRANULARITY_LABELS[item]}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Агрегат"
          className="h-7 w-32 text-xs"
          value={aggregate}
          onChange={(event) => void setAggregate(event.target.value as CohortAggregate)}
        >
          {AGGREGATES.map((item) => (
            <option key={item} value={item}>
              {AGGREGATE_LABELS[item]}
            </option>
          ))}
        </Select>
        <span className="text-xs text-fg-muted">
          Строка — {granularity === "week" ? "неделя" : "день"} публикации, столбец — возраст ролика. В клетке только
          ролики, которые этого возраста достигли.
        </span>
      </TabToolbar>

      <BasisFallbackNote show={cohorts.data?.basis_fallback} />

      {cohorts.isError ? (
        <SectionError error={cohorts.error} onRetry={() => void cohorts.refetch()} title="Когорты не загрузились" />
      ) : cohorts.isPending ? (
        <SkeletonTable rows={6} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Layers}
          title="Когорт пока нет"
          description="За период не было публикаций. Когорта появится в день (или неделю) первой публикации и будет наполняться по мере взросления роликов."
        />
      ) : (
        <Table minWidth={720}>
          <caption className="sr-only">Когорты публикаций</caption>
          <TableHead>
            <tr>
              <Th>Когорта</Th>
              <Th className="text-right">Роликов</Th>
              {AGES.map((age) => (
                <Th key={age} className="text-right">
                  {COHORT_COLUMN_LABELS[age]}
                </Th>
              ))}
            </tr>
          </TableHead>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.cohort}>
                <Td className="tabular font-mono text-xs">{formatDay(row.cohort)}</Td>
                <Td className="tabular text-right font-mono text-xs">{formatNumber(row.n_videos)}</Td>
                {AGES.map((age) => {
                  const value = row.values[age];
                  const mature = row.mature[age];
                  const share = value !== null && maxByAge[age] > 0 ? value / maxByAge[age] : 0;
                  return (
                    <Td
                      key={age}
                      className="tabular text-right font-mono text-xs"
                      style={
                        value !== null && share > 0
                          ? {
                              backgroundColor: `color-mix(in srgb, var(--color-chart-1) ${Math.round(8 + share * 42)}%, transparent)`,
                            }
                          : undefined
                      }
                      title={
                        value === null
                          ? "Ролики когорты ещё не достигли этого возраста"
                          : `${formatNumber(mature)} из ${formatNumber(row.n_videos)} роликов достигли возраста`
                      }
                    >
                      <span className="flex flex-col items-end">
                        <span className={value === null ? "text-fg-muted/60" : undefined}>
                          {formatCohortValue(metric, value)}
                        </span>
                        {value !== null && mature < row.n_videos ? (
                          <span className="text-2xs text-fg-muted">n={formatNumber(mature)}</span>
                        ) : null}
                      </span>
                    </Td>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
