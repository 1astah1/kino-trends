"use client";

import { X } from "lucide-react";
import { useState } from "react";

import { SectionError } from "@/components/common/section-error";
import { MiniPlayer } from "@/components/player/mini-player";
import { formatTimecode } from "@/components/review/review-logic";
import { Badge, Callout, Skeleton, StatusBadge, tabPanelId, Tabs } from "@/components/ui";
import { formatDateTime, formatUsd } from "@/lib/format";
import { useJob, useJobTasks } from "@/lib/queries/jobs";

import { JobActions } from "./job-actions";
import { JOB_KIND_LABELS, JOB_STATUS_META, jobStageLabel, PLATFORM_LABELS } from "./job-meta";
import { CostTab, FilesTab, HistoryTab, QcTab, ScenesTab, ScriptTab } from "./job-tabs";
import { StageTimeline } from "./stage-timeline";
import { TaskLogPanel } from "./task-log";

export const JOB_TABS = ["script", "scenes", "qc", "cost", "logs", "files", "history"] as const;
export type JobTab = (typeof JOB_TABS)[number];

const TAB_LABELS: Record<JobTab, string> = {
  script: "Сценарий",
  scenes: "Сцены",
  qc: "QC",
  cost: "Стоимость",
  logs: "Логи",
  files: "Файлы",
  history: "История",
};

const ID_PREFIX = "job-detail";

/**
 * Панель ролика (§7.3 «JobDetailPane»): шапка с действиями, мини-плеер, таймлайн стадий и вкладки
 * Сценарий / Сцены / QC / Стоимость / Логи / Файлы / История (`?tab=`).
 */
export function JobDetailPane({
  jobId,
  tab,
  onTabChange,
  onClose,
  onOpenJob,
}: {
  jobId: number;
  tab: JobTab;
  onTabChange: (tab: JobTab) => void;
  onClose: () => void;
  onOpenJob: (id: number) => void;
}) {
  const job = useJob(jobId);
  const tasks = useJobTasks(jobId);
  const [logTask, setLogTask] = useState<{ jobId: number; taskId: number } | null>(null);
  const logTaskId = logTask?.jobId === jobId ? logTask.taskId : null;

  if (job.isPending) {
    return (
      <div className="flex flex-col gap-3 p-4" aria-busy="true" aria-label="Загрузка ролика">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (job.isError) {
    return (
      <div className="flex flex-col gap-3 p-4">
        <div className="flex justify-end">
          <CloseButton onClose={onClose} />
        </div>
        <SectionError error={job.error} onRetry={() => void job.refetch()} title="Ролик не загрузился" />
      </div>
    );
  }
  const data = job.data;
  const tabs = JOB_TABS.filter((item) => item !== "scenes" || data.kind === "generate");
  const currentTab: JobTab = tabs.includes(tab) ? tab : "script";

  return (
    <div className="flex flex-col gap-3 p-3 md:p-4" data-testid="job-detail" data-job-id={data.id}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-mono text-base font-semibold">{data.video_uid}</h2>
            <StatusBadge status={data.status} map={JOB_STATUS_META} />
            <Badge variant="neutral">{JOB_KIND_LABELS[data.kind]}</Badge>
            {data.stage ? <span className="text-xs text-fg-muted">стадия «{jobStageLabel(data.stage)}»</span> : null}
          </div>
          <p className="text-xs text-fg-muted">
            {data.channel.title ?? data.channel.id} · {PLATFORM_LABELS[data.channel.platform]} · #{data.id} · создан{" "}
            {formatDateTime(data.created_at, { short: true })} · {formatUsd(data.cost_usd, 4)}
            {data.duration_sec !== null ? ` · ${formatTimecode(data.duration_sec)}` : ""}
          </p>
        </div>
        <CloseButton onClose={onClose} />
      </div>

      {data.status === "failed" && (data.error || data.error_class) ? (
        <Callout variant="negative" title={`Сбой на стадии «${jobStageLabel(data.stage)}»`}>
          <span className="break-words">
            {data.error_class ? <span className="font-mono">{data.error_class}: </span> : null}
            {data.error}
          </span>
        </Callout>
      ) : null}

      <JobActions job={data} onOpenJob={onOpenJob} />

      <div className="grid gap-3 sm:grid-cols-[144px_minmax(0,1fr)]">
        <MiniPlayer
          src={data.media.preview_url ?? data.media.final_url}
          poster={data.media.poster_url}
          label={`Превью ${data.video_uid}`}
          className="mx-auto max-w-[180px] sm:max-w-none"
        />
        <StageTimeline
          job={data}
          tasks={tasks.data}
          onOpenTask={(taskId) => {
            setLogTask({ jobId, taskId });
            onTabChange("logs");
          }}
        />
      </div>

      <Tabs
        idPrefix={ID_PREFIX}
        label="Разделы ролика"
        value={currentTab}
        onChange={(value) => onTabChange(value as JobTab)}
        items={tabs.map((item) => ({ value: item, label: TAB_LABELS[item] }))}
      />
      <section
        id={tabPanelId(ID_PREFIX, currentTab)}
        role="tabpanel"
        aria-labelledby={`${ID_PREFIX}-tab-${currentTab}`}
        className="min-h-40"
      >
        {currentTab === "script" ? <ScriptTab job={data} /> : null}
        {currentTab === "scenes" ? <ScenesTab job={data} /> : null}
        {currentTab === "qc" ? <QcTab job={data} /> : null}
        {currentTab === "cost" ? <CostTab job={data} tasks={tasks.data} /> : null}
        {currentTab === "logs" ? (
          <TaskLogPanel
            tasks={tasks.data}
            taskId={logTaskId}
            onTaskChange={(taskId) => setLogTask({ jobId, taskId })}
          />
        ) : null}
        {currentTab === "files" ? <FilesTab job={data} /> : null}
        {currentTab === "history" ? <HistoryTab job={data} /> : null}
      </section>
    </div>
  );
}

function CloseButton({ onClose }: { onClose: () => void }) {
  return (
    <button
      type="button"
      onClick={onClose}
      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-fg-muted hover:bg-surface-2 hover:text-fg"
      aria-label="Закрыть панель ролика"
      title="Закрыть (Esc)"
    >
      <X className="h-4 w-4" aria-hidden="true" />
    </button>
  );
}
