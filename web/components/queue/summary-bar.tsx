"use client";

import { Cpu, Radar, Server, Workflow } from "lucide-react";
import Link from "next/link";

import { Badge, Callout, SectionCard, Skeleton } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatAgo, formatDateTime, formatNumber } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import type { SystemStatus } from "@/lib/queries/system";
import type { TrendsStatus } from "@/lib/queries/trends";
import type { TrendsRuns } from "@/lib/queries/trends-ops";
import { deferReasonLabel, RESOURCE_CLASS_LABELS, stageLabel } from "@/lib/status-meta";
import { deferReasonOf, isWorkerAlive, type Worker } from "@/lib/worker-state";

import {
  formatElapsed,
  formatSeconds,
  formatUntil,
  parseProgress,
  RUN_STATUS_META,
  type RunStatus,
  runTriggerLabel,
  trendsJobLabel,
} from "./labels";
import { ProgressBar } from "./task-bits";

function Counter({ label, value, tone }: { label: string; value: number | null; tone?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-fg-muted">{label}</span>
      <span className={cn("tabular font-mono text-lg font-semibold", value ? tone : undefined)}>
        {value === null ? "…" : formatNumber(value)}
      </span>
    </div>
  );
}

function WorkerChip({ worker, now }: { worker: Worker; now: number }) {
  const alive = isWorkerAlive(worker, now);
  const reason = alive ? deferReasonOf(worker.state) : null;
  const variant = !alive ? "negative" : reason ? "warning" : worker.state === "busy" ? "machine" : "positive";
  const text = !alive
    ? "нет связи"
    : reason
      ? `отложено: ${deferReasonLabel(reason).toLowerCase()}`
      : worker.state === "busy"
        ? `работает${worker.current_task_id ? ` #${worker.current_task_id}` : ""}`
        : worker.state === "stopping"
          ? "останавливается"
          : "свободен";
  return (
    <Badge variant={variant} title={`${worker.id} · heartbeat ${formatAgo(worker.heartbeat_at, now)}`}>
      <span className="font-mono">{RESOURCE_CLASS_LABELS[worker.resource_class].toLowerCase()}</span> · {text}
    </Badge>
  );
}

function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
      <span className="w-28 shrink-0 text-xs text-fg-muted">{label}</span>
      <span className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">{children}</span>
    </div>
  );
}

/**
 * Сводка над вкладками: слева — воркеры auto-shorts (выполняется / в очереди / отложено / сбоев за сутки,
 * тяжёлый слот, живые и мёртвые воркеры по heartbeat), справа — фон Kino-trends (идёт ли сбор, последний
 * прогон, фоновые работы, конвейер). Данные уже в кеше экрана, карточка их только раскладывает.
 */
export function SummaryBar({
  status,
  failed24h,
  runs,
  trends,
  trendsConfigured,
  trendsPending,
  now,
}: {
  status: SystemStatus | undefined;
  failed24h: number | null;
  runs: TrendsRuns | undefined;
  trends: TrendsStatus | undefined;
  trendsConfigured: boolean;
  trendsPending: boolean;
  now: number;
}) {
  const queue = status?.queue;
  const running = queue ? queue.heavy.running + queue.light.running : null;
  const deferred = queue ? queue.heavy.deferred + queue.light.deferred : null;
  const queued = queue ? queue.heavy.queued + queue.light.queued : null; // без отложенных: их считает deferred
  const workers = status?.workers ?? [];
  const alive = workers.filter((worker) => isWorkerAlive(worker, now)).length;
  const slot = status?.heavy_slot;

  const current = runs?.current;
  const lastRun = runs?.items.find((run) => run.status !== "running") ?? null;
  const runningJobs = Object.entries(runs?.jobs ?? {}).filter(([, job]) => job.running);
  const pipeline = trends?.pipeline ?? null;
  const progress = parseProgress(current?.progress);

  return (
    <div className="grid gap-4 lg:grid-cols-2" data-testid="queue-summary">
      <SectionCard title="auto-shorts" actions={<Server className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}>
        <div className="grid grid-cols-4 gap-3">
          <Counter label="выполняется" value={running} tone="text-machine" />
          <Counter label="в очереди" value={queued} />
          <Counter label="отложено" value={deferred} tone="text-warning" />
          <Counter label="сбоев за 24 ч" value={failed24h} tone="text-negative" />
        </div>
        <Line label="Тяжёлый слот">
          {!status ? (
            <Skeleton className="h-4 w-40" />
          ) : slot?.task_id ? (
            <span className="flex min-w-0 flex-1 flex-col gap-1">
              <span className="text-sm">
                <span className="font-mono">#{slot.task_id}</span> · {stageLabel(slot.stage)}
                <span className="tabular ml-2 font-mono text-xs text-fg-muted">
                  {slot.pct === null || slot.pct === undefined ? "" : `${Math.round(slot.pct)}% · `}
                  {slot.owner ?? "—"}
                </span>
              </span>
              <ProgressBar pct={slot.pct ?? null} className="max-w-xs" />
            </span>
          ) : (
            <span className="text-sm text-fg-muted">свободен</span>
          )}
          <Cpu className="ml-auto h-3.5 w-3.5 shrink-0 text-fg-muted" aria-hidden="true" />
        </Line>
        <Line label={`Воркеры ${workers.length ? `${alive}/${workers.length}` : ""}`}>
          {!status ? (
            <Skeleton className="h-4 w-56" />
          ) : workers.length === 0 ? (
            <Badge variant="negative">ни один воркер не зарегистрирован</Badge>
          ) : (
            workers.map((worker) => <WorkerChip key={worker.id} worker={worker} now={now} />)
          )}
        </Line>
      </SectionCard>

      <SectionCard title="Kino-trends" actions={<Radar className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}>
        {trendsPending ? (
          <div className="flex flex-col gap-2" aria-busy="true">
            <Skeleton className="h-4 w-64" />
            <Skeleton className="h-4 w-48" />
          </div>
        ) : !trendsConfigured ? (
          <Callout variant="info" title="Мост к Kino-trends не настроен">
            Сбор трендов и конвейер появятся здесь после того, как владелец укажет адрес Kino-trends в{" "}
            <Link href={routeHref("/trends")} className="text-accent hover:underline">
              «Трендоскауте»
            </Link>
            .
          </Callout>
        ) : (
          <>
            <Line label="Сбор трендов">
              {current?.running ? (
                <span className="flex min-w-0 flex-1 flex-col gap-1">
                  <span className="flex flex-wrap items-center gap-1.5 text-sm">
                    <Badge variant="machine">идёт сбор</Badge>
                    <span className="truncate">{current.phase || "запуск"}</span>
                    {progress.text ? (
                      <span className="tabular truncate font-mono text-xs text-fg-muted">{progress.text}</span>
                    ) : null}
                    <span className="tabular font-mono text-xs text-fg-muted">
                      {formatElapsed(current.started_at, now)} · {runTriggerLabel(current.trigger)}
                    </span>
                  </span>
                  <ProgressBar pct={progress.pct} className="max-w-xs" />
                </span>
              ) : (
                <>
                  <Badge variant="neutral">простаивает</Badge>
                  {runs?.next_run_at ? (
                    <span className="text-xs text-fg-muted" title={formatDateTime(runs.next_run_at)}>
                      следующий автосбор {formatUntil(runs.next_run_at, now)} (
                      {formatDateTime(runs.next_run_at, { short: true })})
                    </span>
                  ) : runs && runs.schedule_hours === 0 ? (
                    <span className="text-xs text-fg-muted">автосбор выключен — только по кнопке</span>
                  ) : null}
                </>
              )}
            </Line>
            <Line label="Последний прогон">
              {lastRun ? (
                <>
                  <span className="font-mono text-xs">#{lastRun.id}</span>
                  <Badge
                    variant={RUN_STATUS_META[lastRun.status as RunStatus]?.variant ?? "neutral"}
                    title={lastRun.error ?? undefined}
                  >
                    {RUN_STATUS_META[lastRun.status as RunStatus]?.label ?? lastRun.status}
                  </Badge>
                  <span className="text-xs text-fg-muted" title={formatDateTime(lastRun.finished_at)}>
                    {formatAgo(lastRun.finished_at ?? lastRun.started_at, now)} ·{" "}
                    {typeof lastRun.stats?.duration_s === "number"
                      ? formatSeconds(lastRun.stats.duration_s)
                      : formatElapsed(lastRun.started_at, now, lastRun.finished_at)}{" "}
                    · {runTriggerLabel(lastRun.trigger)}
                  </span>
                </>
              ) : runs ? (
                <span className="text-xs text-fg-muted">прогонов ещё не было</span>
              ) : (
                <Skeleton className="h-4 w-48" />
              )}
            </Line>
            <Line label="Фоновые работы">
              {runningJobs.length ? (
                runningJobs.map(([name, job]) => (
                  <Badge key={name} variant="machine" title={job.progress ?? undefined}>
                    {trendsJobLabel(name)}
                    {job.progress ? <span className="tabular ml-1 font-mono opacity-80">{job.progress}</span> : null}
                  </Badge>
                ))
              ) : (
                <span className="text-xs text-fg-muted">нет</span>
              )}
            </Line>
            <Line label="Конвейер">
              {pipeline ? (
                <>
                  <Badge variant={pipeline.active ? "machine" : "neutral"}>
                    в работе {formatNumber(pipeline.active)}
                  </Badge>
                  <Badge variant={pipeline.review ? "human" : "neutral"}>
                    на проверке {formatNumber(pipeline.review)}
                  </Badge>
                  <Badge variant={pipeline.failed ? "negative" : "neutral"}>
                    сбоев {formatNumber(pipeline.failed)}
                  </Badge>
                  <span className="text-xs text-fg-muted">
                    цикл {pipeline.enabled ? "включён" : "выключен"} · связь с auto-shorts{" "}
                    {pipeline.linked ? "есть" : "нет"}
                  </span>
                </>
              ) : trends && !trends.reachable ? (
                <Badge variant="negative">{trends.error ?? "Kino-trends не отвечает"}</Badge>
              ) : (
                <Skeleton className="h-4 w-56" />
              )}
              <Workflow className="ml-auto h-3.5 w-3.5 shrink-0 text-fg-muted" aria-hidden="true" />
            </Line>
          </>
        )}
      </SectionCard>
    </div>
  );
}
