"use client";

import { LoaderCircle } from "lucide-react";

import { StatusBadge } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";

import { formatSeconds } from "./clip-math";
import { CHECK_STATUS_META, RIGHTS_TYPE_LABELS, VERDICT_META } from "./labels";

type OriginalityReportOut = Schemas["OriginalityReportOut"];

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

/**
 * Индикатор 70/30 (§6.3.8): доля авторского вклада против порога `clip_author_share_min` (0,30).
 * Для чужих прав порог — гейт (иначе qc_failed и пакет не соберётся), для своих — только справка.
 */
export function AuthorShareMeter({ report }: { report: OriginalityReportOut }) {
  const s = report.summary;
  const value = Math.max(0, Math.min(1, s.author_share));
  const min = Math.max(0, Math.min(1, s.author_share_min));
  const ok = value >= min;
  const tone = ok ? "bg-positive" : s.gate_applies ? "bg-negative" : "bg-warning";
  return (
    <div className="flex flex-col gap-1" data-testid="author-share-meter">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="text-fg-muted">Авторский вклад</span>
        <span className="font-mono text-sm font-semibold text-fg tabular" data-testid="author-share-value">
          {pct(value)} <span className="text-xs font-normal text-fg-muted">/ порог {pct(min)}</span>
        </span>
      </div>
      <div
        className="relative h-2 overflow-hidden rounded-full bg-surface-2"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(value * 100)}
        aria-label="Доля авторского вклада"
      >
        <div className={cn("h-full transition-[width] duration-150", tone)} style={{ width: `${value * 100}%` }} />
        <div className="absolute inset-y-0 w-0.5 bg-fg" style={{ left: `${min * 100}%` }} aria-hidden="true" />
      </div>
      <span className="text-2xs text-fg-muted">
        {s.gate_applies
          ? `Чужие права (${s.rights_type ? RIGHTS_TYPE_LABELS[s.rights_type] : "—"}): порог обязателен — ниже него ролик не пройдёт QC и не соберётся в пакет.`
          : "Своё видео: порог только показывается."}
      </span>
    </div>
  );
}

function Breakdown({ report }: { report: OriginalityReportOut }) {
  const a = report.author_share;
  const rows: Array<[string, number]> = [
    ["Только автор (хук, вставки-стоп-кадры, вывод)", a.author_only_sec],
    ["Голос автора поверх приглушённого исходника", a.voice_over_sec],
    ["Свой текст на экране", a.text_sec],
    ["Записано своим голосом", a.own_voice_sec],
    ["Стоп-кадры", a.freeze_sec],
  ];
  return (
    <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 text-2xs">
      {rows.map(([label, sec]) => (
        <div key={label} className="contents">
          <dt className="text-fg-muted">{label}</dt>
          <dd className="text-right font-mono text-fg tabular">{formatSeconds(sec)}</dd>
        </div>
      ))}
      <dt className="text-fg-muted">Длительность ролика</dt>
      <dd className="text-right font-mono text-fg tabular">{formatSeconds(a.duration_sec)}</dd>
    </dl>
  );
}

/**
 * Отчёт об оригинальности (OWNER-DECISIONS п. 5): вердикт, доля авторского вклада, проверки
 * с порогами и подсказки по-русски. Один компонент для оценки в студии и для финального отчёта
 * (приёмка, конвейер). `compact` — без разбивки долей.
 */
export function OriginalityReportCard({
  report,
  loading,
  compact,
  className,
}: {
  report: OriginalityReportOut;
  loading?: boolean;
  compact?: boolean;
  className?: string;
}) {
  const checks = [...report.checks].sort((a, b) => rank(b.status) - rank(a.status));
  return (
    <div
      className={cn("flex flex-col gap-2.5", className)}
      data-testid="originality-report"
      data-verdict={report.verdict}
      data-stage={report.stage}
    >
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={report.verdict} map={VERDICT_META} />
        <span className="text-sm text-fg">{report.verdict_label}</span>
        {loading ? <LoaderCircle className="h-3.5 w-3.5 animate-spin text-fg-muted" aria-label="Пересчёт" /> : null}
        <span className="ml-auto font-mono text-2xs text-fg-muted">
          {report.stage === "estimate" ? "оценка до рендера" : "финальный рендер"}
          {report.computed_at ? ` · ${formatDateTime(report.computed_at, { short: true })}` : ""}
        </span>
      </div>
      <AuthorShareMeter report={report} />
      {!compact ? <Breakdown report={report} /> : null}
      {report.suggestions.length ? (
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-fg">Что сделать</span>
          <ol className="flex list-decimal flex-col gap-0.5 pl-4 text-xs text-fg" data-testid="originality-suggestions">
            {report.suggestions.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ol>
        </div>
      ) : null}
      <ul className="flex flex-col gap-1" aria-label="Проверки оригинальности">
        {checks.map((check) => (
          <li key={check.code} className="flex flex-col gap-0.5 text-xs">
            <span className="flex items-center gap-1.5">
              <StatusBadge status={check.status} map={CHECK_STATUS_META} />
              <span className="text-fg">{check.title}</span>
              {check.value !== null && check.value !== undefined && typeof check.value !== "object" ? (
                <span className="ml-auto font-mono text-2xs text-fg-muted tabular">
                  {formatValue(check.value)}
                  {check.threshold !== null && check.threshold !== undefined && typeof check.threshold !== "object"
                    ? ` / ${formatValue(check.threshold)}`
                    : ""}
                </span>
              ) : null}
            </span>
            {check.detail ? <span className="pl-1 text-2xs text-fg-muted">{check.detail}</span> : null}
          </li>
        ))}
      </ul>
      {report.notes.length ? (
        <ul className="flex flex-col gap-0.5 text-2xs text-fg-muted">
          {report.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function rank(status: string): number {
  return status === "fail" ? 4 : status === "warn" ? 3 : status === "pass" ? 2 : status === "info" ? 1 : 0;
}

function formatValue(value: unknown): string {
  if (typeof value === "number") {
    if (value > 0 && value < 1) return `${Math.round(value * 100)}%`;
    return value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
  }
  if (typeof value === "boolean") return value ? "да" : "нет";
  return String(value);
}
