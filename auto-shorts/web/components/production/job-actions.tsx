"use client";

import { ClipboardCheck, Copy, Play, RotateCcw, Send, Square } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { useCurrentUser } from "@/components/auth/auth-context";
import { STAGES_BY_KIND } from "@/components/review/review-logic";
import { Button, Dialog, FormField, Input, Select } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import { canOperate } from "@/lib/auth";
import { routeHref } from "@/lib/nav";
import {
  type Job,
  useAdaptJob,
  useCancelJob,
  useChannelOptions,
  useCloneJob,
  usePatchJob,
  useRetryJob,
  useStartJob,
} from "@/lib/queries/jobs";

import { JOB_STAGE_LABELS, JOB_STATUS_META, jobStageLabel, PLATFORM_LABELS } from "./job-meta";

type JobStage = Schemas["JobStage"];

const TERMINAL = new Set(["published", "rejected", "cancelled"]);
const EDITABLE = new Set(["draft", "queued", "rework"]);
const STARTABLE = new Set(["draft", "rework"]);
const ADAPTABLE = new Set(["approved", "published"]);

/**
 * Действия над роликом (§7.3 «повтор, „с…“, отмена, клон, приоритет»; §6.2 переходы). Сервер —
 * источник истины: недоступные в статусе действия просто не показываются, 409 разбирает общий клиент.
 */
export function JobActions({ job, onOpenJob }: { job: Job; onOpenJob: (id: number) => void }) {
  const user = useCurrentUser();
  const start = useStartJob();
  const cancel = useCancelJob();
  const [dialog, setDialog] = useState<"cancel" | "retry" | "clone" | "adapt" | null>(null);

  if (!canOperate(user.role)) {
    return job.status === "pending_review" || job.status === "qc_failed" ? (
      <Link
        href={routeHref("/review", { job: String(job.id) })}
        className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border px-2.5 text-xs hover:bg-surface-2"
      >
        <ClipboardCheck className="h-3.5 w-3.5" aria-hidden="true" />
        Открыть в приёмке
      </Link>
    ) : null;
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="job-actions">
      {job.status === "pending_review" || job.status === "qc_failed" ? (
        <Link
          href={routeHref("/review", { job: String(job.id) })}
          className="inline-flex h-7 items-center gap-1.5 rounded-md bg-human px-2.5 text-xs font-medium text-human-fg hover:bg-human/90"
        >
          <ClipboardCheck className="h-3.5 w-3.5" aria-hidden="true" />В приёмку
        </Link>
      ) : null}
      {STARTABLE.has(job.status) ? (
        <Button
          size="sm"
          disabled={start.isPending}
          onClick={() => start.mutate(job.id, { onSuccess: () => toast.success(`${job.video_uid}: запущен`) })}
        >
          <Play className="h-3.5 w-3.5" aria-hidden="true" />
          Запустить
        </Button>
      ) : null}
      {job.status === "failed" ? (
        <Button size="sm" onClick={() => setDialog("retry")}>
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          Повторить с…
        </Button>
      ) : null}
      <Button size="sm" variant="outline" onClick={() => setDialog("clone")}>
        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
        Клон
      </Button>
      {ADAPTABLE.has(job.status) ? (
        <Button size="sm" variant="outline" onClick={() => setDialog("adapt")}>
          <Send className="h-3.5 w-3.5" aria-hidden="true" />
          Под другую площадку
        </Button>
      ) : null}
      {EDITABLE.has(job.status) ? <PriorityEditor job={job} /> : null}
      {!TERMINAL.has(job.status) ? (
        <Button size="sm" variant="destructive" onClick={() => setDialog("cancel")}>
          <Square className="h-3.5 w-3.5" aria-hidden="true" />
          Отменить
        </Button>
      ) : null}

      <Dialog
        open={dialog === "cancel"}
        onClose={() => setDialog(null)}
        title={`Отменить ролик ${job.video_uid}?`}
        description="Идущие задачи остановятся, готовый пакет будет помечен устаревшим. Вернуть отменённый ролик нельзя — только клонировать."
        footer={
          <>
            <Button variant="ghost" onClick={() => setDialog(null)}>
              Не отменять
            </Button>
            <Button
              variant="destructive"
              disabled={cancel.isPending}
              onClick={() =>
                cancel.mutate(job.id, {
                  onSuccess: () => {
                    toast.success(`${job.video_uid}: отменён`);
                    setDialog(null);
                  },
                  onError: () => setDialog(null),
                })
              }
            >
              Отменить ролик
            </Button>
          </>
        }
      >
        <p className="text-sm text-fg-muted">
          Сейчас: {JOB_STATUS_META[job.status].label.toLowerCase()}, стадия «{jobStageLabel(job.stage)}».
        </p>
      </Dialog>

      {dialog === "retry" ? <RetryDialog job={job} onClose={() => setDialog(null)} /> : null}
      {dialog === "clone" ? <CloneDialog job={job} onClose={() => setDialog(null)} onOpenJob={onOpenJob} /> : null}
      {dialog === "adapt" ? <AdaptDialog job={job} onClose={() => setDialog(null)} onOpenJob={onOpenJob} /> : null}
    </div>
  );
}

function PriorityEditor({ job }: { job: Job }) {
  const patch = usePatchJob();
  const [value, setValue] = useState(String(job.priority));
  const [trackedPriority, setTrackedPriority] = useState(job.priority);
  if (trackedPriority !== job.priority) {
    setTrackedPriority(job.priority);
    setValue(String(job.priority));
  }
  const parsed = Number(value);
  const valid = value.trim() !== "" && Number.isInteger(parsed) && parsed >= 0 && parsed <= 1000;
  const dirty = valid && parsed !== job.priority;
  return (
    <form
      className="inline-flex items-center gap-1"
      onSubmit={(event) => {
        event.preventDefault();
        if (!dirty) return;
        patch.mutate(
          { jobId: job.id, body: { priority: parsed } },
          {
            onSuccess: () => toast.success(`Приоритет ${parsed}: меньше — раньше`),
            onError: (error) => toast.error(errorMessage(error)),
          },
        );
      }}
    >
      <label htmlFor={`priority-${job.id}`} className="text-xs text-fg-muted">
        Приоритет
      </label>
      <Input
        id={`priority-${job.id}`}
        type="number"
        min={0}
        max={1000}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        aria-invalid={!valid}
        className="h-7 w-20 font-mono text-xs"
        title="0–1000, меньше — раньше; 100 — обычный"
      />
      {dirty ? (
        <Button size="sm" type="submit" variant="outline" disabled={patch.isPending}>
          Сохранить
        </Button>
      ) : null}
    </form>
  );
}

function RetryDialog({ job, onClose }: { job: Job; onClose: () => void }) {
  const retry = useRetryJob();
  const stages = STAGES_BY_KIND[job.kind];
  const [stage, setStage] = useState<JobStage>(job.stage && stages.includes(job.stage) ? job.stage : stages[0]);
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Повторить ${job.video_uid}`}
      description="Артефакты выбранной стадии и следующих удаляются, ролик снова уходит в работу. Платные стадии пройдут через бюджет."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button
            disabled={retry.isPending}
            onClick={() =>
              retry.mutate(
                { jobId: job.id, fromStage: stage },
                {
                  onSuccess: () => {
                    toast.success(`${job.video_uid}: перезапуск со стадии «${JOB_STAGE_LABELS[stage]}»`);
                    onClose();
                  },
                },
              )
            }
          >
            Повторить
          </Button>
        </>
      }
    >
      <FormField label="Со стадии" htmlFor="retry-stage" hint={job.error ? `Ошибка: ${job.error}` : undefined}>
        <Select id="retry-stage" value={stage} onChange={(event) => setStage(event.target.value as JobStage)}>
          {stages.map((item) => (
            <option key={item} value={item}>
              {JOB_STAGE_LABELS[item]}
              {item === job.stage ? " — где упал" : ""}
            </option>
          ))}
        </Select>
      </FormField>
    </Dialog>
  );
}

function CloneDialog({ job, onClose, onOpenJob }: { job: Job; onClose: () => void; onOpenJob: (id: number) => void }) {
  const clone = useCloneJob();
  const channels = useChannelOptions();
  const [channelId, setChannelId] = useState(job.channel.id);
  const [topic, setTopic] = useState(job.topic ?? "");
  const [start, setStart] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Клон ${job.video_uid}`}
      description="Новый ролик с той же нишей и настройками; можно поменять канал и тему. Связь с исходным сохраняется (parent_job_id)."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button
            disabled={clone.isPending}
            onClick={() => {
              setError(null);
              clone.mutate(
                {
                  jobId: job.id,
                  overrides: {
                    channel_id: channelId !== job.channel.id ? channelId : null,
                    topic: topic.trim() !== (job.topic ?? "") ? topic.trim() || null : null,
                    start,
                  },
                },
                {
                  onSuccess: (created) => {
                    toast.success(`Клон создан: ${created.video_uid}`);
                    onClose();
                    onOpenJob(created.id);
                  },
                  onError: (err) => setError(errorMessage(err)),
                },
              );
            }}
          >
            Создать клон
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <FormField label="Канал" htmlFor="clone-channel">
          <Select id="clone-channel" value={channelId} onChange={(event) => setChannelId(event.target.value)}>
            {!channels.data?.some((item) => item.id === job.channel.id) ? (
              <option value={job.channel.id}>{job.channel.title ?? job.channel.id}</option>
            ) : null}
            {(channels.data ?? []).map((channel) => (
              <option key={channel.id} value={channel.id}>
                {channel.title ?? channel.id} · {PLATFORM_LABELS[channel.platform]}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Тема" htmlFor="clone-topic" hint="Пусто — тема из ниши">
          <Input id="clone-topic" value={topic} maxLength={300} onChange={(event) => setTopic(event.target.value)} />
        </FormField>
        <label className="inline-flex items-center gap-2 text-sm">
          <input type="checkbox" checked={start} onChange={(event) => setStart(event.target.checked)} />
          Запустить сразу (иначе — черновик)
        </label>
        {error ? (
          <p role="alert" className="text-sm text-negative">
            {error}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}

function AdaptDialog({ job, onClose, onOpenJob }: { job: Job; onClose: () => void; onOpenJob: (id: number) => void }) {
  const adapt = useAdaptJob();
  const channels = useChannelOptions();
  const options = (channels.data ?? []).filter((channel) => channel.platform !== job.channel.platform);
  const [channelId, setChannelId] = useState<string>("");
  const selected = channelId || options[0]?.id || "";
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Адаптация ${job.video_uid}`}
      description="Дочерний ролик под другую площадку: сценарий, голос и визуал берутся готовые, заново только рендер и QC. VK — не длиннее 60 секунд."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button
            disabled={adapt.isPending || !selected}
            onClick={() => {
              setError(null);
              adapt.mutate(
                { jobId: job.id, channelId: selected },
                {
                  onSuccess: (created) => {
                    toast.success(`Адаптация создана: ${created.video_uid}`);
                    onClose();
                    onOpenJob(created.id);
                  },
                  onError: (err) => setError(errorMessage(err)),
                },
              );
            }}
          >
            Перерендерить
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <FormField
          label="Канал другой площадки"
          htmlFor="adapt-channel"
          hint={options.length === 0 && !channels.isPending ? "Нет каналов другой площадки" : undefined}
        >
          <Select id="adapt-channel" value={selected} onChange={(event) => setChannelId(event.target.value)}>
            {options.map((channel) => (
              <option key={channel.id} value={channel.id}>
                {channel.title ?? channel.id} · {PLATFORM_LABELS[channel.platform]}
              </option>
            ))}
          </Select>
        </FormField>
        {error ? (
          <p role="alert" className="text-sm text-negative">
            {error}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
