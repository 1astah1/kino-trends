import { formatNumber } from "@/lib/format";

/**
 * Общие параметры графиков (docs/ARCHITECTURE.md §7.6 «Графики»). Цвета — только токены
 * «Аппаратной» из app/globals.css: слот закреплён за метрикой (1 — просмотры, 2 — переходы,
 * 3 — старты), `muted` — нейтральная серия (счётчики, спарклайны второстепенных KPI).
 * Статусные цвета в сериях не используются, двойных осей нет — только малые мультипликаторы.
 */
export type ChartColor = 1 | 2 | 3 | "muted";

export function chartColor(color: ChartColor): string {
  return color === "muted" ? "var(--color-fg-muted)" : `var(--color-chart-${color})`;
}

export interface ChartSeries {
  /** Ключ поля в строке данных. */
  key: string;
  label: string;
  color: ChartColor;
  /** Формат значения в подсказке; по умолчанию целое число. */
  format?: (value: number) => string;
}

/** Строка данных графика: `x` — категория или день `YYYY-MM-DD`, остальное — значения серий. */
export interface ChartRow {
  x: string;
  [key: string]: number | string | null;
}

const compactFmt = new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 });

/** Деления оси Y: `0`, `850`, `1,2 тыс.`, `3 млн`. */
export function formatCompact(value: number): string {
  if (!Number.isFinite(value)) return "";
  if (Math.abs(value) < 1000) return formatNumber(value, Number.isInteger(value) ? 0 : 1);
  return compactFmt.format(value);
}

/** `YYYY-MM-DD` → `24.10` для делений оси X. */
export function formatDayTick(value: string | number): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value));
  return match ? `${match[3]}.${match[2]}` : String(value);
}

/** Подписи осей: приглушённый текст, моноширинный, 11 px. */
export const AXIS_TICK = {
  fill: "var(--color-fg-muted)",
  fontSize: 11,
  fontFamily: "var(--font-mono)",
} as const;
export const GRID_STROKE = "var(--color-border)";
export const CURSOR_STROKE = "var(--color-fg-muted)";
export const SURFACE = "var(--color-surface)";
export const SURFACE_2 = "var(--color-surface-2)";
