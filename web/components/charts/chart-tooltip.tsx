"use client";

import type { ReactNode } from "react";
import type { TooltipContentProps } from "recharts";

import { formatNumber } from "@/lib/format";

import { type ChartSeries, chartColor } from "./chart-utils";

export interface ChartTooltipOptions {
  series: ChartSeries[];
  /** Заголовок подсказки из значения оси X (например, день). */
  formatLabel?: (label: string | number | undefined) => ReactNode;
}

// Дженерики по умолчанию (`ValueType, NameType`): именно их ждёт `content` у `<Tooltip>`.
type TooltipSlice = Pick<TooltipContentProps, "active" | "payload" | "label">;

function toNumber(raw: unknown): number | null {
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;
  if (typeof raw === "string" && raw.trim() !== "") {
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/**
 * Подсказка графика: одна на все серии в точке X; значение главное, имя серии — вторым,
 * ключ серии — короткий штрих цвета серии. Текст всегда токенами текста, не цветом данных.
 */
export function ChartTooltipContent({ active, payload, label, series, formatLabel }: TooltipSlice & ChartTooltipOptions) {
  if (!active || !payload || payload.length === 0) return null;
  const byKey = new Map(series.map((item) => [item.key, item]));
  const rows: Array<{ meta: ChartSeries; value: number | null }> = [];
  for (const entry of payload) {
    if (entry.dataKey === undefined || entry.hide) continue;
    const meta = byKey.get(String(entry.dataKey));
    if (!meta) continue;
    rows.push({ meta, value: toNumber(entry.value) });
  }
  if (rows.length === 0) return null;
  return (
    <div className="rounded-md border border-border bg-surface px-2.5 py-2 text-xs text-fg">
      <p className="mb-1 font-mono text-2xs text-fg-muted">{formatLabel ? formatLabel(label) : label}</p>
      <ul className="flex flex-col gap-0.5">
        {rows.map(({ meta, value }) => (
          <li key={meta.key} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="h-0.5 w-3 shrink-0 rounded-full"
              style={{ background: chartColor(meta.color) }}
            />
            <span className="tabular font-mono font-semibold">
              {value === null ? "—" : (meta.format ?? ((v: number) => formatNumber(v)))(value)}
            </span>
            <span className="text-fg-muted">{meta.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** `content` для `<Tooltip>` recharts: замыкание на описание серий. */
export function renderChartTooltip(options: ChartTooltipOptions) {
  return function ChartTooltipRenderer(props: TooltipContentProps) {
    return <ChartTooltipContent active={props.active} payload={props.payload} label={props.label} {...options} />;
  };
}
