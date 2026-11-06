"use client";

import type { Key } from "react";
import { Line, LineChart, ResponsiveContainer } from "recharts";

import { cn } from "@/lib/cn";

import { type ChartColor, chartColor, SURFACE } from "./chart-utils";

export interface SparklineProps {
  values: Array<number | null>;
  color: ChartColor;
  /** Высота, px; ширина — по контейнеру. */
  height?: number;
  /** Подпись для скринридера; без неё спарклайн декоративный (`aria-hidden`). */
  label?: string;
  className?: string;
}

interface DotRenderProps {
  cx?: number;
  cy?: number;
  index?: number;
  key?: Key | null;
}

/**
 * Спарклайн в плитке KPI: линия 1,5 px без осей и подсказки (значение и дельта уже в плитке),
 * точка с кольцом цвета поверхности на последнем дне. Без анимации — плиток на экране десяток.
 */
export function Sparkline({ values, color, height = 28, label, className }: SparklineProps) {
  const rows = values.map((v, i) => ({ i, v }));
  let lastIndex = -1;
  for (let i = values.length - 1; i >= 0; i -= 1) {
    if (values[i] !== null) {
      lastIndex = i;
      break;
    }
  }
  const stroke = chartColor(color);
  const renderDot = (props: DotRenderProps) => {
    const { cx, cy, index, key } = props;
    if (index !== lastIndex || cx === undefined || cy === undefined) return <g key={key ?? index} />;
    return <circle key={key ?? index} cx={cx} cy={cy} r={2.5} fill={stroke} stroke={SURFACE} strokeWidth={1.5} />;
  };
  return (
    <span
      className={cn("block w-full", className)}
      style={{ height }}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {rows.length > 1 ? (
        <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 120, height }}>
          <LineChart data={rows} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
            <Line
              type="monotone"
              dataKey="v"
              stroke={stroke}
              strokeWidth={1.5}
              strokeLinecap="round"
              dot={renderDot}
              activeDot={false}
              connectNulls
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      ) : null}
    </span>
  );
}
