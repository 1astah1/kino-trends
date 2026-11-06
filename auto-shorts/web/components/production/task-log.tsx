"use client";

import { Ban, Radio, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { useCurrentUser } from "@/components/auth/auth-context";
import { SectionError } from "@/components/common/section-error";
import { Badge, Button, EmptyState, Select, Skeleton, StatusBadge } from "@/components/ui";
import { canOperate } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import type { JobTaskItem } from "@/lib/queries/jobs";
import { isTaskActive, useCancelTask, useRetryTask, useTask, useTaskLogStream } from "@/lib/queries/tasks";
import { deferReasonLabel } from "@/lib/status-meta";

import { jobStageLabel, TASK_STATUS_META } from "./job-meta";

/** Задача по умолчанию: идущая, иначе упавшая последней, иначе последняя. */
export function defaultTaskId(tasks: readonly JobTaskItem[] | undefined): number | null {
  if (!tasks || tasks.length === 0) return null;
  const running = tasks.find((task) => task.status === "running");
  if (running) return running.task_id;
  const failed = [...tasks].reverse().find((task) => task.status === "failed");
  if (failed) return failed.task_id;
  return tasks[tasks.length - 1].task_id;
}

/**
 * Лог задачи (§7.3 вкладка «Логи», §7.4): хвост из `GET /tasks/{id}` (200 строк), а пока задача идёт —
 * живой поток `/tasks/{id}/log/stream`, открытый только при видимой вкладке браузера.
 */
export function TaskLogPanel({
  tasks,
  taskId,
  onTaskChange,
}: {
  tasks: readonly JobTaskItem[] | undefined;
  taskId: number | null;
  onTaskChange: (taskId: number) => void;
}) {
  const user = useCurrentUser();
  const selected = taskId ?? defaultTaskId(tasks);
  const detail = useTask(selected);
  const task = detail.data?.task;
  const active = isTaskActive(task?.status);
  const stream = useTaskLogStream(selected, active);
  const retry = useRetryTask();
  const cancel = useCancelTask();
  const [follow, setFollow] = useState(true);
  const boxRef = useRef<HTMLPreElement>(null);

  const streaming = active && stream.lines.length > 0;
  const tail = detail.data?.log_tail;
  const lines = useMemo(() => (streaming ? stream.lines : (tail ?? [])), [streaming, stream.lines, tail]);

  useEffect(() => {
    if (!follow) return;
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [lines, follow]);

  if (!tasks) return <Skeleton className="h-48 w-full" />;
  if (tasks.length === 0 || selected === null) {
    return <EmptyState compact title="Задач ещё нет" description="Лог появится, когда ролик уйдёт в очередь." />;
  }

  return (
    <div className="flex flex-col gap-2" data-testid="task-log">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          aria-label="Задача"
          value={String(selected)}
          onChange={(event) => onTaskChange(Number(event.target.value))}
          className="h-7 w-auto min-w-56 text-xs"
        >
          {tasks.map((item) => (
            <option key={item.task_id} value={item.task_id}>
              #{item.task_id} · {jobStageLabel(item.stage)} · {TASK_STATUS_META[item.status].label.toLowerCase()}
              {item.attempts > 1 ? ` · попытка ${item.attempts}` : ""}
            </option>
          ))}
        </Select>
        {task ? <StatusBadge status={task.status} map={TASK_STATUS_META} /> : null}
        {task?.defer_reason ? <Badge variant="warning">отложена: {deferReasonLabel(task.defer_reason)}</Badge> : null}
        {streaming ? (
          <Badge variant="machine">
            <Radio className="h-3 w-3" aria-hidden="true" />
            живой лог
          </Badge>
        ) : active && !stream.visible ? (
          <Badge variant="neutral">поток на паузе: вкладка скрыта</Badge>
        ) : null}
        <label className="ml-auto inline-flex items-center gap-1.5 text-xs text-fg-muted">
          <input type="checkbox" checked={follow} onChange={(event) => setFollow(event.target.checked)} />
          держать внизу
        </label>
        {task && canOperate(user.role) ? (
          <>
            {task.status === "failed" || task.status === "cancelled" ? (
              <Button
                size="xs"
                variant="outline"
                disabled={retry.isPending}
                onClick={() =>
                  retry.mutate(task.id, { onSuccess: () => toast.success(`Задача #${task.id} снова в очереди`) })
                }
              >
                <RotateCcw className="h-3 w-3" aria-hidden="true" />
                Повторить задачу
              </Button>
            ) : null}
            {active ? (
              <Button
                size="xs"
                variant="destructive"
                disabled={cancel.isPending}
                onClick={() =>
                  cancel.mutate(task.id, { onSuccess: () => toast.success(`Отмена задачи #${task.id} запрошена`) })
                }
              >
                <Ban className="h-3 w-3" aria-hidden="true" />
                Остановить
              </Button>
            ) : null}
          </>
        ) : null}
      </div>
      {task ? (
        <p className="font-mono text-2xs text-fg-muted">
          {task.kind} · {task.resource_class} · попытка {task.attempts}/{task.max_attempts}
          {task.started_at ? ` · старт ${formatDateTime(task.started_at, { short: true })}` : ""}
          {task.finished_at ? ` · финиш ${formatDateTime(task.finished_at, { short: true })}` : ""}
          {task.error_class ? ` · ${task.error_class}` : ""}
        </p>
      ) : null}
      {detail.isError ? (
        <SectionError error={detail.error} onRetry={() => void detail.refetch()} />
      ) : (
        <pre
          ref={boxRef}
          className="max-h-96 min-h-40 overflow-auto rounded-md border border-border bg-video p-2 font-mono text-2xs leading-4 whitespace-pre-wrap break-all text-white/85"
          aria-label="Лог задачи"
          aria-live={streaming ? "off" : undefined}
          tabIndex={0}
        >
          {detail.isPending ? "Загрузка лога…" : lines.length ? lines.join("\n") : "Лог пуст."}
        </pre>
      )}
      {task?.last_error && task.status === "failed" ? (
        <pre className="max-h-40 overflow-auto rounded-md border border-negative/40 bg-negative/10 p-2 font-mono text-2xs whitespace-pre-wrap text-fg">
          {task.last_error}
        </pre>
      ) : null}
    </div>
  );
}
