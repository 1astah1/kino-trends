"use client";

import { Cpu, HardDrive, MemoryStick, Gauge } from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/cn";
import { formatGb, formatMb, formatNumber } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { routeHref } from "@/lib/nav";
import { useSystemStatus } from "@/lib/queries/system";
import { deriveWorkerState, type WorkerIndicatorKind } from "@/lib/worker-state";

import { HeaderPopover, headerButtonClass } from "./popover";

const KIND_STYLE: Record<WorkerIndicatorKind, { dot: string; text: string }> = {
  idle: { dot: "bg-positive", text: "text-fg-muted" },
  busy: { dot: "bg-machine tally-pulse", text: "text-machine" },
  deferred: { dot: "bg-warning", text: "text-warning" },
  down: { dot: "bg-negative", text: "text-negative" },
  unknown: { dot: "bg-fg-muted/50", text: "text-fg-muted" },
};

function Row({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-0.5">
      <span className="text-fg-muted">{label}</span>
      <span className={cn("tabular font-mono text-xs", tone)}>{value}</span>
    </div>
  );
}

/** Индикатор воркера (§7.3): idle / рендер `#task · стадия · 42%` / отложено: причина / down. */
export function WorkerIndicator() {
  const status = useSystemStatus();
  const now = useNow();
  const state = deriveWorkerState(status.data, now);
  const style = status.isError ? KIND_STYLE.unknown : KIND_STYLE[state.kind];
  const label = status.isError ? "воркер: ?" : state.label;
  const data = status.data;

  return (
    <HeaderPopover
      label="Состояние воркеров"
      trigger={
        <button
          type="button"
          className={cn(headerButtonClass, "max-w-[240px]")}
          data-testid="worker-indicator"
          data-state={state.kind}
        >
          <span className={cn("h-2 w-2 shrink-0 rounded-full", style.dot)} aria-hidden="true" />
          <Cpu className="h-3.5 w-3.5 shrink-0 md:hidden" aria-hidden="true" />
          <span className={cn("hidden truncate font-mono md:inline", style.text)}>{label}</span>
          <span className="sr-only md:hidden">{label}</span>
        </button>
      }
    >
      <div className="flex flex-col gap-2.5">
        <div>
          <p className="text-sm font-medium">{label}</p>
          <p className="text-xs text-fg-muted">{status.isError ? "Нет ответа /system/status" : state.detail}</p>
        </div>
        {data ? (
          <>
            <div className="border-t border-border pt-2">
              <p className="mb-1 font-mono text-2xs tracking-wider text-fg-muted uppercase">Тяжёлая очередь</p>
              <Row label="В очереди" value={formatNumber(data.queue.heavy.queued)} />
              <Row label="Выполняется" value={formatNumber(data.queue.heavy.running)} />
              <Row
                label="Отложено"
                value={formatNumber(data.queue.heavy.deferred)}
                tone={data.queue.heavy.deferred ? "text-warning" : undefined}
              />
            </div>
            <div className="border-t border-border pt-2">
              <p className="mb-1 font-mono text-2xs tracking-wider text-fg-muted uppercase">Ресурсы</p>
              <div className="flex items-center justify-between gap-3 py-0.5">
                <span className="flex items-center gap-1.5 text-fg-muted">
                  <HardDrive className="h-3.5 w-3.5" aria-hidden="true" />
                  Диск свободно
                </span>
                <span
                  className={cn(
                    "tabular font-mono text-xs",
                    data.disk.free_gb <= data.disk.thresholds.hard_gb
                      ? "text-negative"
                      : data.disk.free_gb <= data.disk.thresholds.soft_gb
                        ? "text-warning"
                        : undefined,
                  )}
                >
                  {formatGb(data.disk.free_gb)}
                </span>
              </div>
              <div className="flex items-center justify-between gap-3 py-0.5">
                <span className="flex items-center gap-1.5 text-fg-muted">
                  <MemoryStick className="h-3.5 w-3.5" aria-hidden="true" />
                  Память доступно
                </span>
                <span className="tabular font-mono text-xs">{formatMb(data.mem_available_mb)}</span>
              </div>
              <div className="flex items-center justify-between gap-3 py-0.5">
                <span className="flex items-center gap-1.5 text-fg-muted">
                  <Gauge className="h-3.5 w-3.5" aria-hidden="true" />
                  PSI cpu10 / mem10
                </span>
                <span className="tabular font-mono text-xs">
                  {formatNumber(data.psi.cpu10, 1)} / {formatNumber(data.psi.mem10, 1)}
                </span>
              </div>
              <Row label="load1" value={formatNumber(data.load1, 2)} />
            </div>
          </>
        ) : null}
        <Link href={routeHref("/settings", { tab: "system" })} className="text-xs text-accent hover:underline">
          Подробнее: Настройки → Система
        </Link>
      </div>
    </HeaderPopover>
  );
}
