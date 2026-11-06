"use client";

import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { renderChartTooltip } from "@/components/charts/chart-tooltip";
import {
  AXIS_TICK,
  type ChartColor,
  chartColor,
  CURSOR_STROKE,
  formatCompact,
  GRID_STROKE,
  SURFACE,
} from "@/components/charts/chart-utils";
import { cn } from "@/lib/cn";

export interface TrendSeriesRow {
  /** Подпись точки по X: момент замера (ISO) или день `YYYY-MM-DD`. */
  x: string;
  value: number | null;
  /** Второе значение в подсказке (например, просмотры к числу публикаций). */
  extra?: number | null;
}

export interface TrendSeriesChartProps {
  rows: TrendSeriesRow[];
  /** Что нарисовано — подпись серии в подсказке и `aria-label`. */
  label: string;
  extraLabel?: string;
  kind?: "line" | "bars";
  color?: ChartColor;
  height?: number;
  formatX?: (value: string) => string;
  formatValue?: (value: number) => string;
  formatExtra?: (value: number) => string;
  className?: string;
}

/**
 * Маленький ряд для разборов Трендоскаута: просмотры ролика по замерам между прогонами Kino-trends
 * (линия) или публикации чужих нарезок по дням (столбики). Цвета — только токены темы; грузится через
 * `trend-series-chart-lazy.tsx`, чтобы recharts не попал в общий чанк.
 */
export function TrendSeriesChart({
  rows,
  label,
  extraLabel,
  kind = "line",
  color = 1,
  height = 140,
  formatX = (value) => value,
  formatValue = formatCompact,
  formatExtra = formatCompact,
  className,
}: TrendSeriesChartProps) {
  const series = [
    { key: "value", label, color, format: formatValue },
    ...(extraLabel ? [{ key: "extra", label: extraLabel, color: "muted" as const, format: formatExtra }] : []),
  ];
  const stroke = chartColor(color);
  return (
    <figure className={cn("flex flex-col gap-1", className)} aria-label={label}>
      <div className="w-full" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 320, height }}>
          <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke={GRID_STROKE} />
            <XAxis
              dataKey="x"
              tickFormatter={formatX}
              tick={AXIS_TICK}
              axisLine={false}
              tickLine={false}
              minTickGap={32}
            />
            <YAxis
              width={44}
              tickFormatter={formatValue}
              tick={AXIS_TICK}
              axisLine={false}
              tickLine={false}
              allowDecimals={false}
            />
            <Tooltip
              content={renderChartTooltip({ series, formatLabel: (value) => formatX(String(value ?? "")) })}
              cursor={{ stroke: CURSOR_STROKE, strokeWidth: 1 }}
              isAnimationActive={false}
              wrapperStyle={{ outline: "none" }}
            />
            {kind === "bars" ? (
              <Bar
                dataKey="value"
                name={label}
                fill={stroke}
                radius={[2, 2, 0, 0]}
                maxBarSize={28}
                isAnimationActive={false}
              />
            ) : (
              <Line
                type="monotone"
                dataKey="value"
                name={label}
                stroke={stroke}
                strokeWidth={2}
                strokeLinecap="round"
                dot={rows.length <= 12 ? { r: 2.5, fill: stroke, stroke: SURFACE, strokeWidth: 1.5 } : false}
                activeDot={{ r: 4, fill: stroke, stroke: SURFACE, strokeWidth: 2 }}
                connectNulls
                isAnimationActive={false}
              />
            )}
            {extraLabel ? (
              <Line
                dataKey="extra"
                name={extraLabel}
                stroke="none"
                dot={false}
                activeDot={false}
                legendType="none"
                isAnimationActive={false}
              />
            ) : null}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
