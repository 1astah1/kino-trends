"use client";

import { Unplug } from "lucide-react";
import { useMemo, useState } from "react";

import { SectionError } from "@/components/common/section-error";
import { PipelinePanel } from "@/components/trends/pipeline-panel";
import { Badge, EmptyState, Pagination } from "@/components/ui";
import { formatNumber } from "@/lib/format";
import { type Task, useTasks } from "@/lib/queries/tasks";
import { deferReasonLabel, RESOURCE_CLASS_LABELS } from "@/lib/status-meta";

import { type TaskColumn, TaskIdCell, TaskRunAfterCell, TaskTable, TaskWhatCell } from "./task-bits";

const LIMIT = 50;

function byPriorityThenRunAfter(a: Task, b: Task): number {
  if (a.priority !== b.priority) return a.priority - b.priority;
  return Date.parse(a.run_after) - Date.parse(b.run_after);
}

const COLUMNS = (now: number): TaskColumn[] => [
  { key: "id", header: "#", render: (task) => <TaskIdCell task={task} /> },
  { key: "what", header: "Что", className: "min-w-64 max-w-md", render: (task) => <TaskWhatCell task={task} /> },
  {
    key: "class",
    header: "Очередь",
    render: (task) => (
      <Badge variant={task.resource_class === "heavy" ? "machine" : "neutral"}>
        {RESOURCE_CLASS_LABELS[task.resource_class]}
      </Badge>
    ),
  },
  {
    key: "priority",
    header: "Приоритет",
    className: "text-right",
    render: (task) => <span className="tabular font-mono text-xs">{task.priority}</span>,
  },
  { key: "run_after", header: "Запуск", render: (task) => <TaskRunAfterCell task={task} now={now} /> },
  {
    key: "defer",
    header: "Отсрочка",
    render: (task) =>
      task.defer_reason ? (
        <Badge variant="warning">{deferReasonLabel(task.defer_reason)}</Badge>
      ) : (
        <span className="text-fg-muted">—</span>
      ),
  },
  {
    key: "attempts",
    header: "Попытки",
    className: "text-right",
    render: (task) => (
      <span className="tabular font-mono text-xs text-fg-muted">
        {task.attempts}/{task.max_attempts}
      </span>
    ),
  },
];

/**
 * Вкладка «Очередь»: что ждёт своего часа. Сверху — задачи auto-shorts в статусе queued (отложенные —
 * с причиной и временем следующей попытки), по приоритету и времени запуска; ниже — очередь конвейера
 * Kino-trends → auto-shorts (тот же компонент, что на экране трендов).
 */
export function QueuePanel({
  operator,
  trendsConfigured,
  now,
}: {
  operator: boolean;
  trendsConfigured: boolean;
  now: number;
}) {
  const [offset, setOffset] = useState(0);
  const tasks = useTasks({ status: ["queued"], limit: LIMIT, offset }, { whenLive: 10_000 });
  const items = tasks.data?.items;
  const sorted = useMemo(() => [...(items ?? [])].sort(byPriorityThenRunAfter), [items]);
  const deferred = sorted.filter((task) => task.defer_reason).length;
  const total = tasks.data?.total ?? 0;

  return (
    <div className="flex flex-col gap-6" data-testid="queue-queue">
      <section className="flex flex-col gap-2" aria-labelledby="queue-tasks-title">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="queue-tasks-title" className="text-sm font-semibold">
            Задачи auto-shorts
          </h2>
          {tasks.data ? (
            <>
              <Badge variant="neutral">ждут {formatNumber(total - deferred)}</Badge>
              {deferred ? <Badge variant="warning">отложено {formatNumber(deferred)}</Badge> : null}
            </>
          ) : null}
          <span className="text-xs text-fg-muted">
            Воркер берёт первую по приоритету задачу, у которой наступило время запуска; отложенные ждут ресурсов.
          </span>
        </div>
        {tasks.isError ? (
          <SectionError error={tasks.error} onRetry={() => void tasks.refetch()} title="Очередь не загрузилась" />
        ) : (
          <TaskTable
            tasks={sorted}
            columns={COLUMNS(now)}
            operator={operator}
            isLoading={tasks.isPending}
            emptyTitle="Очередь пуста"
            emptyDescription="Все задачи выполнены; новые появятся, когда ролики уйдут в производство или сработает расписание."
            caption="Очередь задач auto-shorts"
          />
        )}
        {total > LIMIT ? <Pagination total={total} limit={LIMIT} offset={offset} onOffsetChange={setOffset} /> : null}
      </section>

      <section className="flex flex-col gap-2" aria-labelledby="queue-pipeline-title">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="queue-pipeline-title" className="text-sm font-semibold">
            Конвейер Kino-trends → auto-shorts
          </h2>
          <span className="text-xs text-fg-muted">
            Исходники из трендов: отправка в auto-shorts, нарезка, приёмка, залив. Ревью — в «Приёмке».
          </span>
        </div>
        {trendsConfigured ? (
          <PipelinePanel operator={operator} enabled />
        ) : (
          <EmptyState
            icon={Unplug}
            compact
            title="Мост к Kino-trends не настроен"
            description="Очередь конвейера появится после настройки адреса Kino-trends в «Трендоскауте»."
          />
        )}
      </section>
    </div>
  );
}
