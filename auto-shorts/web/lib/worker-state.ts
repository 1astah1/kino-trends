/**
 * Индикатор воркера в шапке (§7.3): idle / рендер `#task · стадия · 42%` / отложено: причина / down.
 * Чистая функция от `GET /system/status`, без React: легко проверять.
 */
import type { Schemas } from "@/lib/api/client";
import { deferReasonLabel, stageLabel } from "@/lib/status-meta";

export type SystemStatus = Schemas["SystemStatus"];
export type Worker = Schemas["Worker"];

/** Воркер считается живым, если heartbeat свежее этого порога. */
export const WORKER_STALE_SEC = 90;

export type WorkerIndicatorKind = "idle" | "busy" | "deferred" | "down" | "unknown";

export interface WorkerIndicatorState {
  kind: WorkerIndicatorKind;
  /** Короткая подпись в шапке. */
  label: string;
  /** Подробность (для title и поповера). */
  detail: string;
  pct: number | null;
  aliveWorkers: number;
}

export function isWorkerAlive(worker: Worker, now: number = Date.now()): boolean {
  const heartbeat = Date.parse(worker.heartbeat_at);
  if (Number.isNaN(heartbeat)) return false;
  return now - heartbeat <= WORKER_STALE_SEC * 1000 && worker.state !== "stopping";
}

export function deferReasonOf(state: string): string | null {
  return state.startsWith("deferred:") ? state.slice("deferred:".length) || null : null;
}

export function deriveWorkerState(status: SystemStatus | undefined, now: number = Date.now()): WorkerIndicatorState {
  if (!status) {
    return { kind: "unknown", label: "воркер: …", detail: "Статус ещё не получен", pct: null, aliveWorkers: 0 };
  }
  const alive = status.workers.filter((worker) => isWorkerAlive(worker, now));
  const heavyAlive = alive.filter((worker) => worker.resource_class === "heavy");
  const slot = status.heavy_slot;

  if (alive.length === 0) {
    return {
      kind: "down",
      label: "воркер: нет связи",
      detail: status.workers.length
        ? `Нет свежего heartbeat дольше ${WORKER_STALE_SEC} с`
        : "Ни один воркер не зарегистрирован",
      pct: null,
      aliveWorkers: 0,
    };
  }

  if (slot.task_id !== null && slot.task_id !== undefined) {
    const pct = slot.pct === null || slot.pct === undefined ? null : Math.max(0, Math.min(100, Math.round(slot.pct)));
    const parts = [`#${slot.task_id}`, stageLabel(slot.stage)];
    if (pct !== null) parts.push(`${pct}%`);
    return {
      kind: "busy",
      label: parts.join(" · "),
      detail: `Тяжёлый слот занят${slot.owner ? ` (${slot.owner})` : ""}`,
      pct,
      aliveWorkers: alive.length,
    };
  }

  const deferred = alive.map((worker) => deferReasonOf(worker.state)).find((reason) => reason !== null);
  if (deferred) {
    return {
      kind: "deferred",
      label: `отложено: ${deferReasonLabel(deferred).toLowerCase()}`,
      detail: "Задачи ждут допуска по ресурсам",
      pct: null,
      aliveWorkers: alive.length,
    };
  }

  return {
    kind: "idle",
    label: heavyAlive.length ? "простаивает" : "только лёгкий",
    detail: heavyAlive.length ? "Тяжёлый слот свободен" : "Тяжёлый воркер не запущен",
    pct: null,
    aliveWorkers: alive.length,
  };
}
