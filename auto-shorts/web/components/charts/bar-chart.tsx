"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { cn } from "@/lib/cn";

import { ChartLegend } from "./chart-legend";
import { renderChartTooltip } from "./chart-tooltip";
import {
  AXIS_TICK,
  type ChartRow,
  type ChartSeries,
  chartColor,
  formatCompact,
  formatDayTick,
  GRID_STROKE,
  SURFACE_2,
} from "./chart-utils";

export interface CategoryBarChartProps {
  rows: ChartRow[];
  series: ChartSeries[];
  height?: number;
  title: string;
  /** Подпись категории на оси X и в подсказке; по умолчанию — день `YYYY-MM-DD` → `24.10`. */
  formatX?: (value: string) => string;
  formatY?: (value: number) => string;
  /** Несколько серий стопкой (с зазором цвета поверхности между сегментами). */
  stacked?: boolean;
  className?: string;
}

/** Скруглённый верх (4 px), прямая база. */
const TOP_RADIUS: [number, number, number, number] = [4, 4, 0, 0];

/**
 * Столбцы по категориям (дни, каналы, ниши): тонкие (≤ 24 px), скруглённый верх, от одной базы.
 * Подсказка по наведению на столбец. Грузится через `next/dynamic(..., { ssr: false })`.
 */
export function CategoryBarChart({
  rows,
  series,
  height = 240,
  title,
  formatX = formatDayTick,
  formatY = formatCompact,
  stacked = false,
  className,
}: CategoryBarChartProps) {
  return (
    <figure className={cn("flex flex-col gap-2", className)} aria-label={title}>
      <div className="w-full" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 320, height }}>
          <BarChart data={rows} margin={{ top: 12, right: 12, bottom: 0, left: 0 }} barCategoryGap="25%" barGap={2}>
            <CartesianGrid vertical={false} stroke={GRID_STROKE} />
            <XAxis
              dataKey="x"
              tickFormatter={(value) => formatX(String(value))}
              tick={AXIS_TICK}
              axisLine={false}
              tickLine={false}
              minTickGap={28}
            />
            <YAxis width={44} tickFormatter={formatY} tick={AXIS_TICK} axisLine={false} tickLine={false} allowDecimals={false} />
            <Tooltip
              content={renderChartTooltip({ series, formatLabel: (label) => formatX(String(label ?? "")) })}
              cursor={{ fill: SURFACE_2 }}
              isAnimationActive={false}
              wrapperStyle={{ outline: "none" }}
            />
            {series.map((item, index) => (
              <Bar
                key={item.key}
                dataKey={item.key}
                name={item.label}
                fill={chartColor(item.color)}
                stackId={stacked ? "stack" : undefined}
                stroke={stacked ? "var(--color-surface)" : undefined}
                strokeWidth={stacked ? 2 : 0}
                radius={!stacked || index === series.length - 1 ? TOP_RADIUS : 0}
                maxBarSize={24}
                activeBar={{ fillOpacity: 0.75 }}
                isAnimationActive={false}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ChartLegend series={series} kind="bar" />
    </figure>
  );
}
