"use client";

import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { cn } from "@/lib/cn";
import { formatDay } from "@/lib/format";

import { ChartLegend } from "./chart-legend";
import { renderChartTooltip } from "./chart-tooltip";
import {
  AXIS_TICK,
  type ChartRow,
  type ChartSeries,
  chartColor,
  CURSOR_STROKE,
  formatCompact,
  formatDayTick,
  GRID_STROKE,
  SURFACE,
} from "./chart-utils";

export interface TimeSeriesAnnotation {
  /** День `YYYY-MM-DD`, на котором рисуется вертикальная пунктирная линия. */
  x: string;
  /** Короткая подпись у линии; полный текст лучше вынести под график. */
  label: string;
}

export interface TimeSeriesChartProps {
  rows: ChartRow[];
  series: ChartSeries[];
  annotations?: TimeSeriesAnnotation[];
  /** Высота области графика, px (220–260 на экранах консоли). */
  height?: number;
  /** Что нарисовано — для скринридера (`aria-label` у figure). */
  title: string;
  formatY?: (value: number) => string;
  allowDecimals?: boolean;
  className?: string;
}

/**
 * Временной ряд: несколько серий на одной оси (разные масштабы — разные графики, двойной оси нет),
 * перекрестие по X, одна подсказка на все серии, аннотации вертикальными линиями.
 * Грузится через `next/dynamic(..., { ssr: false })` — recharts не попадает в общий чанк.
 */
export function TimeSeriesChart({
  rows,
  series,
  annotations = [],
  height = 240,
  title,
  formatY = formatCompact,
  allowDecimals = false,
  className,
}: TimeSeriesChartProps) {
  const days = new Set(rows.map((row) => row.x));
  const visibleAnnotations = annotations.filter((item) => days.has(item.x));
  return (
    <figure className={cn("flex flex-col gap-2", className)} aria-label={title}>
      <div className="w-full" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 320, height }}>
          <LineChart data={rows} margin={{ top: 12, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke={GRID_STROKE} />
            <XAxis
              dataKey="x"
              tickFormatter={formatDayTick}
              tick={AXIS_TICK}
              axisLine={false}
              tickLine={false}
              minTickGap={28}
            />
            <YAxis
              width={44}
              tickFormatter={formatY}
              tick={AXIS_TICK}
              axisLine={false}
              tickLine={false}
              allowDecimals={allowDecimals}
            />
            <Tooltip
              content={renderChartTooltip({ series, formatLabel: (label) => formatDay(String(label ?? "")) })}
              cursor={{ stroke: CURSOR_STROKE, strokeWidth: 1 }}
              isAnimationActive={false}
              wrapperStyle={{ outline: "none" }}
            />
            {visibleAnnotations.map((item) => (
              <ReferenceLine
                key={item.x}
                x={item.x}
                stroke={CURSOR_STROKE}
                strokeDasharray="4 3"
                label={{ value: item.label, position: "insideTopLeft", fill: "var(--color-fg-muted)", fontSize: 10 }}
              />
            ))}
            {series.map((item) => (
              <Line
                key={item.key}
                type="monotone"
                dataKey={item.key}
                name={item.label}
                stroke={chartColor(item.color)}
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                dot={false}
                activeDot={{ r: 4, fill: chartColor(item.color), stroke: SURFACE, strokeWidth: 2 }}
                connectNulls
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <ChartLegend series={series} />
    </figure>
  );
}
