"use client";

// Графики аналитики на recharts (грузятся отдельным чанком через charts-lazy.tsx, §7.1).
// Цвета — только токены темы: var(--color-chart-1|2|3) и var(--color-fg-muted) для хвоста «прочее».
import { useMemo } from "react";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  type TooltipPayloadEntry,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState } from "@/components/ui";
import { BUSINESS_TIMEZONE, formatDateTime, formatDay, formatNumber, formatPct } from "@/lib/format";
import type {
  AgeViewsPoint,
  ChartAnnotation,
  RetentionPoint,
  TimeseriesSeries,
  TrafficGranularity,
  VideoTrafficPoint,
} from "@/lib/queries/analytics";

import { AGE_POINT_LABELS, formatCompact } from "./labels";

export const CHART_COLORS = ["var(--color-chart-1)", "var(--color-chart-2)", "var(--color-chart-3)"] as const;
const OTHER_COLOR = "var(--color-fg-muted)";
const GRID_COLOR = "var(--color-border)";
const TICK = { fill: "var(--color-fg-muted)", fontSize: 11 } as const;
const AXIS_LINE = { stroke: "var(--color-border)" } as const;
const MARGIN = { top: 8, right: 12, bottom: 0, left: 0 } as const;

type Formatter = (value: number) => string;

/** Цвет ряда по индексу; свёрнутый хвост `other` — приглушённый. */
export function seriesColor(index: number, key?: string): string {
  if (key === "other") return OTHER_COLOR;
  return CHART_COLORS[index % CHART_COLORS.length];
}

function shortDay(value: unknown): string {
  const text = String(value ?? "");
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(text);
  return match ? `${match[3]}.${match[2]}` : text;
}

const hourFmt = new Intl.DateTimeFormat("ru-RU", { timeZone: BUSINESS_TIMEZONE, hour: "2-digit", minute: "2-digit" });

function formatTooltipValue(value: TooltipPayloadEntry["value"], format: Formatter): string {
  if (Array.isArray(value)) {
    return value.map((item) => (typeof item === "number" ? format(item) : String(item))).join(" – ");
  }
  if (typeof value === "number") return format(value);
  return value === undefined || value === null ? "—" : String(value);
}

/** Тултип в токенах консоли (у встроенного — белый фон и чёрный текст, в тёмной теме не читается). */
function ChartTooltip({
  active,
  payload,
  label,
  formatValue,
  formatLabel,
  colorOf,
}: {
  active?: boolean;
  payload?: ReadonlyArray<TooltipPayloadEntry>;
  label?: string | number;
  formatValue: Formatter;
  formatLabel?: (label: string | number) => string;
  colorOf?: (name: string) => string | undefined;
}) {
  if (!active || !payload?.length) return null;
  const rows = payload.filter((entry) => entry.value !== undefined && entry.value !== null);
  if (!rows.length) return null;
  return (
    <div className="rounded-md border border-border bg-surface px-2.5 py-2 text-xs shadow-md">
      {label !== undefined && label !== null ? (
        <p className="mb-1 font-medium text-fg">{formatLabel ? formatLabel(label) : String(label)}</p>
      ) : null}
      <ul className="flex flex-col gap-0.5">
        {rows.map((entry, index) => {
          const name = String(entry.name ?? entry.dataKey ?? "");
          return (
            <li key={`${name}-${index}`} className="flex items-center justify-between gap-4">
              <span className="flex items-center gap-1.5 text-fg-muted">
                <span
                  className="h-2 w-2 shrink-0 rounded-sm"
                  style={{ background: colorOf?.(name) ?? entry.color ?? OTHER_COLOR }}
                  aria-hidden="true"
                />
                {name}
              </span>
              <span className="tabular font-mono text-fg">{formatTooltipValue(entry.value, formatValue)}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function ChartLegend({ items }: { items: Array<{ label: string; color: string; dashed?: boolean }> }) {
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-fg-muted">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <span
            className="inline-block h-0.5 w-3 rounded-full"
            style={
              item.dashed
                ? { backgroundImage: `repeating-linear-gradient(90deg, ${item.color} 0 3px, transparent 3px 5px)` }
                : { background: item.color }
            }
            aria-hidden="true"
          />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------- ряд метрики по дням/неделям

export interface TimeseriesChartProps {
  series: TimeseriesSeries[];
  annotations?: ChartAnnotation[];
  formatValue?: Formatter;
  formatAxis?: Formatter;
  height?: number;
  /** Расход по группам — стопкой площадей; иначе линии. */
  stacked?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
}

/** Ряд (или несколько рядов) по дням или неделям; отметки — вертикальные пунктиры по дате. */
export function TimeseriesChart({
  series,
  annotations = [],
  formatValue = (value) => formatNumber(value, 2),
  formatAxis = formatCompact,
  height = 260,
  stacked = false,
  emptyTitle = "Нет данных за период",
  emptyDescription,
}: TimeseriesChartProps) {
  const rows = useMemo(() => {
    const byDate = new Map<string, Record<string, string | number | null>>();
    for (const item of series) {
      for (const point of item.points) {
        let row = byDate.get(point.t);
        if (!row) {
          row = { t: point.t };
          byDate.set(point.t, row);
        }
        row[item.key] = point.v;
      }
    }
    return [...byDate.values()].sort((a, b) => String(a.t).localeCompare(String(b.t)));
  }, [series]);

  const hasData = rows.some((row) => series.some((item) => typeof row[item.key] === "number"));
  if (!hasData) return <EmptyState compact title={emptyTitle} description={emptyDescription} />;

  const dates = new Set(rows.map((row) => String(row.t)));
  const marks = annotations.filter((annotation) => dates.has(annotation.date));
  const colorOf = (name: string) => {
    const index = series.findIndex((item) => item.label === name);
    return index === -1 ? undefined : seriesColor(index, series[index].key);
  };

  return (
    <div className="flex flex-col gap-2">
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={rows} margin={MARGIN}>
          <CartesianGrid stroke={GRID_COLOR} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="t"
            tick={TICK}
            tickLine={false}
            axisLine={AXIS_LINE}
            tickFormatter={shortDay}
            minTickGap={28}
          />
          <YAxis tick={TICK} tickLine={false} axisLine={false} width={64} tickFormatter={formatAxis} />
          <Tooltip
            cursor={{ stroke: GRID_COLOR }}
            content={
              <ChartTooltip
                formatValue={formatValue}
                formatLabel={(label) => formatDay(String(label))}
                colorOf={colorOf}
              />
            }
          />
          {series.map((item, index) =>
            stacked ? (
              <Area
                key={item.key}
                type="monotone"
                dataKey={item.key}
                name={item.label}
                stackId="stack"
                stroke={seriesColor(index, item.key)}
                fill={seriesColor(index, item.key)}
                fillOpacity={0.22}
                strokeWidth={1.5}
                dot={false}
                connectNulls
                isAnimationActive={false}
              />
            ) : (
              <Line
                key={item.key}
                type="monotone"
                dataKey={item.key}
                name={item.label}
                stroke={seriesColor(index, item.key)}
                strokeWidth={series.length === 1 ? 2 : 1.5}
                dot={rows.length <= 2 ? { r: 3 } : false}
                activeDot={{ r: 3 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            ),
          )}
          {marks.map((annotation) => (
            <ReferenceLine
              key={`${annotation.code}-${annotation.date}`}
              x={annotation.date}
              stroke="var(--color-fg-muted)"
              strokeDasharray="4 4"
            />
          ))}
        </ComposedChart>
      </ResponsiveContainer>
      {series.length > 1 ? (
        <ChartLegend
          items={series.map((item, index) => ({ label: item.label, color: seriesColor(index, item.key) }))}
        />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- просмотры от возраста на фоне канала

/** Просмотры ролика по ровным возрастам на фоне коридора p25–p75 и медианы канала. */
export function AgeViewsChart({ points, height = 260 }: { points: AgeViewsPoint[]; height?: number }) {
  const rows = points.map((point) => ({
    label: AGE_POINT_LABELS[point.age] ?? point.age,
    views: point.reached ? point.views : null,
    median: point.channel_median,
    band: point.channel_p25 !== null && point.channel_p75 !== null ? [point.channel_p25, point.channel_p75] : null,
  }));
  const hasViews = rows.some((row) => typeof row.views === "number");
  const hasChannel = rows.some((row) => row.median !== null || row.band !== null);
  if (!hasViews && !hasChannel) {
    return (
      <EmptyState
        compact
        title="Снимков метрик ещё нет"
        description="Первый опрос — через час после публикации, дальше по лестнице 2, 4, 8, 12, 24 ч…"
      />
    );
  }
  const colors: Record<string, string> = {
    Ролик: CHART_COLORS[0],
    "Медиана канала": CHART_COLORS[2],
    "Коридор p25–p75": CHART_COLORS[2],
  };
  return (
    <div className="flex flex-col gap-2">
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={rows} margin={MARGIN}>
          <CartesianGrid stroke={GRID_COLOR} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={TICK} tickLine={false} axisLine={AXIS_LINE} />
          <YAxis tick={TICK} tickLine={false} axisLine={false} width={64} tickFormatter={formatCompact} />
          <Tooltip
            cursor={{ stroke: GRID_COLOR }}
            content={
              <ChartTooltip
                formatValue={(value) => formatNumber(value)}
                formatLabel={(label) => `Возраст ${label}`}
                colorOf={(name) => colors[name]}
              />
            }
          />
          <Area
            type="monotone"
            dataKey="band"
            name="Коридор p25–p75"
            stroke="none"
            fill={CHART_COLORS[2]}
            fillOpacity={0.15}
            connectNulls
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="median"
            name="Медиана канала"
            stroke={CHART_COLORS[2]}
            strokeDasharray="4 3"
            strokeWidth={1.5}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="views"
            name="Ролик"
            stroke={CHART_COLORS[0]}
            strokeWidth={2}
            dot={{ r: 2.5 }}
            activeDot={{ r: 4 }}
            connectNulls={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
      <ChartLegend
        items={[
          { label: "Ролик", color: CHART_COLORS[0] },
          { label: "Медиана канала", color: CHART_COLORS[2], dashed: true },
          { label: "Коридор p25–p75 канала", color: CHART_COLORS[2] },
        ]}
      />
    </div>
  );
}

// ---------------------------------------------------------------- удержание

/** Кривая удержания: доля зрителей по доле длительности; пунктир — относительно похожих роликов. */
export function RetentionChart({ points, height = 220 }: { points: RetentionPoint[]; height?: number }) {
  const rows = points.map((point) => ({
    x: Math.round(point.ratio * 1000) / 10,
    watch: Math.round(point.audience_watch_ratio * 1000) / 10,
    relative: point.relative === null ? null : Math.round(point.relative * 1000) / 10,
  }));
  if (!rows.length) return <EmptyState compact title="Точек удержания нет" />;
  const hasRelative = rows.some((row) => row.relative !== null);
  const colors: Record<string, string> = { Смотрят: CHART_COLORS[0], "Относительно похожих": CHART_COLORS[1] };
  return (
    <div className="flex flex-col gap-2">
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={rows} margin={MARGIN}>
          <CartesianGrid stroke={GRID_COLOR} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="x"
            type="number"
            domain={[0, 100]}
            ticks={[0, 25, 50, 75, 100]}
            tick={TICK}
            tickLine={false}
            axisLine={AXIS_LINE}
            tickFormatter={(value: number) => `${value}%`}
          />
          <YAxis
            tick={TICK}
            tickLine={false}
            axisLine={false}
            width={48}
            domain={[0, "auto"]}
            tickFormatter={(value: number) => `${value}%`}
          />
          <Tooltip
            cursor={{ stroke: GRID_COLOR }}
            content={
              <ChartTooltip
                formatValue={(value) => formatPct(value, 1)}
                formatLabel={(label) => `${label}% длительности`}
                colorOf={(name) => colors[name]}
              />
            }
          />
          <Line
            type="monotone"
            dataKey="watch"
            name="Смотрят"
            stroke={CHART_COLORS[0]}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          {hasRelative ? (
            <Line
              type="monotone"
              dataKey="relative"
              name="Относительно похожих"
              stroke={CHART_COLORS[1]}
              strokeDasharray="4 3"
              strokeWidth={1.5}
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
          ) : null}
        </ComposedChart>
      </ResponsiveContainer>
      <ChartLegend
        items={[
          { label: "Доля зрителей, которые ещё смотрят", color: CHART_COLORS[0] },
          ...(hasRelative
            ? [{ label: "Относительно похожих роликов (50% — как у всех)", color: CHART_COLORS[1], dashed: true }]
            : []),
        ]}
      />
    </div>
  );
}

// ---------------------------------------------------------------- трафик ролика по часам/дням

/** Переходы (столбики), посетители и старты бота (линии) по часам или дням. */
export function TrafficChart({
  points,
  granularity,
  height = 220,
}: {
  points: VideoTrafficPoint[];
  granularity: TrafficGranularity;
  height?: number;
}) {
  const rows = points.map((point) => ({
    t: point.t,
    clicks: point.clicks,
    visitors: point.unique_visitors,
    bot_starts: point.bot_starts,
  }));
  const hasData = rows.some((row) => row.clicks > 0 || row.visitors > 0 || row.bot_starts > 0);
  if (!hasData) {
    return (
      <EmptyState
        compact
        title="Переходов пока нет"
        description="Клики по ссылке из описания и старты бота появятся здесь по часам."
      />
    );
  }
  const tick = (value: unknown) => {
    const text = String(value ?? "");
    if (granularity === "hour") {
      const date = new Date(text);
      return Number.isNaN(date.getTime()) ? text : hourFmt.format(date);
    }
    return shortDay(text);
  };
  const label = (value: string | number) =>
    granularity === "hour" ? formatDateTime(String(value), { short: true }) : formatDay(String(value).slice(0, 10));
  const colors: Record<string, string> = {
    Переходы: CHART_COLORS[0],
    Посетители: CHART_COLORS[1],
    "Старты бота": CHART_COLORS[2],
  };
  return (
    <div className="flex flex-col gap-2">
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={rows} margin={MARGIN}>
          <CartesianGrid stroke={GRID_COLOR} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="t" tick={TICK} tickLine={false} axisLine={AXIS_LINE} tickFormatter={tick} minTickGap={28} />
          <YAxis
            tick={TICK}
            tickLine={false}
            axisLine={false}
            width={48}
            allowDecimals={false}
            tickFormatter={formatCompact}
          />
          <Tooltip
            cursor={{ fill: "var(--color-surface-2)" }}
            content={
              <ChartTooltip
                formatValue={(value) => formatNumber(value)}
                formatLabel={label}
                colorOf={(name) => colors[name]}
              />
            }
          />
          <Bar dataKey="clicks" name="Переходы" fill={CHART_COLORS[0]} fillOpacity={0.45} isAnimationActive={false} />
          <Line
            type="monotone"
            dataKey="visitors"
            name="Посетители"
            stroke={CHART_COLORS[1]}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="bot_starts"
            name="Старты бота"
            stroke={CHART_COLORS[2]}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
      <ChartLegend
        items={[
          { label: "Переходы", color: CHART_COLORS[0] },
          { label: "Посетители", color: CHART_COLORS[1] },
          { label: "Старты бота", color: CHART_COLORS[2] },
        ]}
      />
    </div>
  );
}
