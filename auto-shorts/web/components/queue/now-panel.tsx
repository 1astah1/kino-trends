"use client";

import { Hourglass, Square } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";

import { SectionError } from "@/components/common/section-error";
import { isPipelineState, PIPELINE_STATE_META } from "@/components/trends/labels";
import {
  Badge,
  Button,
  EmptyState,
  SkeletonTable,
  Table,
  TableBody,
  TableHead,
  TableRow,
  Td,
  Th,
} from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/format";
import { nextScheduledRun, useSchedules } from "@/lib/queries/schedules";
import { type Task, useTasks } from "@/lib/queries/tasks";
import { useTrendsPipeline } from "@/lib/queries/trends";
import { useCancelRun, useTrendsRuns } from "@/lib/queries/trends-ops";

import {
  clampPct,
  formatElapsed,
  formatUntil,
  parseProgress,
  PIPELINE_NOW_STATES,
  runTriggerLabel,
  taskKindLabel,
  trendsJobLabel,
} from "./labels";
import { ExpandCell, ProgressBar, SourceBadge, TaskActions, TaskLogTail, TaskWhatCell } from "./task-bits";

interface NowRow {
  key: string;
  source: "auto-shorts" | "kino-trends";
  what: ReactNode;
  pct: number | null;
  progressText: string;
  who: string;
  startedAt: string | null;
  task?: Task;
  actions?: ReactNode;
}

function Two({ title, hint }: { title: ReactNode; hint?: ReactNode }) {
  return (
    <span className="flex min-w-0 flex-col">
      <span className="truncate font-medium text-fg">{title}</span>
      {hint ? <span className="truncate text-2xs text-fg-muted">{hint}</span> : null}
    </span>
  );
}

/**
 * Вкладка «Сейчас»: единый живой список того, что выполняется прямо сейчас в обеих системах —
 * задачи воркеров auto-shorts (опрос 5 с + события SSE), текущий сбор Kino-trends с фазой и прогрессом,
 * его фоновые работы и задания конвейера в работе. Строка задачи раскрывается в хвост лога.
 */
export function NowPanel({
  operator,
  trendsConfigured,
  now,
}: {
  operator: boolean;
  trendsConfigured: boolean;
  now: number;
}) {
  const tasks = useTasks({ status: ["running"], limit: 100, offset: 0 }, { whenLive: 5_000 });
  const runs = useTrendsRuns({}, { enabled: trendsConfigured, whenLive: 5_000 });
  const pipeline = useTrendsPipeline({}, { enabled: trendsConfigured });
  const schedules = useSchedules();
  const cancelRun = useCancelRun();
  const [expanded, setExpanded] = useState<number | null>(null);

  async function onStopRun() {
    try {
      const result = await cancelRun.mutateAsync();
      toast.success(result.cancelling ? "Сбору отправлен сигнал остановки" : "Сбор уже не шёл");
    } catch (error) {
      toast.error("Не удалось остановить сбор", { description: errorMessage(error) });
    }
  }

  const rows: NowRow[] = [];

  for (const task of [...(tasks.data?.items ?? [])].sort(byStartedAt)) {
    const pct = clampPct(task.progress);
    rows.push({
      key: `task-${task.id}`,
      source: "auto-shorts",
      what: <TaskWhatCell task={task} />,
      pct,
      progressText: pct === null ? "идёт" : `${pct}%`,
      who: task.locked_by ?? "—",
      startedAt: task.started_at ?? task.updated_at,
      task,
    });
  }

  const current = runs.data?.current;
  if (current?.running) {
    const progress = parseProgress(current.progress);
    rows.push({
      key: "trends-run",
      source: "kino-trends",
      what: (
        <Two
          title={`Сбор трендов · ${current.phase || "запуск"}`}
          hint={`прогон #${current.run_id ?? "—"} · ${runTriggerLabel(current.trigger)}`}
        />
      ),
      pct: progress.pct,
      progressText: progress.text || "идёт",
      who: "Kino-trends",
      startedAt: current.started_at ?? null,
      actions: operator ? (
        <Button variant="ghost" size="xs" disabled={cancelRun.isPending} onClick={() => void onStopRun()}>
          <Square className="h-3 w-3" aria-hidden="true" />
          Остановить
        </Button>
      ) : undefined,
    });
  }

  for (const [name, job] of Object.entries(runs.data?.jobs ?? {})) {
    if (!job.running) continue;
    const progress = parseProgress(job.progress);
    rows.push({
      key: `trends-job-${name}`,
      source: "kino-trends",
      what: <Two title={trendsJobLabel(name)} hint="фоновая работа Kino-trends" />,
      pct: progress.pct,
      progressText: progress.text || "идёт",
      who: "Kino-trends",
      startedAt: null,
    });
  }

  for (const item of pipeline.data?.items ?? []) {
    if (!PIPELINE_NOW_STATES.has(item.status)) continue;
    const meta = isPipelineState(item.status) ? PIPELINE_STATE_META[item.status] : null;
    rows.push({
      key: `pipeline-${item.id}`,
      source: "kino-trends",
      what: (
        <Two
          title={`${meta?.label ?? item.status_ru ?? item.status}: ${item.label ?? item.ref_id}`}
          hint={[
            item.kind === "film" ? "кино" : "видео",
            item.channel,
            item.as_source_id ? `источник ${item.as_source_id}` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        />
      ),
      pct: null,
      progressText: "в конвейере",
      who: "Kino-trends → auto-shorts",
      startedAt: item.updated_at ?? null,
    });
  }

  if (tasks.isPending) return <SkeletonTable rows={4} />;

  const errors = (
    <>
      {tasks.isError ? (
        <SectionError
          error={tasks.error}
          onRetry={() => void tasks.refetch()}
          title="Задачи auto-shorts не загрузились"
        />
      ) : null}
      {runs.isError ? (
        <SectionError error={runs.error} onRetry={() => void runs.refetch()} title="Фон Kino-trends не загрузился" />
      ) : null}
    </>
  );

  if (rows.length === 0) {
    const parts: string[] = [];
    const next = runs.data?.next_run_at;
    if (next) parts.push(`сбор трендов ${formatUntil(next, now)} (${formatDateTime(next, { short: true })})`);
    const scheduled = nextScheduledRun(schedules.data);
    if (scheduled) {
      parts.push(
        `${taskKindLabel(scheduled.task_kind).toLowerCase()} ${formatUntil(scheduled.next_run_at, now)} (${formatDateTime(
          scheduled.next_run_at,
          { short: true },
        )})`,
      );
    }
    return (
      <div className="flex flex-col gap-3">
        {errors}
        <EmptyState
          icon={Hourglass}
          title="Сейчас ничего не выполняется"
          description={
            parts.length
              ? `Система ждёт следующего прогона: ${parts.join(" · ")}.`
              : "Ни одной идущей задачи ни в auto-shorts, ни в Kino-trends."
          }
        />
      </div>
    );
  }

  const span = 7;
  return (
    <div className="flex flex-col gap-3" data-testid="queue-now">
      {errors}
      <Table minWidth={920}>
        <caption className="sr-only">Что выполняется сейчас</caption>
        <TableHead>
          <tr>
            <Th className="w-8" aria-label="Раскрыть" />
            <Th>Система</Th>
            <Th className="min-w-72">Что</Th>
            <Th className="min-w-40">Прогресс</Th>
            <Th>Кто</Th>
            <Th>Идёт</Th>
            <Th className="text-right" aria-label="Действия" />
          </tr>
        </TableHead>
        <TableBody>
          {rows.map((row) => {
            const taskId = row.task?.id ?? null;
            const open = taskId !== null && expanded === taskId;
            return [
              <TableRow
                key={row.key}
                clickable={taskId !== null}
                onClick={taskId === null ? undefined : () => setExpanded(open ? null : taskId)}
                aria-expanded={taskId === null ? undefined : open}
                data-testid="queue-now-row"
              >
                <Td>{taskId === null ? null : <ExpandCell expanded={open} />}</Td>
                <Td>
                  <SourceBadge source={row.source} />
                </Td>
                <Td className="max-w-md">{row.what}</Td>
                <Td>
                  <span className="flex min-w-32 flex-col gap-1">
                    <ProgressBar pct={row.pct} tone={row.source === "auto-shorts" ? "bg-machine" : "bg-human"} />
                    <span className="tabular truncate font-mono text-2xs text-fg-muted" title={row.progressText}>
                      {row.progressText}
                    </span>
                  </span>
                </Td>
                <Td>
                  <span className="font-mono text-xs text-fg-muted">{row.who}</span>
                </Td>
                <Td>
                  <span
                    className="tabular font-mono text-xs"
                    title={row.startedAt ? formatDateTime(row.startedAt) : undefined}
                  >
                    {row.startedAt ? formatElapsed(row.startedAt, now) : "—"}
                  </span>
                </Td>
                <Td className="text-right">
                  {row.task ? <TaskActions task={row.task} operator={operator} /> : row.actions}
                </Td>
              </TableRow>,
              open && row.task ? (
                <tr key={`${row.key}-log`} className="bg-surface-2/40">
                  <td colSpan={span} className="px-3 pb-2">
                    <TaskLogTail task={row.task} />
                  </td>
                </tr>
              ) : null,
            ];
          })}
        </TableBody>
      </Table>
      <p className="text-2xs text-fg-muted">
        <Badge variant="accent" outline className="mr-1 font-mono">
          auto-shorts
        </Badge>
        задачи воркеров (тяжёлый слот один, лёгкие идут параллельно) ·{" "}
        <Badge variant="human" outline className="mr-1 font-mono">
          Kino-trends
        </Badge>
        сбор трендов, фоновые работы и задания конвейера в работе.
      </p>
    </div>
  );
}

function byStartedAt(a: Task, b: Task): number {
  return Date.parse(a.started_at ?? a.updated_at) - Date.parse(b.started_at ?? b.updated_at);
}
