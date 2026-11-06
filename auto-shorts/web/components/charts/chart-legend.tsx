import { cn } from "@/lib/cn";

import { type ChartSeries, chartColor } from "./chart-utils";

/**
 * Легенда под графиком: нужна от двух серий (одна серия названа заголовком блока).
 * Ключ повторяет марку: штрих для линий, квадрат для столбцов; подпись — токеном текста.
 */
export function ChartLegend({
  series,
  kind = "line",
  className,
}: {
  series: ChartSeries[];
  kind?: "line" | "bar";
  className?: string;
}) {
  if (series.length < 2) return null;
  return (
    <ul className={cn("flex flex-wrap gap-x-3 gap-y-1 text-xs text-fg-muted", className)} aria-label="Легенда">
      {series.map((item) => (
        <li key={item.key} className="inline-flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className={kind === "line" ? "h-0.5 w-3 rounded-full" : "h-2.5 w-2.5 rounded-sm"}
            style={{ background: chartColor(item.color) }}
          />
          {item.label}
        </li>
      ))}
    </ul>
  );
}
