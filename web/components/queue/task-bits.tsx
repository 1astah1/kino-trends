"use client";

import { Ban, ChevronDown, ChevronRight, ExternalLink, Radio, RotateCcw } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { SectionError } from "@/components/common/section-error";
import { TASK_STATUS_META } from "@/components/production/job-meta";
import {
  Badge,
  Button,
  EmptyState,
  SkeletonTable,
  StatusBadge,
  Table,
  TableBody,
  TableHead,
  TableRow,
  Td,
  Th,
} from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatAgo, formatDateTime } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import { isTaskActive, type Task, useCancelTask, useRetryTask, useTask, useTaskLogStream } from "@/lib/queries/tasks";
import { deferReasonLabel, RESOURCE_CLASS_LABELS } from "@/lib/status-meta";

import { clampPct, formatElapsed, formatUntil, taskKindLabel, taskTitle } from "./labels";

// ---------------------------------------------------------------- мелочи

export function ProgressBar({
  pct,
  tone = "bg-machine",
  className,
}: {
  pct: number | null;
  tone?: string;
  className?: string;
}) {
  const value = clampPct(pct);
  return (
    <div
      className={cn("relative h-1.5 w-full overflow-hidden rounded-full bg-surface-2", className)}
      role={value === null ? undefined : "progressbar"}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value ?? undefined}
    >
      {value === null ? (
        <span className={cn("absolute inset-y-0 left-0 w-1/3 animate-pulse rounded-full opacity-60", tone)} />
      ) : (
        <span className={cn("absolute inset-y-0 left-0 rounded-full", tone)} style={{ width: `${value}%` }} />
      )}
    </div>
  );
}

export function SourceBadge({ source }: { source: "auto-shorts" | "kino-trends" }) {
  return source === "auto-shorts" ? (
    <Badge variant="accent" outline className="font-mono">
      auto-shorts
    </Badge>
  ) : (
    <Badge variant="human" outline className="font-mono">
      Kino-trends
    </Badge>
  );
}

/** Ссылка на ролик в «Конвейере» (`/production?job=`): там таймлайн стадий, медиа и полный лог. */
export function JobLink({ jobId, children }: { jobId: number; children?: ReactNode }) {
  return (
    <Link
      href={routeHref("/production", { job: String(jobId) })}
      onClick={(event) => event.stopPropagation()}
      className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
      data-testid="queue-job-link"
    >
      {children ?? `ролик #${jobId}`}
      <ExternalLink className="h-3 w-3" aria-hidden="true" />
    </Link>
  );
}

/** Подзаголовок задачи: вид · класс · попытка · воркер. */
export function taskSubtitle(task: Task): string {
  return [
    task.kind === "job_stage" ? taskKindLabel(task.kind) : null,
    RESOURCE_CLASS_LABELS[task.resource_class].toLowerCase(),
    task.max_attempts > 1 || task.attempts > 1 ? `попытка ${task.attempts}/${task.max_attempts}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

// ---------------------------------------------------------------- действия

/** «Отменить» для queued/running, «Повторить» для failed/cancelled. Ошибки показывает общий MutationCache. */
export function TaskActions({ task, operator }: { task: Task; operator: boolean }) {
  const retry = useRetryTask();
  const cancel = useCancelTask();
  if (!operator) return null;
  const active = isTaskActive(task.status);
  return (
    <span className="inline-flex items-center justify-end gap-1.5" onClick={(event) => event.stopPropagation()}>
      {task.status === "failed" || task.status === "cancelled" ? (
        <Button
          variant="outline"
          size="xs"
          disabled={retry.isPending}
          onClick={() =>
            retry.mutate(task.id, { onSuccess: () => toast.success(`Задача #${task.id} снова в очереди`) })
          }
          data-testid="queue-task-retry"
        >
          <RotateCcw className="h-3 w-3" aria-hidden="true" />
          Повторить
        </Button>
      ) : null}
      {active ? (
        task.cancel_requested ? (
          <Badge variant="warning">отмена запрошена</Badge>
        ) : (
          <Button
            variant="ghost"
            size="xs"
            disabled={cancel.isPending}
            onClick={() =>
              cancel.mutate(task.id, { onSuccess: () => toast.success(`Отмена задачи #${task.id} запрошена`) })
            }
            data-testid="queue-task-cancel"
          >
            <Ban className="h-3 w-3" aria-hidden="true" />
            Отменить
          </Button>
        )
      ) : null}
    </span>
  );
}

// ---------------------------------------------------------------- хвост лога

/**
 * Раскрытая строка задачи: хвост лога из `GET /tasks/{id}` (200 строк), а пока задача идёт — живой поток
 * `/tasks/{id}/log/stream` (тот же механизм, что у панели «Логи» ролика в «Конвейере»).
 */
export function TaskLogTail({ task }: { task: Task }) {
  const detail = useTask(task.id);
  const current = detail.data?.task ?? task;
  const active = isTaskActive(current.status);
  const stream = useTaskLogStream(task.id, active);
  const streaming = active && stream.lines.length > 0;
  const tail = detail.data?.log_tail;
  const lines = useMemo(() => (streaming ? stream.lines : (tail ?? [])), [streaming, stream.lines, tail]);
  const boxRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [lines]);

  return (
    <div className="flex flex-col gap-2 py-1" data-testid="queue-task-log">
      <div className="flex flex-wrap items-center gap-2 text-2xs text-fg-muted">
        <span className="font-mono">
          {current.kind} · {current.resource_class} · попытка {current.attempts}/{current.max_attempts}
          {current.started_at ? ` · старт ${formatDateTime(current.started_at, { short: true })}` : ""}
          {current.finished_at ? ` · финиш ${formatDateTime(current.finished_at, { short: true })}` : ""}
          {current.locked_by ? ` · ${current.locked_by}` : ""}
          {current.error_class ? ` · ${current.error_class}` : ""}
        </span>
        {streaming ? (
          <Badge variant="machine">
            <Radio className="h-3 w-3" aria-hidden="true" />
            живой лог
          </Badge>
        ) : active && !stream.visible ? (
          <Badge variant="neutral">поток на паузе: вкладка скрыта</Badge>
        ) : null}
        {current.job_id ? (
          <span className="ml-auto">
            <JobLink jobId={current.job_id}>открыть ролик и полный лог</JobLink>
          </span>
        ) : null}
      </div>
      {detail.isError ? (
        <SectionError error={detail.error} onRetry={() => void detail.refetch()} />
      ) : (
        <pre
          ref={boxRef}
          className="max-h-72 min-h-24 overflow-auto rounded-md border border-border bg-video p-2 font-mono text-2xs leading-4 whitespace-pre-wrap break-all text-white/85"
          aria-label={`Лог задачи #${task.id}`}
          tabIndex={0}
        >
          {detail.isPending ? "Загрузка лога…" : lines.length ? lines.join("\n") : "Лог пуст."}
        </pre>
      )}
      {current.last_error && current.status === "failed" ? (
        <pre className="max-h-40 overflow-auto rounded-md border border-negative/40 bg-negative/10 p-2 font-mono text-2xs whitespace-pre-wrap text-fg">
          {current.last_error}
        </pre>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- таблица задач с раскрытием строк

export interface TaskColumn {
  key: string;
  header: ReactNode;
  className?: string;
  render: (task: Task) => ReactNode;
}

export function ExpandCell({ expanded }: { expanded: boolean }) {
  const Icon = expanded ? ChevronDown : ChevronRight;
  return <Icon className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />;
}

/** Таблица задач: строка раскрывается в хвост лога; справа — «Отменить»/«Повторить». */
export function TaskTable({
  tasks,
  columns,
  operator,
  isLoading,
  emptyTitle,
  emptyDescription,
  caption,
  minWidth = 880,
}: {
  tasks: Task[];
  columns: TaskColumn[];
  operator: boolean;
  isLoading?: boolean;
  emptyTitle: string;
  emptyDescription?: ReactNode;
  caption: string;
  minWidth?: number;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);
  if (isLoading) return <SkeletonTable rows={5} />;
  if (tasks.length === 0) return <EmptyState title={emptyTitle} description={emptyDescription} compact />;
  const span = columns.length + 2;
  return (
    <Table minWidth={minWidth}>
      <caption className="sr-only">{caption}</caption>
      <TableHead>
        <tr>
          <Th className="w-8" aria-label="Раскрыть" />
          {columns.map((col) => (
            <Th key={col.key} className={col.className}>
              {col.header}
            </Th>
          ))}
          <Th className="text-right" aria-label="Действия" />
        </tr>
      </TableHead>
      <TableBody>
        {tasks.map((task) => {
          const open = expanded === task.id;
          return [
            <TableRow
              key={task.id}
              clickable
              onClick={() => setExpanded(open ? null : task.id)}
              aria-expanded={open}
              data-testid="queue-task-row"
            >
              <Td>
                <ExpandCell expanded={open} />
              </Td>
              {columns.map((col) => (
                <Td key={col.key} className={col.className}>
                  {col.render(task)}
                </Td>
              ))}
              <Td className="text-right">
                <TaskActions task={task} operator={operator} />
              </Td>
            </TableRow>,
            open ? (
              <tr key={`${task.id}-log`} className="bg-surface-2/40">
                <td colSpan={span} className="px-3 pb-2">
                  <TaskLogTail task={task} />
                </td>
              </tr>
            ) : null,
          ];
        })}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------- готовые ячейки

export function TaskIdCell({ task }: { task: Task }) {
  return <span className="font-mono text-xs">#{task.id}</span>;
}

export function TaskWhatCell({ task }: { task: Task }) {
  return (
    <span className="flex min-w-0 flex-col">
      <span className="truncate font-medium text-fg">{taskTitle(task)}</span>
      <span className="truncate text-2xs text-fg-muted">
        {taskSubtitle(task)}
        {task.job_id ? (
          <>
            {" · "}
            <JobLink jobId={task.job_id}>в конвейере</JobLink>
          </>
        ) : null}
      </span>
    </span>
  );
}

export function TaskStatusCell({ task }: { task: Task }) {
  return (
    <span className="flex flex-col gap-1">
      <StatusBadge status={task.status} map={TASK_STATUS_META} />
      {task.defer_reason ? <Badge variant="warning">отложена: {deferReasonLabel(task.defer_reason)}</Badge> : null}
    </span>
  );
}

export function TaskProgressCell({ task, now }: { task: Task; now: number }) {
  const pct = clampPct(task.progress);
  return (
    <span className="flex min-w-32 flex-col gap-1">
      <ProgressBar pct={pct} />
      <span className="tabular font-mono text-2xs text-fg-muted">
        {pct === null ? "идёт" : `${pct}%`}
        {task.started_at ? ` · ${formatElapsed(task.started_at, now)}` : ""}
      </span>
    </span>
  );
}

export function TaskRunAfterCell({ task, now }: { task: Task; now: number }) {
  const at = Date.parse(task.run_after);
  const future = !Number.isNaN(at) && at > now;
  return (
    <span className="flex flex-col">
      <span className="tabular font-mono text-xs">
        {future ? formatUntil(task.run_after, now) : "готова к запуску"}
      </span>
      <span className="text-2xs text-fg-muted" title={formatDateTime(task.run_after)}>
        {future ? formatDateTime(task.run_after, { short: true }) : formatAgo(task.run_after, now)}
      </span>
    </span>
  );
}

export function TaskErrorCell({ task }: { task: Task }) {
  if (!task.error_class && !task.last_error) return <span className="text-fg-muted">—</span>;
  return (
    <span className="flex min-w-0 max-w-96 flex-col">
      {task.error_class ? <span className="font-mono text-xs text-negative">{task.error_class}</span> : null}
      {task.last_error ? (
        <span className="line-clamp-2 text-xs text-fg-muted" title={task.last_error}>
          {task.last_error.trim().split("\n").filter(Boolean).slice(-1)[0] ?? task.last_error}
        </span>
      ) : null}
    </span>
  );
}
