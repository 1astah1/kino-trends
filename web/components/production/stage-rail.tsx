"use client";

import { ChevronRight } from "lucide-react";

import { Skeleton } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import type { StageCount } from "@/lib/queries/jobs";

import { JOB_STAGE_LABELS, PROCESSING_STAGE_ORDER } from "./job-meta";

type JobStatus = Schemas["JobStatus"];
type JobStage = Schemas["JobStage"];

type Tone = "neutral" | "machine" | "human" | "negative" | "warning" | "positive";

export interface RailChip {
  key: string;
  label: string;
  count: number;
  status: JobStatus;
  stage: JobStage | null;
  tone: Tone;
  group: "before" | "machine" | "human" | "after";
}

const TONE: Record<Tone, { active: string; count: string }> = {
  neutral: { active: "border-fg-muted/60 bg-surface-2", count: "text-fg" },
  machine: { active: "border-machine/60 bg-machine/10", count: "text-machine" },
  human: { active: "border-human/60 bg-human/10", count: "text-human" },
  negative: { active: "border-negative/60 bg-negative/10", count: "text-negative" },
  warning: { active: "border-warning/60 bg-warning/10", count: "text-warning" },
  positive: { active: "border-positive/60 bg-positive/10", count: "text-positive" },
};

/** Всегда видимые стадии генерации; стадии нарезки — когда в них что-то есть. */
const ALWAYS_STAGES = new Set<JobStage>(["script", "voice", "visuals", "render", "qc"]);

/**
 * Лента стадий-фильтров (§7.3): черновики и очередь → стадии в работе (фиолет, работает машина) →
 * приёмка, провал QC, доработка, сбой (янтарь и красный: ход оператора) → одобренные.
 */
export function buildRail(counts: readonly StageCount[]): RailChip[] {
  const byStatus = (status: JobStatus) =>
    counts.filter((item) => item.status === status).reduce((sum, item) => sum + item.count, 0);
  const chips: RailChip[] = [
    {
      key: "draft",
      label: "Черновики",
      count: byStatus("draft"),
      status: "draft",
      stage: null,
      tone: "neutral",
      group: "before",
    },
    {
      key: "queued",
      label: "В очереди",
      count: byStatus("queued"),
      status: "queued",
      stage: null,
      tone: "machine",
      group: "before",
    },
  ];
  for (const stage of PROCESSING_STAGE_ORDER) {
    const count = counts
      .filter((item) => item.status === "processing" && item.stage === stage)
      .reduce((sum, item) => sum + item.count, 0);
    if (count === 0 && !ALWAYS_STAGES.has(stage)) continue;
    chips.push({
      key: `processing:${stage}`,
      label: JOB_STAGE_LABELS[stage],
      count,
      status: "processing",
      stage,
      tone: "machine",
      group: "machine",
    });
  }
  chips.push(
    {
      key: "pending_review",
      label: "Ждёт приёмки",
      count: byStatus("pending_review"),
      status: "pending_review",
      stage: null,
      tone: "human",
      group: "human",
    },
    {
      key: "qc_failed",
      label: "Не прошёл QC",
      count: byStatus("qc_failed"),
      status: "qc_failed",
      stage: null,
      tone: "negative",
      group: "human",
    },
    {
      key: "rework",
      label: "На доработке",
      count: byStatus("rework"),
      status: "rework",
      stage: null,
      tone: "warning",
      group: "human",
    },
    {
      key: "failed",
      label: "Сбой",
      count: byStatus("failed"),
      status: "failed",
      stage: null,
      tone: "negative",
      group: "human",
    },
    {
      key: "approved",
      label: "Одобрены",
      count: byStatus("approved"),
      status: "approved",
      stage: null,
      tone: "positive",
      group: "after",
    },
  );
  return chips;
}

export function StageRail({
  counts,
  isLoading,
  status,
  stage,
  onSelect,
}: {
  counts: readonly StageCount[] | undefined;
  isLoading: boolean;
  status: JobStatus | null;
  stage: JobStage | null;
  onSelect: (filter: { status: JobStatus | null; stage: JobStage | null }) => void;
}) {
  if (isLoading && !counts) {
    return <Skeleton className="h-9 w-full" />;
  }
  const chips = buildRail(counts ?? []);
  const total = (counts ?? []).reduce((sum, item) => sum + item.count, 0);
  const noneActive = status === null && stage === null;

  return (
    <nav aria-label="Лента стадий" className="-mx-1 overflow-x-auto px-1" data-testid="stage-rail">
      <ul className="flex min-w-max items-center gap-1">
        <li>
          <RailButton
            label="Все"
            count={total}
            active={noneActive}
            tone="neutral"
            onClick={() => onSelect({ status: null, stage: null })}
          />
        </li>
        {chips.map((chip, index) => {
          const prev = chips[index - 1];
          const active = chip.status === status && (chip.stage ?? null) === (stage ?? null);
          return (
            <li key={chip.key} className="flex items-center gap-1">
              {prev && prev.group !== chip.group ? (
                <ChevronRight className="h-3.5 w-3.5 text-fg-muted/50" aria-hidden="true" />
              ) : null}
              <RailButton
                label={chip.label}
                count={chip.count}
                active={active}
                tone={chip.tone}
                onClick={() =>
                  onSelect(active ? { status: null, stage: null } : { status: chip.status, stage: chip.stage })
                }
              />
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

function RailButton({
  label,
  count,
  active,
  tone,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  tone: Tone;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs transition-colors duration-150",
        active ? TONE[tone].active : "border-border text-fg-muted hover:bg-surface-2 hover:text-fg",
        count === 0 && !active && "opacity-60",
      )}
    >
      <span className={cn(active && "text-fg")}>{label}</span>
      <span className={cn("tabular font-mono font-semibold", count > 0 ? TONE[tone].count : "text-fg-muted")}>
        {count}
      </span>
    </button>
  );
}
