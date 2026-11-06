"use client";

import { ArrowDownRight, ArrowUpRight, Download, Minus } from "lucide-react";
import type { ReactNode } from "react";

import { Badge, buttonVariants, Callout, Table, TableBody, TableHead, TableRow, Td, Th } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatAgo, formatDay, formatNumber, formatPct } from "@/lib/format";
import type {
  AttributionLevel,
  AttributionSplit,
  AttributionTotals,
  ChartAnnotation,
  DataFreshness,
  FunnelStep,
} from "@/lib/queries/analytics";

import { ATTRIBUTION_HINTS, ATTRIBUTION_LABELS, formatRatio, formatRubMinor, FUNNEL_STEP_LABELS } from "./labels";

/** Ссылка-кнопка на CSV текущего отчёта: `<a download>`, сервер отдаёт attachment с BOM. */
export function CsvLink({ href, label = "CSV" }: { href: string; label?: string }) {
  return (
    <a
      href={href}
      download
      className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
      data-testid="analytics-csv"
    >
      <Download className="h-3.5 w-3.5" aria-hidden="true" />
      {label}
    </a>
  );
}

/** Дельта к прошлому периоду: стрелка, проценты и цвет (у цен и брака рост — плохо). */
export function DeltaBadge({
  value,
  previous,
  goodWhenDown = false,
}: {
  value: number | null | undefined;
  previous: number | null | undefined;
  goodWhenDown?: boolean;
}) {
  if (value === null || value === undefined || previous === null || previous === undefined) return null;
  if (previous === 0) {
    return value === 0 ? (
      <Badge variant="neutral">
        <Minus className="h-3 w-3" aria-hidden="true" />0
      </Badge>
    ) : (
      <Badge variant="neutral" title="В прошлом периоде было 0">
        новое
      </Badge>
    );
  }
  const pct = ((value - previous) / Math.abs(previous)) * 100;
  if (Math.abs(pct) < 0.05) {
    return (
      <Badge variant="neutral">
        <Minus className="h-3 w-3" aria-hidden="true" />
        0%
      </Badge>
    );
  }
  const up = pct > 0;
  const good = goodWhenDown ? !up : up;
  const Icon = up ? ArrowUpRight : ArrowDownRight;
  return (
    <Badge variant={good ? "positive" : "negative"} title={`Прошлый период: ${formatNumber(previous, 2)}`}>
      <Icon className="h-3 w-3" aria-hidden="true" />
      {up ? "+" : "−"}
      {formatPct(Math.abs(pct), Math.abs(pct) < 10 ? 1 : 0)}
    </Badge>
  );
}

/** Просили engaged, а данных YouTube Analytics нет — сервер посчитал по публичным просмотрам. */
export function BasisFallbackNote({ show }: { show: boolean | undefined }) {
  if (!show) return null;
  return (
    <Callout variant="warning" title="Вовлечённых просмотров нет">
      База «вовлечённые» требует OAuth YouTube Analytics. Цифры ниже посчитаны по публичным просмотрам (они завышены на
      20–30% с 24.08.2026).
    </Callout>
  );
}

/** Отметки на временной оси (например, смена подсчёта публичных просмотров YouTube). */
export function AnnotationList({ annotations }: { annotations: ChartAnnotation[] }) {
  if (!annotations.length) return null;
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-2xs text-fg-muted">
      {annotations.map((annotation) => (
        <li key={`${annotation.code}-${annotation.date}`} className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-px border-l border-dashed border-fg-muted" aria-hidden="true" />
          <span className="tabular font-mono">{formatDay(annotation.date)}</span>
          <span>{annotation.label}</span>
        </li>
      ))}
    </ul>
  );
}

/** Плашка свежести данных мелким текстом под экраном (§7.3). */
export function FreshnessLine({ freshness, now }: { freshness: DataFreshness | undefined; now: number }) {
  if (!freshness) return null;
  const parts: string[] = [];
  parts.push(
    freshness.metrics_polled_at
      ? `метрики опрошены ${formatAgo(freshness.metrics_polled_at, now)}`
      : "метрики ещё не опрашивались",
  );
  if (freshness.public_counts_age_min !== null) {
    parts.push(`медианный возраст снимка ${formatNumber(freshness.public_counts_age_min)} мин`);
  }
  parts.push(freshness.rollup_at ? `ролл-ап ${formatAgo(freshness.rollup_at, now)}` : "ролл-ап ещё не считался");
  if (freshness.clicks_last_at) parts.push(`последний переход ${formatAgo(freshness.clicks_last_at, now)}`);
  if (freshness.conversions_last_at) {
    parts.push(`последняя конверсия ${formatAgo(freshness.conversions_last_at, now)}`);
  }
  if (freshness.publications_stale > 0) parts.push(`просрочен опрос: ${freshness.publications_stale}`);
  if (freshness.publications_manual_due > 0) parts.push(`ждут ручного ввода: ${freshness.publications_manual_due}`);
  return (
    <div className="flex flex-col gap-1 text-2xs text-fg-muted" data-testid="analytics-freshness">
      <p>
        <span className="font-mono tracking-wider uppercase">Свежесть данных</span>
        {" · "}
        {parts.join(" · ")}
      </p>
      {freshness.warnings.length ? (
        <ul className="flex flex-wrap gap-x-3 gap-y-0.5">
          {freshness.warnings.map((warning) => (
            <li key={warning.code} className="text-warning">
              {warning.message}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- атрибуция

const ATTRIBUTION_ROWS: ReadonlyArray<{ key: keyof AttributionTotals; label: string; money?: boolean }> = [
  { key: "visitors", label: "Посетители" },
  { key: "bot_starts", label: "Старты бота" },
  { key: "users", label: "Пользователи" },
  { key: "signups", label: "Регистрации" },
  { key: "first_payments", label: "Первые оплаты" },
  { key: "revenue_minor", label: "Выручка", money: true },
];
const ATTRIBUTION_LEVELS: AttributionLevel[] = ["exact", "channel", "direct"];

/** Уровни атрибуции (§6.6): в разрезы роликов попадает только точная. */
export function AttributionTable({ attribution }: { attribution: AttributionSplit }) {
  const empty = ATTRIBUTION_LEVELS.every((level) => ATTRIBUTION_ROWS.every((row) => attribution[level][row.key] === 0));
  if (empty) {
    return <p className="text-sm text-fg-muted">Конверсий за период нет — атрибуцию делить пока нечего.</p>;
  }
  return (
    <Table minWidth={420}>
      <caption className="sr-only">Уровни атрибуции</caption>
      <TableHead>
        <tr>
          <Th>Показатель</Th>
          {ATTRIBUTION_LEVELS.map((level) => (
            <Th key={level} className="text-right" title={ATTRIBUTION_HINTS[level]}>
              {ATTRIBUTION_LABELS[level]}
            </Th>
          ))}
        </tr>
      </TableHead>
      <TableBody>
        {ATTRIBUTION_ROWS.map((row) => (
          <TableRow key={row.key}>
            <Td className="text-fg-muted">{row.label}</Td>
            {ATTRIBUTION_LEVELS.map((level) => {
              const value = attribution[level][row.key];
              return (
                <Td key={level} className="tabular text-right font-mono text-xs">
                  {row.money ? formatRubMinor(value) : formatNumber(value)}
                </Td>
              );
            })}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------- воронка полосами

/** Ветка воронки: шаги полосами относительно самого большого шага, доля от прошлого и на 1000 просмотров. */
export function FunnelBranch({
  steps,
  formatCost,
}: {
  steps: FunnelStep[];
  formatCost: (value: number | null) => string;
}) {
  const max = Math.max(...steps.map((step) => step.value ?? 0), 0);
  if (max === 0) {
    return <p className="text-sm text-fg-muted">По этой ветке за период ничего не было.</p>;
  }
  return (
    <ol className="flex flex-col gap-2">
      {steps.map((step) => {
        const value = step.value ?? 0;
        const width = value > 0 ? Math.max(1.5, (value / max) * 100) : 0;
        return (
          <li key={step.code} className="flex flex-col gap-1">
            <div className="flex items-baseline justify-between gap-3 text-xs">
              <span className="text-fg">{FUNNEL_STEP_LABELS[step.code] ?? step.code}</span>
              <span className="tabular flex items-baseline gap-3 font-mono text-fg-muted">
                {step.rate_from_prev !== null ? (
                  <span title="Доля от прошлого шага">{formatRatio(step.rate_from_prev, 1)}</span>
                ) : null}
                {step.per_1k_views !== null && step.code !== "views" ? (
                  <span title="На 1000 просмотров">{formatNumber(step.per_1k_views, 2)}/1k</span>
                ) : null}
                {step.cost_per_usd !== null && step.code !== "views" ? (
                  <span title="Цена шага">{formatCost(step.cost_per_usd)}</span>
                ) : null}
                <span className="font-semibold text-fg">{step.value === null ? "—" : formatNumber(step.value)}</span>
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
              <span className="block h-full rounded-full bg-chart-1" style={{ width: `${width}%` }} />
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** Строка панели вкладки: селекты слева, CSV справа. */
export function TabToolbar({ children, right }: { children?: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {children}
      {right ? <div className="ml-auto flex items-center gap-2">{right}</div> : null}
    </div>
  );
}
