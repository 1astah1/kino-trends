"use client";

import { STAGES_BY_KIND } from "@/components/review/review-logic";
import type { Schemas } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import { formatNumber, formatUsd } from "@/lib/format";
import type { Job, JobTaskItem } from "@/lib/queries/jobs";

import { JOB_STAGE_LABELS, STAGE_PENDING_ICON, TASK_STATUS_META } from "./job-meta";

type JobStage = Schemas["JobStage"];

const TEXT_TONE = {
  neutral: "text-fg-muted",
  machine: "text-machine",
  positive: "text-positive",
  negative: "text-negative",
  accent: "text-accent",
  human: "text-human",
  warning: "text-warning",
} as const;

function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "";
  if (ms < 1000) return `${ms} мс`;
  const sec = ms / 1000;
  if (sec < 60) return `${formatNumber(sec, 1)} с`;
  return `${Math.floor(sec / 60)} мин ${Math.round(sec % 60)} с`;
}

/**
 * Таймлайн стадий ролика (§7.3 «StageTimeline»): все стадии трека по порядку, у пройденных — статус,
 * попытки, время и цена; у идущей — прогресс задачи. Клик по стадии открывает её лог.
 */
export function StageTimeline({
  job,
  tasks,
  onOpenTask,
}: {
  job: Job;
  tasks: readonly JobTaskItem[] | undefined;
  onOpenTask: (taskId: number) => void;
}) {
  const order: JobStage[] = [...STAGES_BY_KIND[job.kind]];
  if (job.stages.some((item) => item.stage === "package")) order.push("package");
  const infoByStage = new Map(job.stages.map((item) => [item.stage, item]));
  const progressByTask = new Map((tasks ?? []).map((task) => [task.task_id, task.progress]));

  return (
    <ol className="flex flex-col" aria-label="Стадии ролика" data-testid="stage-timeline">
      {order.map((stage, index) => {
        const info = infoByStage.get(stage);
        const meta = info ? TASK_STATUS_META[info.status] : null;
        const Icon = meta?.icon ?? STAGE_PENDING_ICON;
        const tone = meta ? TEXT_TONE[meta.variant ?? "neutral"] : "text-fg-muted/60";
        const current = job.stage === stage && (job.status === "processing" || job.status === "queued");
        const progress = info?.task_id ? progressByTask.get(info.task_id) : null;
        const last = index === order.length - 1;
        return (
          <li key={stage} className="relative flex gap-2 pb-2 last:pb-0">
            {!last ? <span className="absolute top-5 bottom-0 left-[9px] w-px bg-border" aria-hidden="true" /> : null}
            <Icon
              className={cn(
                "relative mt-0.5 h-[18px] w-[18px] shrink-0 bg-surface",
                tone,
                info?.status === "running" && "animate-spin",
              )}
              aria-hidden="true"
            />
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="flex items-baseline justify-between gap-2">
                {info?.task_id ? (
                  <button
                    type="button"
                    onClick={() => onOpenTask(info.task_id as number)}
                    className={cn("text-left text-sm hover:underline", current ? "font-medium text-fg" : "text-fg")}
                    title="Открыть лог задачи"
                  >
                    {JOB_STAGE_LABELS[stage]}
                  </button>
                ) : (
                  <span className="text-sm text-fg-muted">{JOB_STAGE_LABELS[stage]}</span>
                )}
                <span className="shrink-0 font-mono text-2xs text-fg-muted tabular">
                  {info ? (
                    <>
                      {info.attempts > 1 ? `×${info.attempts} · ` : ""}
                      {formatMs(info.duration_ms)}
                      {info.cost_usd > 0 ? ` · ${formatUsd(info.cost_usd, 4)}` : ""}
                    </>
                  ) : null}
                </span>
              </div>
              <span className="sr-only">{meta ? meta.label : "ещё не начиналась"}</span>
              {info?.status === "running" && typeof progress === "number" ? (
                <span className="mt-1 flex items-center gap-1.5">
                  <span className="h-1 flex-1 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
                    <span
                      className="block h-full bg-machine"
                      style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
                    />
                  </span>
                  <span className="font-mono text-2xs text-machine tabular">{Math.round(progress)}%</span>
                </span>
              ) : null}
              {info?.error ? (
                <span className="mt-0.5 line-clamp-2 text-xs break-words text-negative" title={info.error}>
                  {info.error}
                </span>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
