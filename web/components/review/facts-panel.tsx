"use client";

import { CircleCheck, CircleX, Copy, History, ImageOff, Layers, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useEffect, useRef } from "react";

import { OriginalityReportCard } from "@/components/clipping/originality-report";
import { type PlayerTimeStore } from "@/components/player/player-time";
import { useTimeSelector } from "@/components/player/overlay-svg";
import {
  JOB_KIND_LABELS,
  JOB_STATUS_META,
  PLATFORM_LABELS,
  QC_CHECK_LABELS,
  REVIEW_FLAG_LABELS,
  SEVERITY_META,
  severityRank,
} from "@/components/production/job-meta";
import { Badge, StatusBadge } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import { formatPct, formatUsd } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import type { Job } from "@/lib/queries/jobs";
import type { RejectReasonItem, ReviewContext } from "@/lib/queries/review";

import { ChannelPicker } from "./channel-picker";
import { activeWordIndex, DECISION_DONE_LABELS } from "./review-logic";

type ReviewWord = Schemas["ReviewWord"];

function Section({ title, children, testId }: { title: string; children: ReactNode; testId?: string }) {
  return (
    <section className="flex flex-col gap-2 border-b border-border px-3 py-3 last:border-b-0" data-testid={testId}>
      <h2 className="font-mono text-2xs tracking-wider text-fg-muted uppercase">{title}</h2>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------- караоке

function KaraokeScript({
  words,
  fallbackText,
  timeStore,
  onSeek,
}: {
  words: readonly ReviewWord[];
  fallbackText: string | null;
  timeStore: PlayerTimeStore;
  onSeek: (sec: number) => void;
}) {
  const active = useTimeSelector(timeStore, (t) => activeWordIndex(words, t));
  const boxRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (active < 0) return;
    const node = boxRef.current?.querySelector<HTMLElement>(`[data-word="${active}"]`);
    node?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (words.length === 0) {
    return fallbackText ? (
      <p className="text-sm whitespace-pre-line text-fg">{fallbackText}</p>
    ) : (
      <p className="text-sm text-fg-muted">Текста озвучки нет.</p>
    );
  }
  return (
    <p ref={boxRef} className="max-h-48 overflow-y-auto text-sm leading-6 text-fg" data-testid="karaoke">
      {words.map((word, index) => (
        <span key={`${index}-${word.start_sec}`}>
          <button
            type="button"
            data-word={index}
            onClick={() => onSeek(word.start_sec)}
            className={cn(
              "rounded-sm px-0.5 transition-colors duration-100",
              index === active ? "bg-accent text-accent-fg" : index < active ? "text-fg" : "text-fg-muted",
            )}
            tabIndex={-1}
          >
            {word.word}
          </button>{" "}
        </span>
      ))}
    </p>
  );
}

// ---------------------------------------------------------------- секции

function FlagsList({ flags }: { flags: ReviewContext["flags"] }) {
  if (flags.length === 0) {
    return (
      <p className="inline-flex items-center gap-1.5 text-sm text-positive">
        <CircleCheck className="h-4 w-4" aria-hidden="true" />
        Флагов нет
      </p>
    );
  }
  const sorted = [...flags].sort((a, b) => severityRank(b.severity) - severityRank(a.severity));
  return (
    <ul className="flex flex-col gap-1.5" data-testid="review-flags">
      {sorted.map((flag, index) => {
        const meta = SEVERITY_META[flag.severity];
        const Icon = meta.icon;
        return (
          <li key={`${flag.code}-${index}`} className={cn("flex items-start gap-2 rounded-md px-2 py-1.5", meta.bg)}>
            <Icon className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", meta.text)} aria-hidden="true" />
            <div className="min-w-0 text-sm">
              <span className="font-medium text-fg">{REVIEW_FLAG_LABELS[flag.code] ?? flag.code}</span>
              <span className="sr-only"> ({meta.label})</span>
              {flag.detail ? <span className="block text-xs break-words text-fg-muted">{flag.detail}</span> : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function QcChips({ qc }: { qc: ReviewContext["qc"] }) {
  if (!qc) return <p className="text-sm text-fg-muted">Отчёта QC нет.</p>;
  if (qc.checks.length === 0) {
    return (
      <p className="text-sm text-fg-muted">{qc.passed ? "QC пройден" : "QC не пройден"}, проверок в отчёте нет.</p>
    );
  }
  return (
    <ul className="flex flex-wrap gap-1.5" data-testid="review-qc">
      {qc.checks.map((check) => {
        const Icon = check.ok ? CircleCheck : CircleX;
        const title = [check.measured && `измерено: ${check.measured}`, check.threshold && `порог: ${check.threshold}`]
          .filter(Boolean)
          .join(" · ");
        return (
          <li key={check.code}>
            <Badge variant={check.ok ? "positive" : "negative"} outline title={title || undefined}>
              <Icon className="h-3 w-3" aria-hidden="true" />
              {QC_CHECK_LABELS[check.code] ?? check.code}
              {check.measured ? <span className="font-mono text-2xs opacity-80">{check.measured}</span> : null}
            </Badge>
          </li>
        );
      })}
    </ul>
  );
}

const LAYOUT_ZONE_LABELS: Record<string, string> = {
  source_zone: "субтитры",
  author_zone: "реплики автора",
  title_zone: "заголовок",
  onscreen_zone: "тезисы",
};

function BannerFacts({ banner }: { banner: ReviewContext["banner"] }) {
  const warnings = [...banner.warnings].sort((a, b) => severityRank(b.severity) - severityRank(a.severity));
  const overlap = banner.captions_overlap ?? [];
  return (
    <div className="flex flex-col gap-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {banner.variant_code ? (
          <Badge variant="accent" outline>
            <Layers className="h-3 w-3" aria-hidden="true" />
            вариант {banner.variant_code}
          </Badge>
        ) : (
          <Badge variant="neutral">вариант не назначен</Badge>
        )}
        <span className="text-xs text-fg-muted">
          креативов в кадре: <span className="font-mono tabular">{banner.creative_sha256s.length}</span>
        </span>
        {banner.fallback_used ? (
          <Badge variant="warning">
            <ImageOff className="h-3 w-3" aria-hidden="true" />
            запасная плашка
          </Badge>
        ) : null}
        {banner.layout_manual ? (
          <Badge variant="human" outline>
            раскладка вручную
          </Badge>
        ) : null}
      </div>
      {overlap.length ? (
        <p className="inline-flex items-start gap-1.5 text-xs text-negative" data-testid="review-layout-overlap">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>
            В рендере текст налезает на баннер: {overlap.map((zone) => LAYOUT_ZONE_LABELS[zone] ?? zone).join(", ")}.
            Поправьте в режиме «Раскладка» и перерендерите.
          </span>
        </p>
      ) : null}
      {warnings.length ? (
        <ul className="flex flex-col gap-1">
          {warnings.map((warning, index) => {
            const meta = SEVERITY_META[warning.severity];
            const Icon = meta.icon;
            return (
              <li key={`${warning.code}-${index}`} className="flex items-start gap-1.5 text-xs">
                <Icon className={cn("mt-0.5 h-3 w-3 shrink-0", meta.text)} aria-hidden="true" />
                <span className="text-fg">
                  {warning.message || warning.code}
                  {warning.layer_id ? <span className="ml-1 font-mono text-fg-muted">[{warning.layer_id}]</span> : null}
                </span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

function SimilarityCard({ similarity }: { similarity: ReviewContext["similarity"] }) {
  if (!similarity || similarity.score === null) {
    return <p className="text-sm text-fg-muted">Похожих своих роликов не найдено.</p>;
  }
  const high = similarity.score >= 0.9;
  return (
    <div className="flex items-center justify-between gap-2 text-sm">
      <span className={cn("inline-flex items-center gap-1.5", high ? "text-warning" : "text-fg")}>
        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
        сходство <span className="font-mono tabular">{formatPct(similarity.score * 100, 0)}</span>
        <span className="text-xs text-fg-muted">({similarity.method})</span>
      </span>
      {similarity.nearest_job_id ? (
        <Link
          href={routeHref("/production", { job: String(similarity.nearest_job_id) })}
          className="text-xs text-accent hover:underline"
        >
          ролик #{similarity.nearest_job_id}
        </Link>
      ) : null}
    </div>
  );
}

function AttemptsCompare({
  attempts,
  reasons,
}: {
  attempts: ReviewContext["previous_attempts"];
  reasons: readonly RejectReasonItem[];
}) {
  if (attempts.length === 0) return <p className="text-sm text-fg-muted">Первая попытка.</p>;
  return (
    <ol className="flex flex-col gap-1.5">
      {attempts.map((attempt, index) => {
        const reason = attempt.reject_reason
          ? (reasons.find((item) => item.code === attempt.reject_reason)?.label ?? attempt.reject_reason)
          : null;
        return (
          <li key={`${attempt.job_id}-${index}`} className="flex items-start gap-2 text-sm">
            <History className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fg-muted" aria-hidden="true" />
            <div className="min-w-0">
              <span className="text-fg">{DECISION_DONE_LABELS[attempt.decision]}</span>
              {reason ? <span className="text-fg-muted"> · {reason}</span> : null}
              <Link
                href={routeHref("/production", { job: String(attempt.job_id) })}
                className="ml-1 font-mono text-2xs text-accent hover:underline"
              >
                #{attempt.job_id}
              </Link>
              {attempt.comment ? <p className="text-xs break-words text-fg-muted">«{attempt.comment}»</p> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------- панель

/** Нарезка: источник и права, откуда авторский текст, финальный отчёт об оригинальности (M5). */
function ClipFacts({ context }: { context: ReviewContext }) {
  const source = context.source;
  return (
    <Section title="Оригинальность" testId="review-originality-section">
      {source ? (
        <p className="text-xs text-fg-muted">
          Источник: <span className="text-fg">{source.title}</span>
          {source.license ? <span className="font-mono"> · {source.license}</span> : null}
          {source.attribution_text ? (
            <span className="mt-1 block break-words text-2xs">Атрибуция: {source.attribution_text}</span>
          ) : null}
        </p>
      ) : null}
      {context.author_script?.label ? (
        <p className="text-xs text-fg-muted">
          Авторский текст: <span className="text-fg">{context.author_script.label}</span>
          {context.author_script.needs_operator_edit ? (
            <Badge variant="warning" className="ml-1">
              перепишите своими словами
            </Badge>
          ) : null}
        </p>
      ) : null}
      {context.originality ? (
        <OriginalityReportCard report={context.originality} compact />
      ) : (
        <p className="text-xs text-fg-muted">Отчёт появится после стадии QC</p>
      )}
      {source ? (
        <Link
          href={routeHref("/clipping/source", { id: String(source.source_id) })}
          className="self-start text-xs text-accent hover:underline"
        >
          Открыть в студии
        </Link>
      ) : null}
    </Section>
  );
}

export function FactsPanel({
  context,
  job,
  reasons,
  timeStore,
  onSeek,
  canOperate = false,
}: {
  context: ReviewContext;
  job?: Job;
  reasons: readonly RejectReasonItem[];
  timeStore: PlayerTimeStore;
  onSeek: (sec: number) => void;
  /** Можно менять канал ролика (роль оператора). */
  canOperate?: boolean;
}) {
  const info = context.job;
  return (
    <div className="flex flex-col" data-testid="review-facts">
      <Section title="Ролик">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm text-fg" data-testid="review-current-uid">
            {info.video_uid}
          </span>
          <StatusBadge status={info.status} map={JOB_STATUS_META} />
          <Badge variant="neutral">{JOB_KIND_LABELS[info.kind]}</Badge>
          {info.attempt_count > 1 ? <Badge variant="warning">попытка {info.attempt_count}</Badge> : null}
        </div>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
          <dt className="text-fg-muted">Канал</dt>
          <dd className="min-w-0 truncate text-fg">
            {info.kind === "clip" && job ? (
              <ChannelPicker
                jobId={info.id}
                platform={job.channel.platform}
                fallbackTitle={job.channel.title ?? info.channel_id}
                canOperate={canOperate}
              />
            ) : (
              <>
                {job?.channel.title ?? info.channel_id}
                {job ? <span className="text-fg-muted"> · {PLATFORM_LABELS[job.channel.platform]}</span> : null}
              </>
            )}
          </dd>
          <dt className="text-fg-muted">Ниша</dt>
          <dd className="truncate font-mono text-fg">{info.niche_id}</dd>
          <dt className="text-fg-muted">Продукт</dt>
          <dd className="truncate font-mono text-fg">{info.product_id ?? "—"}</dd>
          <dt className="text-fg-muted">Себестоимость</dt>
          <dd className="font-mono text-fg tabular">{formatUsd(context.cost_usd, 4)}</dd>
          {context.script.topic ? (
            <>
              <dt className="text-fg-muted">Тема</dt>
              <dd className="text-fg">{context.script.topic}</dd>
            </>
          ) : null}
          {context.script.hook_pattern ? (
            <>
              <dt className="text-fg-muted">Хук</dt>
              <dd className="font-mono text-fg">{context.script.hook_pattern}</dd>
            </>
          ) : null}
        </dl>
        <Link
          href={routeHref("/production", { job: String(info.id) })}
          className="self-start text-xs text-accent hover:underline"
        >
          Открыть в конвейере
        </Link>
      </Section>
      <Section title="Флаги" testId="review-flags-section">
        <FlagsList flags={context.flags} />
      </Section>
      {info.kind === "clip" ? <ClipFacts context={context} /> : null}
      <Section title="Текст">
        <KaraokeScript
          words={context.script.words}
          fallbackText={context.script.voiceover_text}
          timeStore={timeStore}
          onSeek={onSeek}
        />
        {context.script.cta_text ? (
          <p className="text-xs text-fg-muted">
            CTA: <span className="text-fg">{context.script.cta_text}</span>
          </p>
        ) : null}
      </Section>
      <Section title="QC">
        <QcChips qc={context.qc} />
      </Section>
      <Section title="Баннер">
        <BannerFacts banner={context.banner} />
      </Section>
      <Section title="Похожесть">
        <SimilarityCard similarity={context.similarity} />
      </Section>
      <Section title="Прошлые попытки">
        <AttemptsCompare attempts={context.previous_attempts} reasons={reasons} />
      </Section>
    </div>
  );
}
