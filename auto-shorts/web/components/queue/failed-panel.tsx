"use client";

import { Ban, RotateCcw, ShieldCheck, Unplug } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { SectionError } from "@/components/common/section-error";
import { canCancel, canRetry, PIPELINE_STATE_META, type PipelineState } from "@/components/trends/labels";
import { Badge, Button, DataTable, type DataTableColumn, EmptyState, PillToggle, StatusBadge } from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { formatAgo, formatDateTime, formatNumber } from "@/lib/format";
import { type Task, type TasksFilters, type TaskStatus, useTasks } from "@/lib/queries/tasks";
import {
  type TrendsPipelineItem,
  useCancelTrendItem,
  useRetryTrendItem,
  useTrendsPipeline,
} from "@/lib/queries/trends";

import { PIPELINE_FAILED_STATES } from "./labels";
import { type TaskColumn, TaskErrorCell, TaskIdCell, TaskStatusCell, TaskTable, TaskWhatCell } from "./task-bits";

/** Один и тот же ключ для вкладки и бейджа в шапке: последние 100 упавших и отменённых задач. */
export const FAILED_FILTERS: TasksFilters = {
  status: ["failed", "cancelled"] as TaskStatus[],
  limit: 100,
  offset: 0,
};

export const RECENT_HOURS = 24;

export function finishedAt(task: Task): number {
  return Date.parse(task.finished_at ?? task.updated_at);
}

export function isRecent(task: Task, now: number, hours = RECENT_HOURS): boolean {
  const at = finishedAt(task);
  return !Number.isNaN(at) && now - at <= hours * 3_600_000;
}

type Range = "recent" | "all";

const COLUMNS = (now: number): TaskColumn[] => [
  { key: "id", header: "#", render: (task) => <TaskIdCell task={task} /> },
  { key: "what", header: "Что", className: "min-w-64 max-w-md", render: (task) => <TaskWhatCell task={task} /> },
  { key: "status", header: "Статус", render: (task) => <TaskStatusCell task={task} /> },
  { key: "error", header: "Ошибка", className: "min-w-64", render: (task) => <TaskErrorCell task={task} /> },
  {
    key: "finished",
    header: "Завершена",
    render: (task) => (
      <span
        className="tabular font-mono text-xs text-fg-muted"
        title={formatDateTime(task.finished_at ?? task.updated_at)}
      >
        {formatAgo(task.finished_at ?? task.updated_at, now)}
      </span>
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
 * Вкладка «Сбои»: упавшие и отменённые задачи auto-shorts за сутки (класс ошибки, последняя строка лога,
 * «Повторить», раскрытие в хвост лога) и сбойные задания конвейера Kino-trends с «Повторить»/«Отменить».
 */
export function FailedPanel({
  operator,
  trendsConfigured,
  now,
}: {
  operator: boolean;
  trendsConfigured: boolean;
  now: number;
}) {
  const [range, setRange] = useState<Range>("recent");
  const tasks = useTasks(FAILED_FILTERS, { whenLive: 15_000 });
  const items = tasks.data?.items;
  const visible = useMemo(() => {
    const list = items ?? [];
    const filtered = range === "recent" ? list.filter((task) => isRecent(task, now)) : list;
    return [...filtered].sort((a, b) => finishedAt(b) - finishedAt(a));
  }, [items, range, now]);
  const hiddenOlder = range === "recent" && items ? items.length - visible.length : 0;

  return (
    <div className="flex flex-col gap-6" data-testid="queue-failed">
      <section className="flex flex-col gap-2" aria-labelledby="failed-tasks-title">
        <div className="flex flex-wrap items-center gap-3">
          <h2 id="failed-tasks-title" className="text-sm font-semibold">
            Задачи auto-shorts
          </h2>
          <PillToggle
            label="Период"
            options={[
              { value: "recent", label: `За ${RECENT_HOURS} ч` },
              { value: "all", label: "Последние 100" },
            ]}
            value={range}
            onChange={(value) => setRange(value as Range)}
          />
          {hiddenOlder > 0 ? (
            <span className="text-xs text-fg-muted">ещё {formatNumber(hiddenOlder)} старше суток</span>
          ) : null}
        </div>
        {tasks.isError ? (
          <SectionError error={tasks.error} onRetry={() => void tasks.refetch()} title="Сбои не загрузились" />
        ) : (
          <TaskTable
            tasks={visible}
            columns={COLUMNS(now)}
            operator={operator}
            isLoading={tasks.isPending}
            emptyTitle={range === "recent" ? "За сутки сбоев не было" : "Сбоев нет"}
            emptyDescription="Упавшие и отменённые задачи появятся здесь с классом ошибки и хвостом лога."
            caption="Сбои задач auto-shorts"
          />
        )}
      </section>

      <section className="flex flex-col gap-2" aria-labelledby="failed-pipeline-title">
        <h2 id="failed-pipeline-title" className="text-sm font-semibold">
          Конвейер Kino-trends
        </h2>
        {trendsConfigured ? (
          <FailedPipeline operator={operator} now={now} />
        ) : (
          <EmptyState
            icon={Unplug}
            compact
            title="Мост к Kino-trends не настроен"
            description="Сбои конвейера появятся после настройки адреса Kino-trends в «Трендоскауте»."
          />
        )}
      </section>
    </div>
  );
}

function FailedPipeline({ operator, now }: { operator: boolean; now: number }) {
  const pipeline = useTrendsPipeline({});
  const retry = useRetryTrendItem();
  const cancel = useCancelTrendItem();
  const failed = (pipeline.data?.items ?? []).filter((item) => PIPELINE_FAILED_STATES.has(item.status));
  const statesRu = pipeline.data?.states_ru ?? {};
  const busy = retry.isPending || cancel.isPending;

  async function onRetry(item: TrendsPipelineItem) {
    try {
      const updated = await retry.mutateAsync(item.id);
      toast.success("Задание снова в очереди", { description: updated.status_ru ?? updated.status });
    } catch (error) {
      toast.error("Не удалось повторить", { description: errorMessage(error) });
    }
  }

  async function onCancel(item: TrendsPipelineItem) {
    try {
      await cancel.mutateAsync(item.id);
      toast.success("Задание отменено", { description: item.label ?? item.ref_id });
    } catch (error) {
      toast.error("Не удалось отменить", { description: errorMessage(error) });
    }
  }

  if (pipeline.isError) {
    return (
      <SectionError error={pipeline.error} onRetry={() => void pipeline.refetch()} title="Конвейер не загрузился" />
    );
  }

  const columns: Array<DataTableColumn<TrendsPipelineItem>> = [
    {
      key: "kind",
      header: "Тип",
      render: (item) => <Badge variant="neutral">{item.kind === "film" ? "кино" : "видео"}</Badge>,
    },
    {
      key: "label",
      header: "Что",
      className: "min-w-64 max-w-96",
      render: (item) => (
        <span className="flex min-w-0 flex-col">
          <span className="truncate font-medium text-fg" title={item.label ?? item.ref_id}>
            {item.label ?? item.ref_id}
          </span>
          <span className="truncate text-2xs text-fg-muted">
            {[item.channel, item.as_source_id ? `источник ${item.as_source_id}` : null, item.note]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </span>
      ),
    },
    {
      key: "status",
      header: "Статус",
      render: (item) => (
        <StatusBadge
          status={item.status as PipelineState}
          map={PIPELINE_STATE_META}
          fallbackLabel={item.status_ru ?? statesRu[item.status] ?? item.status}
        />
      ),
    },
    {
      key: "error",
      header: "Ошибка",
      className: "min-w-64",
      render: (item) =>
        item.last_error ? (
          <span className="line-clamp-2 max-w-96 text-xs text-negative" title={item.last_error}>
            {item.last_error}
          </span>
        ) : (
          <span className="text-fg-muted">—</span>
        ),
    },
    {
      key: "updated",
      header: "Обновлено",
      render: (item) => (
        <span className="tabular font-mono text-xs text-fg-muted" title={item.updated_at ?? undefined}>
          {formatAgo(item.updated_at, now)}
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      className: "text-right",
      render: (item) => (
        <span className="inline-flex items-center justify-end gap-1.5">
          {operator && canRetry(item.status) ? (
            <Button
              variant="outline"
              size="xs"
              disabled={busy}
              onClick={() => void onRetry(item)}
              data-testid="queue-trends-retry"
            >
              <RotateCcw className="h-3 w-3" aria-hidden="true" />
              Повторить
            </Button>
          ) : null}
          {operator && canCancel(item.status) ? (
            <Button
              variant="ghost"
              size="xs"
              disabled={busy}
              onClick={() => void onCancel(item)}
              data-testid="queue-trends-cancel"
            >
              <Ban className="h-3 w-3" aria-hidden="true" />
              Отменить
            </Button>
          ) : null}
        </span>
      ),
    },
  ];

  if (pipeline.data && failed.length === 0) {
    return (
      <EmptyState
        icon={ShieldCheck}
        compact
        title="В конвейере сбоев нет"
        description="Задания с ошибкой или не прошедшие QC появятся здесь с кнопками «Повторить» и «Отменить»."
      />
    );
  }

  return (
    <DataTable
      columns={columns}
      rows={failed}
      rowKey={(item) => String(item.id)}
      isLoading={pipeline.isPending}
      emptyTitle="В конвейере сбоев нет"
      minWidth={900}
      caption="Сбойные задания конвейера Kino-trends"
    />
  );
}
