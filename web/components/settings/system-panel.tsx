"use client";

import { useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CircleX, Cpu, Database, HardDrive, MemoryStick, RefreshCw, Server } from "lucide-react";

import { SectionError } from "@/components/common/section-error";
import { Badge, Button, DataTable, type DataTableColumn, SectionCard, SkeletonStatGrid } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatAgo, formatDateTime, formatGb, formatMb, formatNumber, formatTime } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { queryKeys } from "@/lib/queries/keys";
import { type ReadyResponse, type SystemStatus, useReady, useSystemStatus } from "@/lib/queries/system";
import { deferReasonLabel, RESOURCE_CLASS_LABELS, stageLabel } from "@/lib/status-meta";
import { deferReasonOf, isWorkerAlive, type Worker } from "@/lib/worker-state";

const READY_LABELS: Record<keyof ReadyResponse["checks"], string> = {
  db: "База данных",
  migrations: "Миграции",
  media_writable: "Каталог медиа доступен на запись",
  disk: "Свободное место",
};

function Counter({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-fg-muted">{label}</span>
      <span className={cn("tabular font-mono text-lg font-semibold", tone)}>{formatNumber(value)}</span>
    </div>
  );
}

function ReadyCard({
  ready,
  isError,
  error,
  onRetry,
}: {
  ready?: ReadyResponse;
  isError: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  return (
    <SectionCard
      title="Готовность"
      actions={
        ready ? (
          ready.status === "ok" ? (
            <Badge variant="positive">
              <CircleCheck className="h-3 w-3" aria-hidden="true" />
              готов
            </Badge>
          ) : (
            <Badge variant="negative">
              <CircleX className="h-3 w-3" aria-hidden="true" />
              не готов
            </Badge>
          )
        ) : null
      }
    >
      {isError ? (
        <SectionError error={error} onRetry={onRetry} title="Нет ответа /health/ready" />
      ) : ready ? (
        <ul className="flex flex-col gap-1.5">
          {(Object.keys(READY_LABELS) as Array<keyof ReadyResponse["checks"]>).map((key) => {
            const check = ready.checks[key];
            return (
              <li key={key} className="flex items-start gap-2 text-sm">
                {check.ok ? (
                  <CircleCheck className="mt-0.5 h-4 w-4 shrink-0 text-positive" aria-hidden="true" />
                ) : (
                  <CircleX className="mt-0.5 h-4 w-4 shrink-0 text-negative" aria-hidden="true" />
                )}
                <span className="flex-1">
                  {READY_LABELS[key]}
                  <span className="sr-only">: {check.ok ? "в порядке" : "ошибка"}</span>
                  {check.detail ? <span className="block text-xs text-fg-muted">{check.detail}</span> : null}
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-sm text-fg-muted">Проверка…</p>
      )}
    </SectionCard>
  );
}

function DiskBar({ status }: { status: SystemStatus }) {
  const { free_gb: free, thresholds } = status.disk;
  // Шкала до двойного «мягкого» порога: главное — видеть, как близко жёсткие ступени 12/8/5 ГБ.
  const scale = Math.max(thresholds.soft_gb * 2, free, 1);
  const pos = (value: number) => `${Math.min(100, (value / scale) * 100)}%`;
  const tone = free <= thresholds.hard_gb ? "bg-negative" : free <= thresholds.soft_gb ? "bg-warning" : "bg-positive";
  return (
    <div className="flex flex-col gap-1">
      <div className="relative h-2 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
        <span className={cn("absolute inset-y-0 left-0 rounded-full", tone)} style={{ width: pos(free) }} />
        {[thresholds.emergency_gb, thresholds.hard_gb, thresholds.soft_gb].map((value, index) => (
          <span key={index} className="absolute inset-y-0 w-px bg-fg/60" style={{ left: pos(value) }} />
        ))}
      </div>
      <p className="tabular font-mono text-2xs text-fg-muted">
        пороги: мягкий {formatGb(thresholds.soft_gb)} · жёсткий {formatGb(thresholds.hard_gb)} · аварийный{" "}
        {formatGb(thresholds.emergency_gb)}
      </p>
    </div>
  );
}

function WorkerState({ worker, now }: { worker: Worker; now: number }) {
  const alive = isWorkerAlive(worker, now);
  if (!alive)
    return (
      <Badge variant="negative">
        <CircleX className="h-3 w-3" aria-hidden="true" />
        нет связи
      </Badge>
    );
  const reason = deferReasonOf(worker.state);
  if (reason) return <Badge variant="warning">отложено: {deferReasonLabel(reason).toLowerCase()}</Badge>;
  if (worker.state === "busy") return <Badge variant="machine">работает</Badge>;
  if (worker.state === "stopping") return <Badge variant="neutral">останавливается</Badge>;
  return <Badge variant="positive">свободен</Badge>;
}

/** Настройки → Система (§7.3): готовность, воркеры, тяжёлый слот, очередь, диск, память, PSI, версии. */
export function SystemPanel() {
  const status = useSystemStatus({ slowWhenLive: 15_000 });
  const ready = useReady();
  const queryClient = useQueryClient();
  const now = useNow(5_000);
  const data = status.data;

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.systemStatus });
    void queryClient.invalidateQueries({ queryKey: queryKeys.ready });
  };

  const workerColumns: Array<DataTableColumn<Worker>> = [
    { key: "id", header: "Воркер", render: (w) => <span className="font-mono text-xs">{w.id}</span> },
    {
      key: "class",
      header: "Очередь",
      render: (w) => (
        <Badge variant={w.resource_class === "heavy" ? "machine" : "neutral"}>
          {RESOURCE_CLASS_LABELS[w.resource_class]}
        </Badge>
      ),
    },
    { key: "state", header: "Состояние", render: (w) => <WorkerState worker={w} now={now} /> },
    {
      key: "task",
      header: "Задача",
      render: (w) => <span className="font-mono text-xs">{w.current_task_id ? `#${w.current_task_id}` : "—"}</span>,
    },
    {
      key: "heartbeat",
      header: "Heartbeat",
      render: (w) => (
        <span className="text-fg-muted" title={formatDateTime(w.heartbeat_at)}>
          {formatAgo(w.heartbeat_at, now)}
        </span>
      ),
    },
    {
      key: "started",
      header: "Запущен",
      render: (w) => <span className="text-fg-muted">{formatDateTime(w.started_at, { short: true })}</span>,
    },
    {
      key: "version",
      header: "Версия",
      render: (w) => <span className="font-mono text-xs text-fg-muted">{w.version ?? "—"}</span>,
    },
  ];

  return (
    <div className="flex flex-col gap-4" data-testid="system-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">Состояние системы</h2>
          <p className="text-xs text-fg-muted">
            {status.dataUpdatedAt ? `Обновлено ${formatTime(status.dataUpdatedAt)}` : "Загрузка…"} · тяжёлый слот всегда
            один
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh}>
          <RefreshCw className={cn("h-3.5 w-3.5", status.isFetching && "animate-spin")} aria-hidden="true" />
          Обновить
        </Button>
      </div>

      {status.isError ? (
        <SectionError error={status.error} onRetry={() => void status.refetch()} title="Нет ответа /system/status" />
      ) : null}

      <div className="grid gap-4 lg:grid-cols-3">
        <ReadyCard
          ready={ready.data}
          isError={ready.isError}
          error={ready.error}
          onRetry={() => void ready.refetch()}
        />

        <SectionCard title="Тяжёлый слот" actions={<Cpu className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}>
          {data ? (
            data.heavy_slot.task_id ? (
              <div className="flex flex-col gap-2">
                <p className="text-sm">
                  <span className="font-mono">#{data.heavy_slot.task_id}</span> · {stageLabel(data.heavy_slot.stage)}
                </p>
                <div className="relative h-2 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
                  <span
                    className="absolute inset-y-0 left-0 rounded-full bg-machine"
                    style={{ width: `${Math.min(100, data.heavy_slot.pct ?? 0)}%` }}
                  />
                </div>
                <p className="tabular font-mono text-xs text-fg-muted">
                  {formatNumber(data.heavy_slot.pct, 0)}% · {data.heavy_slot.owner ?? "—"}
                </p>
              </div>
            ) : (
              <p className="text-sm text-fg-muted">Свободен</p>
            )
          ) : (
            <SkeletonStatGrid count={1} className="grid-cols-1 sm:grid-cols-1" />
          )}
        </SectionCard>

        <SectionCard
          title="Очередь задач"
          actions={<Server className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}
        >
          {data ? (
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-2">
                <p className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Тяжёлая</p>
                <Counter label="в очереди" value={data.queue.heavy.queued} />
                <Counter
                  label="выполняется"
                  value={data.queue.heavy.running}
                  tone={data.queue.heavy.running ? "text-machine" : undefined}
                />
                <Counter
                  label="отложено"
                  value={data.queue.heavy.deferred}
                  tone={data.queue.heavy.deferred ? "text-warning" : undefined}
                />
              </div>
              <div className="flex flex-col gap-2">
                <p className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Лёгкая</p>
                <Counter label="в очереди" value={data.queue.light.queued} />
                <Counter
                  label="выполняется"
                  value={data.queue.light.running}
                  tone={data.queue.light.running ? "text-machine" : undefined}
                />
                <Counter
                  label="отложено"
                  value={data.queue.light.deferred}
                  tone={data.queue.light.deferred ? "text-warning" : undefined}
                />
              </div>
            </div>
          ) : (
            <SkeletonStatGrid count={2} className="grid-cols-2 sm:grid-cols-2" />
          )}
        </SectionCard>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <SectionCard title="Диск" actions={<HardDrive className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}>
          {data ? (
            <>
              <p className="tabular font-mono text-2xl font-semibold">{formatGb(data.disk.free_gb)}</p>
              <DiskBar status={data} />
            </>
          ) : (
            <SkeletonStatGrid count={1} className="grid-cols-1 sm:grid-cols-1" />
          )}
        </SectionCard>
        <SectionCard
          title="Память и нагрузка"
          actions={<MemoryStick className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}
        >
          {data ? (
            <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
              <dt className="text-fg-muted">MemAvailable</dt>
              <dd className="tabular text-right font-mono">{formatMb(data.mem_available_mb)}</dd>
              <dt className="text-fg-muted">PSI cpu10</dt>
              <dd className="tabular text-right font-mono">{formatNumber(data.psi.cpu10, 2)}</dd>
              <dt className="text-fg-muted">PSI mem10</dt>
              <dd className="tabular text-right font-mono">{formatNumber(data.psi.mem10, 2)}</dd>
              <dt className="text-fg-muted">load1</dt>
              <dd className="tabular text-right font-mono">{formatNumber(data.load1, 2)}</dd>
            </dl>
          ) : (
            <SkeletonStatGrid count={2} className="grid-cols-2 sm:grid-cols-2" />
          )}
        </SectionCard>
        <SectionCard title="Версии" actions={<Database className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}>
          {data ? (
            <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
              <dt className="text-fg-muted">Приложение</dt>
              <dd className="text-right font-mono">{data.versions.app}</dd>
              <dt className="text-fg-muted">Схема БД</dt>
              <dd className="text-right font-mono">{data.versions.db_schema ?? "—"}</dd>
            </dl>
          ) : (
            <SkeletonStatGrid count={1} className="grid-cols-1 sm:grid-cols-1" />
          )}
        </SectionCard>
      </div>

      <section className="flex flex-col gap-2" aria-labelledby="workers-title">
        <h3 id="workers-title" className="text-sm font-semibold">
          Воркеры
        </h3>
        <DataTable
          caption="Воркеры"
          columns={workerColumns}
          rows={data?.workers ?? []}
          rowKey={(w) => w.id}
          isLoading={status.isPending}
          emptyTitle="Воркеры не зарегистрированы"
          emptyDescription="Запустите autoshorts-worker --class light и --class heavy."
          minWidth={820}
        />
      </section>
    </div>
  );
}
